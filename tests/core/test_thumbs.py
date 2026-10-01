"""``gallery.core.thumbs`` 的测试。

核心不变量只有一句：**缓存可以随时删掉**。所以这里既要验证「命中缓存时不再解码」，
也要验证「缓存坏了、写不进去、被清空」时软件依然正确。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

import gallery.core.thumbs as thumbs
from gallery.core.errors import ImageDecodeError
from gallery.core.photos import PhotoRef
from gallery.core.thumbs import ThumbnailCache


def entries(cache: ThumbnailCache) -> list[Path]:
    if not cache.directory.is_dir():
        return []
    return sorted(path for path in cache.directory.iterdir() if path.suffix in {".jpg", ".png"})


@pytest.fixture
def cache(tmp_path: Path) -> ThumbnailCache:
    return ThumbnailCache(tmp_path / "cache" / "thumbs", max_edge=64)


def test_generates_and_stores(cache: ThumbnailCache, make_photo: Callable[..., Path]) -> None:
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))

    raster = cache.get(ref)

    assert raster.size == (64, 32)
    assert len(entries(cache)) == 1
    assert entries(cache)[0].suffix == ".jpg"


def test_second_read_hits_cache(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))
    cache.get(ref)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("命中缓存时不应再解码源图")

    monkeypatch.setattr(thumbs, "decode_rgba", boom)

    assert cache.get(ref).size == (64, 32)


def test_changed_mtime_invalidates(cache: ThumbnailCache, make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(400, 200))
    cache.get(PhotoRef.from_path(path))

    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    cache.get(PhotoRef.from_path(path))

    assert len(entries(cache)) == 2


def test_spec_version_invalidates(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(400, 200))
    ref = PhotoRef.from_path(path)
    directory = tmp_path / "cache" / "thumbs"

    ThumbnailCache(directory, max_edge=64, spec_version=1).get(ref)
    ThumbnailCache(directory, max_edge=64, spec_version=2).get(ref)

    assert len(list(directory.iterdir())) == 2


def test_transparent_png_is_stored_losslessly(cache: ThumbnailCache, tmp_path: Path) -> None:
    from PIL import Image

    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    image.save(path)
    image.close()

    raster = cache.get(PhotoRef.from_path(path))

    assert raster.has_alpha is True
    assert entries(cache)[0].suffix == ".png"
    # 透明像素必须保住：转成 JPEG 的话这里会变成黑色。
    assert raster.rgba[:4] == bytes((0, 0, 0, 0))


def test_corrupt_entry_is_regenerated(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))
    cache.get(ref)
    entry = entries(cache)[0]
    entry.write_bytes(b"not an image")

    raster = cache.get(ref)

    assert raster.size == (64, 32)
    assert entry.read_bytes()[:2] == b"\xff\xd8"  # JPEG 魔数，说明确实重写了


def test_missing_source_propagates(cache: ThumbnailCache, tmp_path: Path) -> None:
    ref = PhotoRef(path=tmp_path / "不存在.jpg", size=0, mtime_ns=0)

    with pytest.raises(ImageDecodeError):
        cache.get(ref)


def test_cache_write_failure_is_not_fatal(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """磁盘满、目录只读时，看图这件事不能跟着失败。"""
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(thumbs, "write_bytes_atomic", boom)

    assert cache.get(ref).size == (64, 32)
    assert entries(cache) == []


def test_clear_removes_only_known_entries(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    cache.get(PhotoRef.from_path(make_photo("a.jpg")))
    cache.get(PhotoRef.from_path(make_photo("b.jpg")))
    cache.directory.mkdir(parents=True, exist_ok=True)
    foreign = cache.directory / "用户放的文件.txt"
    foreign.write_text("别删我", encoding="utf-8")

    removed = cache.clear()

    assert removed == 2
    assert [path.name for path in cache.directory.iterdir()] == [foreign.name]


def test_enforce_limit_evicts_oldest(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    for index in range(3):
        path = make_photo(f"{index}.jpg", size=(400, 200))
        cache.get(PhotoRef.from_path(path))
    # 让三个条目的修改时间明确拉开先后。
    for offset, entry in enumerate(entries(cache)):
        os.utime(entry, (1_000 + offset, 1_000 + offset))
    limited = ThumbnailCache(cache.directory, max_edge=64, max_bytes=1)

    removed = limited.enforce_limit()

    assert removed == 3
    assert limited.total_bytes() == 0


def test_zero_limit_disables_eviction(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    cache.get(PhotoRef.from_path(make_photo("a.jpg")))
    unlimited = ThumbnailCache(cache.directory, max_edge=64, max_bytes=0)

    assert unlimited.enforce_limit() == 0
    assert len(entries(cache)) == 1


def test_cache_can_be_deleted_and_rebuilt(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    """硬性约定：删掉缓存目录后重新扫描必须得到等价结果。"""
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))
    first = cache.get(ref)

    for entry in entries(cache):
        entry.unlink()
    cache.directory.rmdir()

    second = cache.get(ref)

    assert (first.width, first.height, first.rgba) == (second.width, second.height, second.rgba)
