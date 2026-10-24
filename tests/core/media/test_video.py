"""``gallery.core.media.video`` 与视频那条解码路的测试（ADR 0090）。

视频素材是现场拼的 AVI/MJPEG（见 ``tests/conftest.py`` 的 ``make_video``）：仓库里不放
二进制，也不需要 ffmpeg。**要验的是三件事**：第 0 秒那一帧真的被取出来、读不出来时给的是
一句能行动的话、缩略图缓存对视频同样有效。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.errors import ImageDecodeError
from gallery.core.library.photos import PhotoRef
from gallery.core.media import thumbs as thumbs_module
from gallery.core.media.decode import RasterImage, decode_rgba
from gallery.core.media.thumbs import ThumbnailCache
from gallery.core.media.video import VIDEO_SUPPORT

#: 取帧要真的调 AVFoundation。没有它（非 macOS）就跳过，最后一条单独盯着这个能力。
needs_video = pytest.mark.skipif(not VIDEO_SUPPORT, reason="这台机器没有可用的 AVFoundation")


def _center(raster: RasterImage) -> tuple[int, int, int]:
    index = ((raster.height // 2) * raster.width + raster.width // 2) * 4
    return raster.rgba[index], raster.rgba[index + 1], raster.rgba[index + 2]


@needs_video
def test_the_first_frame_is_the_first_frame(
    make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """三帧红 / 绿 / 蓝：取回来必须是**第一帧**（红），不是随便哪一帧。"""
    path = make_video(
        frames=3, colors=[(220, 30, 30), (30, 220, 30), (30, 30, 220)], directory=tmp_path
    )

    raster = decode_rgba(path, max_edge=32)

    assert (raster.width, raster.height) == (32, 24)
    red, green, blue = _center(raster)
    assert red > 150 and green < 90 and blue < 90, f"取到的不是第一帧：{(red, green, blue)}"
    assert raster.has_alpha is False


@needs_video
def test_max_edge_scales_during_decode(make_video: Callable[..., Path], tmp_path: Path) -> None:
    path = make_video(frames=1, size=(160, 120), directory=tmp_path)

    raster = decode_rgba(path, max_edge=40)

    assert (raster.width, raster.height) == (40, 30)


@needs_video
def test_a_broken_video_says_what_is_wrong(tmp_path: Path) -> None:
    """坏文件要像照片那条路一样给一句中文，而不是让 PyObjC 的异常漏出去。"""
    path = tmp_path / "坏掉的.mp4"
    path.write_bytes(b"not a video")

    with pytest.raises(ImageDecodeError) as caught:
        decode_rgba(path)

    assert "第一帧" in str(caught.value)


@needs_video
def test_a_missing_video_is_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(ImageDecodeError):
        decode_rgba(tmp_path / "不存在.mp4")


@needs_video
def test_the_thumbnail_cache_holds_video_frames(
    make_video: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二次取同一格必须命中缓存：与照片同一条淘汰与命中规则。"""
    path = make_video(frames=1, directory=tmp_path)
    cache = ThumbnailCache(tmp_path / "thumbs", max_edge=32)
    ref = PhotoRef.from_path(path)

    first = cache.get(ref)
    assert first.width == 32

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("命中缓存时不该再解码")

    monkeypatch.setattr(thumbs_module, "decode_rgba", refuse)
    second = cache.get(ref)

    assert (second.width, second.height) == (first.width, first.height)
    assert second.rgba == first.rgba


def test_video_support_is_available_on_macos() -> None:
    """动态加载是这条路唯一脆弱的一环；它挂了要在测试里当场看见，而不是只剩占位图。"""
    if sys.platform == "darwin":
        assert VIDEO_SUPPORT is True
