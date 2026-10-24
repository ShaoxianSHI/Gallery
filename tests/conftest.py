"""测试共享工具。

测试数据一律现场构造：仓库里不放真实照片，也不放任何大尺寸二进制；
需要真实文件时用 Pillow 当场生成最小的合法图片，视频用 ``make_video`` 现场拼一个
几百字节的 AVI（AVFoundation 与 Qt 都能解它）。
"""

from __future__ import annotations

import io
import itertools
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import ExifTags, Image

from gallery.core import paths

#: EXIF 中 Orientation 标签的编号。
EXIF_ORIENTATION_TAG = 0x0112


#: 测试用的假废纸篓目录名。以点开头，扫描器因此不会把它当成一个相册（真废纸篓同理）。
FAKE_TRASH_NAME = ".废纸篓"


@pytest.fixture(autouse=True)
def fake_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把「废纸篓」指向临时目录，返回那个目录。

    故意做成 autouse：漏了这个 fixture 就会把临时照片搬进用户真实的 ``~/.Trash``，
    而那个隐患是静默的。
    """
    from gallery.core.metadata import trash

    directory = tmp_path / FAKE_TRASH_NAME
    monkeypatch.setattr(trash, "trash_dir_for", lambda _path: directory)
    return directory


#: 没被 :func:`isolated_app_data` 换掉的那个 :func:`paths.app_data_base`。
_REAL_APP_DATA_BASE = paths.app_data_base


@pytest.fixture(autouse=True)
def isolated_app_data(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**把应用数据目录挪进临时目录**，任何测试都不许碰用户真实的那一个。

    兜住 ``state.json`` 与 ``<app cache>/thumbs/<库摘要>/``（ADR 0038 决策 1）；后者更要紧
    —— 摘要按**库路径**算，而每个用例一个 ``tmp_path``，不隔离的话两轮测试就在用户真实
    缓存目录里堆出几千个既不会淘汰也没有上限的摘要目录。

    钉在 ``app_data_base`` 这一个来源上而不是逐个调用点传参：``Library.open`` 的调用点
    有三十多处，逐个传参等于三十多次「记得写」，漏掉任何一处都是同样的泄漏。
    """
    base = tmp_path_factory.mktemp("应用数据")
    monkeypatch.setattr(paths, "app_data_base", lambda: base)


@pytest.fixture
def real_app_data_base() -> Callable[[], Path]:
    """真正的（没被隔离的）``app_data_base``，供「各平台默认落在哪」那条断言用。"""
    return _REAL_APP_DATA_BASE


@pytest.fixture
def make_photo(tmp_path: Path) -> Callable[..., Path]:
    """返回一个「造照片」的工厂：``make_photo("a.jpg", size=(40, 20))``。

    ``exif`` / ``exif_ifd`` 往文件里写真 EXIF（主 IFD 与 Exif 子 IFD，键用标签编号）；
    分组与排序的测试需要它们 —— 那种照片得带拍摄时间才分得开。
    """
    counter = itertools.count()

    def _make(
        name: str = "photo.jpg",
        *,
        size: tuple[int, int] = (64, 48),
        color: tuple[int, ...] = (200, 120, 40),
        mode: str = "RGB",
        orientation: int | None = None,
        directory: Path | None = None,
        data: bytes | None = None,
        exif: dict[int, object] | None = None,
        exif_ifd: dict[int, object] | None = None,
    ) -> Path:
        target_dir = tmp_path if directory is None else directory
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / name
        if data is not None:
            path.write_bytes(data)
            return path
        if path.exists():  # 同名重复调用时换一个名字，避免测试互相覆盖
            path = target_dir / f"{path.stem}-{next(counter)}{path.suffix}"
        image = Image.new(mode, size, color)
        try:
            if orientation is None and not exif and not exif_ifd:
                image.save(path)
            else:
                tags = image.getexif()
                if orientation is not None:
                    tags[EXIF_ORIENTATION_TAG] = orientation
                for tag_id, value in (exif or {}).items():
                    tags[tag_id] = value
                # DateTimeOriginal、ISO 这些住在 Exif 子 IFD 里，编号与主 IFD 不同。
                for tag_id, value in (exif_ifd or {}).items():
                    tags.get_ifd(ExifTags.IFD.Exif)[tag_id] = value
                image.save(path, exif=tags)
        finally:
            image.close()
        return path

    return _make


#: 夹具视频的帧率。25 是 AVI 的常见默认值；3 帧就是 120 ms，够一次「播完了」的断言。
VIDEO_FPS = 25


def _jpeg_frame(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), color)
    try:
        image.save(buffer, format="JPEG", quality=85)
    finally:
        image.close()
    return buffer.getvalue()


def _riff_chunk(tag: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) % 2 else b""
    return tag + struct.pack("<I", len(payload)) + payload + padding


def _avi_bytes(frames: list[bytes], width: int, height: int, fps: int) -> bytes:
    """若干 JPEG 帧 → AVI/MJPEG。只有 RIFF 必需的那几段，播放器与取帧都用得上。"""
    largest = max(len(frame) for frame in frames)
    avih = struct.pack(
        "<IIIIIIIIII4I",
        1_000_000 // fps,
        0,
        0,
        0x10,  # AVIF_HASINDEX
        len(frames),
        0,
        1,
        largest,
        width,
        height,
        0,
        0,
        0,
        0,
    )
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        b"vids",
        b"MJPG",
        0,
        0,
        0,
        0,
        1,
        fps,
        0,
        len(frames),
        largest,
        0xFFFFFFFF,
        0,
        0,
        0,
        width,
        height,
    )
    strf = struct.pack("<IiiHH4sIiiII", 40, width, height, 1, 24, b"MJPG", largest, 0, 0, 0, 0)
    strl = _riff_chunk(b"LIST", b"strl" + _riff_chunk(b"strh", strh) + _riff_chunk(b"strf", strf))
    hdrl = _riff_chunk(b"LIST", b"hdrl" + _riff_chunk(b"avih", avih) + strl)
    chunks = b"".join(_riff_chunk(b"00dc", frame) for frame in frames)
    movi = _riff_chunk(b"LIST", b"movi" + chunks)
    entries = []
    offset = 4
    for frame in frames:
        entries.append(struct.pack("<4sIII", b"00dc", 0x10, offset, len(frame)))
        offset += 8 + len(frame) + (len(frame) % 2)
    idx1 = _riff_chunk(b"idx1", b"".join(entries))
    body = b"AVI " + hdrl + movi + idx1
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.fixture
def make_video(tmp_path: Path) -> Callable[..., Path]:
    """返回一个「造视频」的工厂：``make_video("clip.avi", frames=3, color=(255, 0, 0))``。

    容器是 AVI/MJPEG（RIFF + 几段 JPEG），几百字节，纯标准库拼出来 —— 仓库里不放视频
    二进制。AVFoundation 与 Qt 的 FFmpeg 后端都认它，所以取帧与播放测试共用同一个造法。
    """

    def _make(
        name: str = "clip.avi",
        *,
        frames: int = 1,
        size: tuple[int, int] = (64, 48),
        color: tuple[int, int, int] = (200, 40, 40),
        colors: list[tuple[int, int, int]] | None = None,
        directory: Path | None = None,
    ) -> Path:
        target_dir = tmp_path if directory is None else directory
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / name
        palette = colors if colors is not None else [color] * frames
        jpegs = [_jpeg_frame(size[0], size[1], item) for item in palette]
        path.write_bytes(_avi_bytes(jpegs, size[0], size[1], VIDEO_FPS))
        return path

    return _make
