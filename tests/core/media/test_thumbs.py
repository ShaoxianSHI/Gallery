"""``gallery.core.media.thumbs`` 的测试。

核心不变量只有一句：**缓存可以随时删掉**。所以这里既要验证「命中缓存时不再解码」，
也要验证「缓存坏了、写不进去、被清空」时软件依然正确。

还有一条容易漏的：**原子写崩在中间留下的 ``.tmp`` 也是缓存**。它过去对记账完全隐形，
于是 2 GiB 上限不是真的上限（见下面「原子写的孤儿」那一节）。
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import gallery.core.atomic as atomic
import gallery.core.media.thumbs as thumbs
from gallery.core.errors import ImageDecodeError
from gallery.core.library.photos import PhotoRef
from gallery.core.media.thumbs import ThumbnailCache


def entries(cache: ThumbnailCache) -> list[Path]:
    if not cache.directory.is_dir():
        return []
    return sorted(path for path in cache.directory.iterdir() if path.suffix in {".jpg", ".png"})


def orphans(cache: ThumbnailCache) -> list[Path]:
    if not cache.directory.is_dir():
        return []
    return sorted(cache.directory.glob("*.tmp"))


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


# ---- 原子写的孤儿（崩在 mkstemp 与 os.replace 之间）------------------------


def _kill_os_replace(_source: object, _target: object) -> None:
    """模拟「崩在 ``os.replace`` 之前」。"""
    raise KeyboardInterrupt


def orphan_after_a_crash(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> Path:
    """真的造一个孤儿出来，而不是照着规律手搓一个文件名。

    抛的是 ``KeyboardInterrupt`` 而不是 ``OSError``，这正是关键：``_write_atomic``
    **只在 ``OSError`` 时**收拾自己的临时文件，而真正的崩溃（SIGKILL、断电、用户强杀）
    是什么都不收拾的 —— 那些文件就是那样留在缓存目录里的，永远没人来收。

    顺带一提，手搓名字的那条路会**把实现的判据抄进测试**：写错了测试跟着一起错，
    于是什么都没验到。这里让 ``mkstemp`` 自己起名。
    """
    before = {path.name for path in orphans(cache)}
    ref = PhotoRef.from_path(make_photo("一张.jpg", size=(400, 200)))
    with monkeypatch.context() as patch:
        patch.setattr(atomic.os, "replace", _kill_os_replace)
        with pytest.raises(KeyboardInterrupt):
            cache.get(ref)
    # 用「前后差集」而不是「目录里只有一个 .tmp」：有些用例会故意先在目录里摆几个
    # 长得很像的东西，那几个同样以 .tmp 收尾，不能把它们算成这次崩出来的。
    left = [path for path in orphans(cache) if path.name not in before]
    assert len(left) == 1, "崩在 os.replace 之前就该留下且只留下一个新临时文件"
    return left[0]


def test_a_crashed_atomic_write_is_counted_in_the_total(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """孤儿必须算进总量 —— 算少了，2 GiB 上限就只是看着像上限。

    ``total_bytes()`` 过去只看 ``.jpg`` / ``.png``，于是这些崩在原子写中间的半截文件
    占了多少磁盘完全无人过问：``_over_limit()`` 不会因为它们响，``enforce_limit()``
    也永远够不着它们，缓存就这么一直涨。
    """
    orphan = orphan_after_a_crash(cache, make_photo, monkeypatch)

    assert cache.total_bytes() == orphan.stat().st_size


def test_a_crashed_atomic_write_is_evicted(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """孤儿跟别的条目一样可淘汰，而且是**最老的那一批先走**。"""
    for index in range(2):
        cache.get(PhotoRef.from_path(make_photo(f"{index}.jpg", size=(400, 200))))
    orphan = orphan_after_a_crash(cache, make_photo, monkeypatch)
    # 孤儿的时间戳压到最老：它本来就该先被淘汰（它还不是任何照片的缓存）。
    os.utime(orphan, (1000, 1000))
    kept = entries(cache)
    for offset, entry in enumerate(kept, start=2000):
        os.utime(entry, (offset, offset))
    limited = ThumbnailCache(cache.directory, max_edge=64, max_bytes=1)

    removed = limited.enforce_limit()

    assert removed == 3
    assert limited.total_bytes() == 0
    assert not orphan.exists()


def test_clear_removes_orphans(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """清空缓存必须把孤儿一起清掉，还要**算进报出来的条数**。

    过去 ``clear()`` 报「已清空」却把孤儿原封不动留在那儿：用户点了清空，目录却没有
    变小，而「清空之后还是这么大」是最难自查的一类问题。
    """
    cache.get(PhotoRef.from_path(make_photo("a.jpg", size=(400, 200))))
    orphan = orphan_after_a_crash(cache, make_photo, monkeypatch)

    removed = cache.clear()

    assert removed == 2
    assert not orphan.exists()
    assert list(cache.directory.iterdir()) == []


def test_clear_still_spares_lookalikes_the_module_never_named(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**删之前必须认得出「这是自己的东西」**，否则就会删掉用户放进来的文件。

    ``clear()`` 的承诺是「只删自己认识的条目」。下面这些一个个都长得像孤儿，但没有一条
    是 ``mkstemp(prefix="<40 位摘要><扩展名>.", suffix=".tmp")`` 真的会起出来的名字 ——
    判据放宽一点，它们就会跟着一起消失。
    """
    cache.get(PhotoRef.from_path(make_photo("a.jpg", size=(400, 200))))
    real = entries(cache)[0]
    digest = real.name.removesuffix(".jpg")
    spared = [
        "用户自己放的.jpg.a1b2c3d4.tmp",  # 摘要不是 40 位十六进制
        f"{digest}.jpeg.a1b2c3d4.tmp",  # 不是本模块会写的扩展名
        f"{digest}.jpg.a1b2c3d.tmp",  # 随机段不是 8 个字符
        f"{digest}.jpg.a1b2c3d4x.tmp",  # 多一个字符
        f"{digest}.jpg.a1b2c3d4e.tmp",  # 随机段里混进了字母表之外的字符
        f"{digest.upper()}.jpg.a1b2c3d4.tmp",  # 摘要不是小写
        f"{digest}.jpg.a1b2c3d4.tmp.part",  # 不是 .tmp 收尾
    ]
    cache.directory.mkdir(parents=True, exist_ok=True)
    for name in spared:
        (cache.directory / name).write_text("别删我", encoding="utf-8")
    orphan = orphan_after_a_crash(cache, make_photo, monkeypatch)

    removed = cache.clear()

    assert removed == 2, "一条真条目 + 一条真孤儿"
    assert not orphan.exists()
    assert {path.name for path in cache.directory.iterdir()} == set(spared)


def test_writing_a_thumbnail_never_fsyncs(
    cache: ThumbnailCache, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """写缩略图**一次 fsync 都不该有**，而默认档还得是 True。

    缓存条目丢了可以重解一次，为它每次落盘等一次 fsync 纯属白付（那是用户每看一张图
    都要多等的一下）。但 ``durable`` 的**默认值不能动**：``config.toml`` 与 journal
    记录全靠它，写坏了就是数据没了（红线 4）。
    """
    syncs: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        syncs.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(atomic.os, "fsync", counting_fsync)
    assert inspect.signature(atomic.write_bytes_atomic).parameters["durable"].default is True

    cache.get(PhotoRef.from_path(make_photo("a.jpg", size=(400, 200))))

    assert syncs == [], "缩略图是可再生的，不该付 fsync"


def test_a_torn_thumbnail_is_regenerated_without_fsync(
    cache: ThumbnailCache, make_photo: Callable[..., Path]
) -> None:
    """``durable=False`` 之后断电能留下的最坏结果，是半截文件 —— 而半截文件能自愈。

    缩略图缓存是**可再生的**，所以写它时不 fsync（见上一条）。代价是断电可能留下一条
    内容不全的条目；这一条钉住的是「那份代价真的可接受」：PIL 解不开就删掉重生成
    （``UnidentifiedImageError`` 也是 ``OSError``）。半截 JPEG 在 PIL 明确报错，
    不会解出一张少了几道扫描线的图来冒充正常缓存。
    """
    ref = PhotoRef.from_path(make_photo("a.jpg", size=(400, 200)))
    cache.get(ref)
    entry = entries(cache)[0]
    entry.write_bytes(entry.read_bytes()[: len(entry.read_bytes()) // 2])

    raster = cache.get(ref)

    assert raster.size == (64, 32)
    assert entry.read_bytes()[:2] == b"\xff\xd8"


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
