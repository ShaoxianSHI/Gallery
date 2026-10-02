"""``gallery.core.media.thumbs`` 的测试。

核心不变量只有一句：**缓存可以随时删掉**。所以这里既要验证「命中缓存时不再解码」，
也要验证「缓存坏了、写不进去、被清空」时软件依然正确。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import gallery.core.media.thumbs as thumbs
from gallery.core.errors import ImageDecodeError
from gallery.core.library.photos import PhotoRef
from gallery.core.media.thumbs import ThumbnailCache


def entries(cache: ThumbnailCache) -> list[Path]:
    if not cache.directory.is_dir():
        return []
    return sorted(path for path in cache.directory.iterdir() if path.suffix in {".jpg", ".png"})


@pytest.fixture
def cache(tmp_path: Path) -> ThumbnailCache:
    return ThumbnailCache(tmp_path / "cache" / "thumbs", max_edge=64)


@pytest.fixture
def counted_scans(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """数一数 ``_entries()``（也就是「真的去列一遍目录」）被调了几次。"""
    calls: list[int] = []
    original = ThumbnailCache._entries

    def counting(self: ThumbnailCache) -> list[tuple[Path, int, float]]:
        calls.append(1)
        return original(self)

    monkeypatch.setattr(ThumbnailCache, "_entries", counting)
    yield calls


def jpeg_sampling_factors(data: bytes) -> list[int]:
    """从 JPEG 的 SOF0 段里读出各颜色分量的采样因子。

    4:2:0 存下来的图是 ``[0x22, 0x11, 0x11]``（第一个分量取 2×2 的块，另两个各 1×1），
    4:4:4 是 ``[0x11, 0x11, 0x11]``。这是「到底有没有用色度抽样」唯一可靠的判据 ——
    比断言常量值有意义，因为它验的是真正落到磁盘上的那串字节。
    """
    index = 2  # 跳过 SOI
    while index < len(data) - 1:
        if data[index] != 0xFF:
            raise AssertionError("不是预期的 JPEG 段结构")
        marker = data[index + 1]
        index += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[index : index + 2], "big")
        if marker == 0xC0:  # SOF0
            count = data[index + 7]
            return [data[index + 9 + 3 * component] for component in range(count)]
        index += length
    raise AssertionError("JPEG 里找不到 SOF0 段")


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


def test_a_palette_png_keeps_its_transparency_across_cache_hits(
    cache: ThumbnailCache, tmp_path: Path
) -> None:
    """调色板 PNG 的透明**不能只在第一次看的时候对**。

    判据过去只看 ``getbands()`` 里有没有 ``A``，于是这种图被当成不透明：第一次
    ``get()`` 返回的确实是透明的（当场 convert 出来的），但它按 JPEG 存了盘，
    于是**第二次之后**缓存命中返回的就是不透明的了 —— 错的是写进磁盘的那一份，
    退出重开也一样。
    """
    from PIL import Image

    path = tmp_path / "调色板.png"
    image = Image.new("P", (64, 32))
    image.putpalette([0, 0, 0] * 256)
    image.save(path, format="PNG", transparency=0)
    image.close()
    ref = PhotoRef.from_path(path)

    first = cache.get(ref)
    second = cache.get(ref)  # 命中缓存

    assert first.has_alpha is True
    assert entries(cache)[0].suffix == ".png", "带透明就该存 PNG，否则透明像素变成黑色"
    assert first.rgba[:4] == second.rgba[:4] == bytes((0, 0, 0, 0))


def test_a_rename_does_not_make_the_cache_rescan_itself(
    cache: ThumbnailCache, counted_scans: list[int], make_photo: Callable[..., Path]
) -> None:
    """改名只是把条目挪个位置，**字节数没变**：记账不该因此作废。

    作废的代价是下一次写入时 ``_over_limit()`` 答不出、于是付一次全目录扫描
    （ADR 0015 正是把那一次扫描从热路径上拿掉的）。只有「目标已存在、旧的被丢掉」
    那一步才真的少了字节。
    """
    path = make_photo("一张.jpg", size=(120, 90))
    ref = PhotoRef.from_path(path)
    cache.get(ref)
    cache.get(ref)
    scans_before = len(counted_scans)

    # 真改一次名（大小与修改时间随之而来），再告诉缓存挪一下。
    renamed = path.with_name("两张.jpg")
    path.rename(renamed)

    assert cache.relocate(ref, PhotoRef.from_path(renamed)) is True

    assert cache._total_bytes is not None, "纯改名不该作废记账"
    # 挪完之后再取一张：不该因此多付一次全目录扫描。
    cache.get(PhotoRef.from_path(make_photo("另一张.jpg", size=(120, 90))))
    assert len(counted_scans) == scans_before


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


# ---- 总量记账（ADR 0015）----------------------------------------------------


def test_writes_do_not_rescan_the_directory(
    tmp_path: Path, make_photo: Callable[..., Path], counted_scans: list[int]
) -> None:
    """上限宽松时，写再多张也只该数一次目录。

    这条钉住的是「记账替代数目录」：旧实现每 64 张就 ``iterdir()`` 一次，代价随
    缓存线性增长，在 2 GiB 上限下每张要白付近 10 ms。
    """
    refs = [PhotoRef.from_path(make_photo(f"{index}.jpg")) for index in range(12)]
    generous = ThumbnailCache(tmp_path / "thumbs", max_edge=64, max_bytes=64 * 1024 * 1024)

    for ref in refs:
        generous.get(ref)

    assert len(entries(generous)) == 12
    assert len(counted_scans) == 1  # 只有播种那一次


def test_unlimited_cache_never_scans(
    tmp_path: Path, make_photo: Callable[..., Path], counted_scans: list[int]
) -> None:
    """上限为 0 = 不限制，那就一次目录都不该数。"""
    unlimited = ThumbnailCache(tmp_path / "thumbs", max_edge=64, max_bytes=0)

    for index in range(5):
        unlimited.get(PhotoRef.from_path(make_photo(f"{index}.jpg")))

    assert len(counted_scans) == 0


def test_accounting_reseeds_on_a_fresh_instance(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**重启后记账归零，上限必须照样生效** —— 这是播种那一次扫描的理由。

    记账只活在内存里；新进程拿到的第一个对象是空的。它必须自己去数一遍目录，
    否则会以为缓存是空的，2 GiB 上限形同虚设、缓存无限涨。
    """
    refs = [PhotoRef.from_path(make_photo(f"{index}.jpg")) for index in range(8)]
    directory = tmp_path / "thumbs"
    # 上一个进程写了 4 张就退出了。
    warm = ThumbnailCache(directory, max_edge=64, max_bytes=64 * 1024 * 1024)
    for ref in refs[:4]:
        warm.get(ref)
    one_entry = entries(warm)[0].stat().st_size

    # 模拟重启：全新实例，记账是空的，上限只够留两条。
    reopened = ThumbnailCache(directory, max_edge=64, max_bytes=one_entry * 2)
    assert reopened._total_bytes is None

    # 接着写另外 4 张（全新照片，必然未命中，才会真的往目录里落盘）。
    for ref in refs[4:]:
        reopened.get(ref)

    assert reopened._total_bytes is not None  # 已经播种
    assert reopened.total_bytes() <= one_entry * 2
    assert len(entries(reopened)) < 8  # 确实淘汰过，而不是放任它涨


def test_accounting_stays_in_step_with_the_directory(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """记账不能越走越偏：它必须和「真的数一遍」始终一致。"""
    refs = [PhotoRef.from_path(make_photo(f"{index}.jpg")) for index in range(8)]
    cache = ThumbnailCache(tmp_path / "thumbs", max_edge=64, max_bytes=4 * 1024)
    for ref in refs:
        cache.get(ref)

    tracked = cache._total_bytes
    assert tracked is not None
    assert cache.total_bytes() == tracked


def test_clear_resets_the_accounting(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    for index in range(4):
        cache.get(PhotoRef.from_path(make_photo(f"{index}.jpg")))

    assert cache.clear() == 4
    assert cache._total_bytes == 0


# ---- 编码规格（ADR 0015）----------------------------------------------------


def test_thumbnail_jpeg_uses_chroma_subsampling(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    """缓存里的 JPEG 必须是 4:2:0。

    钉的是「**显式**写出来」这件事：不写的话这个值是由 libjpeg 的默认行为决定的
    （在 q82/q85 上它恰好也是 4:2:0），一旦换版本或换质量就可能悄悄变掉。验的是
    真正落到磁盘上的字节，不是常量值（ADR 0015）。
    """
    cache.get(PhotoRef.from_path(make_photo("a.jpg", size=(400, 200))))

    factors = jpeg_sampling_factors(entries(cache)[0].read_bytes())

    assert factors == [0x22, 0x11, 0x11]
