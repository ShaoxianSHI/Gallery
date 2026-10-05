"""测试共享工具。

测试数据一律现场构造：仓库里不放真实照片，也不放任何大尺寸二进制（见 AGENTS.md）。
需要真实文件时用 Pillow 当场生成最小的合法图片。
"""

from __future__ import annotations

import itertools
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

    **故意是 autouse 的**：这条测试要动的是用户的文件，测试一旦漏了这个 fixture，
    就会把临时照片搬进用户真实的 ``~/.Trash`` —— 那里面的东西用户还要还原，
    而这个隐患是静默的（在受限环境里表现为「测试莫名其妙失败」）。
    统一在这里兜住，每个测试就不必自己记得了（需要断言废纸篓内容的照常显式要它）。
    """
    from gallery.core.metadata import trash

    directory = tmp_path / FAKE_TRASH_NAME
    monkeypatch.setattr(trash, "trash_dir_for", lambda _path: directory)
    return directory


#: 没被 :func:`isolated_app_data` 换掉的那个 :func:`paths.app_data_base`。
#:
#: 只有「测它自己」的用例需要 —— 见下面那个 ``real_app_data_base`` fixture。
_REAL_APP_DATA_BASE = paths.app_data_base


@pytest.fixture(autouse=True)
def isolated_app_data(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**把应用数据目录挪进临时目录**，任何测试都不许碰用户真实的那一个。

    与 :func:`fake_trash` 是同一个形状、同一个理由：真废纸篓与真缓存目录都在用户的
    家里，测试漏掉一次就是往那儿丢东西，而且**丢得看不出来**。

    这里兜住的是两样：``state.json``（最近打开列表、窗口几何）与系统级缩略图缓存
    ``<app cache>/thumbs/<库摘要>/``（ADR 0038 决策 1）。后者更要紧 —— 摘要按**库路径**
    算，而测试每个用例一个 ``tmp_path``，于是**每个真去解码缩略图的用例都会留下一个
    新的摘要目录**，而它们既不会被淘汰也没有上限（ADR 0038 那条已知遗留）。
    实测：不隔离的话跑两轮就在用户的真实缓存目录里留下 2509 个，65 MB。

    **钉在它身上而不是逐个调用点传参**：``app_data_base`` 是 ``state.json`` 与系统缓存
    唯一的共同来源，而 ``Library.open`` 的调用点在测试里有三十多处 —— 逐个传
    ``cache_base`` 是三十多次「记得写」，漏掉任何一处都是同样的泄漏。
    """
    base = tmp_path_factory.mktemp("应用数据")
    monkeypatch.setattr(paths, "app_data_base", lambda: base)


@pytest.fixture
def real_app_data_base() -> Callable[[], Path]:
    """真正的（没被隔离的）``app_data_base``。

    只有一条用例需要它：``test_app_data_base_is_platform_specific`` 测的就是「各平台
    落在各平台该在的地方」——那是关于**真实默认值**的断言，隔离了就没得测。
    """
    return _REAL_APP_DATA_BASE


@pytest.fixture
def make_photo(tmp_path: Path) -> Callable[..., Path]:
    """返回一个「造照片」的工厂：``make_photo("a.jpg", size=(40, 20))``。

    默认在 ``tmp_path`` 下生成，保证每个测试都在自己的临时目录里工作。

    ``exif`` / ``exif_ifd`` 可以往文件里写真的 EXIF（主 IFD 与 Exif 子 IFD，
    键用标签编号）。分组与排序的测试需要它们 —— 那种照片得带拍摄时间才分得开。
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
