"""缩略图磁盘缓存。

缓存是**可以随时删掉**的东西，所以这里的每条路径都必须能自愈：目录不存在就建，
缓存文件损坏就删掉重新生成，超过上限就按修改时间淘汰。缓存写不进去（磁盘满、目录只读）
只应导致「慢一点」，绝不能让看图这件事失败。

**原子写崩在中间留下的 ``.tmp`` 也是缓存条目**：见 :func:`_is_orphan`。认不出它，
2 GiB 上限就成了假上限（总量算少了、淘汰轮不到它、清空也清不掉）。

缓存 key 覆盖所有会影响像素结果的输入：
``(绝对路径, mtime_ns, size, max_edge, spec_version)``。
照片被外部工具改写（mtime/size 变化）或缩略图规格升级时，旧条目自动不再命中。

带透明通道的源图存 PNG（否则透明区域会变黑），其余存 JPEG 以省空间。

**总量靠记账而不是靠数目录**（见 ``_total_bytes`` 与 ADR 0015）：「缓存有多大了」
这个问题一旦靠 ``iterdir()`` 来答，每写一张图就付一次 O(条目数)，在默认 2 GiB 上限
下每张要付掉近 10 ms —— 比编码加落盘还贵一倍，而且缓存越大越贵、从不收敛。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import re
import stat as stat_module
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PIL import Image

from gallery.core.atomic import write_bytes_atomic
from gallery.core.constants import (
    DEFAULT_MAX_CACHE_BYTES,
    DEFAULT_THUMBNAIL_MAX_EDGE,
    THUMBNAIL_SPEC_VERSION,
)
from gallery.core.library.photos import PhotoRef
from gallery.core.media.decode import RasterImage, decode_rgba
from gallery.core.paths import resolved_str

#: 缓存 JPEG 的质量。缩略图不需要更高质量，82 在体积与观感上最划算
#: （相对 85 省 7.6% 体积，PSNR 只掉 0.7 dB，而它最多只显示成 288 逻辑像素）。
JPEG_QUALITY = 82

#: 缓存 JPEG 的色度抽样。**显式写出来而不是靠默认值**：libjpeg 在这个质量下本来就
#: 选 4:2:0，写出来只是让「用哪一档色度抽样」成为代码里的一行承诺（ADR 0015）。
JPEG_SUBSAMPLING = "4:2:0"

#: 触发淘汰后把总量压到上限的这个比例，避免在阈值附近反复淘汰。
_EVICT_TARGET_RATIO = 0.9

_ENTRY_SUFFIXES = (".jpg", ".png")

#: ``atomic.write_bytes_atomic`` 的临时文件后缀（``tempfile.mkstemp(suffix=...)``）。
_TMP_SUFFIX = ".tmp"

#: ``mkstemp`` 在 prefix 与 suffix 之间塞的那一串随机字符：固定 8 个，取自
#: ``tempfile`` 自己的字母表（小写字母 + 数字 + 下划线）。
_MKTEMP_RANDOM_LEN = 8
_MKTEMP_RANDOM_PATTERN = "[a-z0-9_]"

#: 「这个文件名是不是本模块自己留下的原子写临时文件」的判据，逐段对上
#: ``mkstemp(dir=..., prefix=f"{条目名}.", suffix=".tmp")`` 实际会产出的那一串：
#:
#:     <40 位十六进制摘要> + (".jpg" | ".png") + "." + <8 个随机字符> + ".tmp"
#:
#: **摘要长度从 ``hashlib.sha1()`` 取**，不是写死的数字 —— 换了摘要算法它跟着变，
#: 不会留下一条「判据说 40 位、实际是 64 位」的静默失效。
#:
#: 为什么非要认得这么死：认宽了就会去删用户自己放进来的文件。``clear()`` 的承诺是
#: 「只删自己认识的条目」，一个叫 ``holiday.jpg.a1b2c3d4.tmp`` 的用户文件不该被删掉；
#: 而一个 40 位十六进制摘要加上 8 位随机尾巴，用户几乎不可能自己造出来撞上 —— 撞上了
#: 它也确实只可能是我们崩在 ``mkstemp`` 与 ``os.replace`` 之间留下的。
_ORPHAN_PATTERN: Final = re.compile(
    rf"[0-9a-f]{{{hashlib.sha1().digest_size * 2}}}"
    rf"(?:{'|'.join(re.escape(suffix) for suffix in _ENTRY_SUFFIXES)})"
    rf"\.{_MKTEMP_RANDOM_PATTERN}{{{_MKTEMP_RANDOM_LEN}}}{re.escape(_TMP_SUFFIX)}"
)


def _is_orphan(name: str) -> bool:
    """这个文件名是不是一次**崩在半路的原子写**留下的孤儿？

    ``write_bytes_atomic`` 的顺序是 ``mkstemp → 写 → fsync → os.replace``。进程被
    SIGKILL、机器断电都只能崩在中间，``os.replace`` 没跑到，于是那个临时文件**永远留在
    缓存目录里**：没人会再来收它，可它实实在在占着磁盘。

    它过去对记账完全隐形（``_entries()`` 只认 ``.jpg`` / ``.png``），于是三件事同时坏掉：
    ``total_bytes()`` 少算、``enforce_limit()`` 够不着它、``clear()`` 报告「已清空」却把它
    留下 —— 2 GiB 上限因此不是真的上限，缓存可以一直涨下去。认了它就只是又一条可淘汰
    的条目，和别的条目走同一套记账、同一次淘汰、同一回删除。
    """
    return _ORPHAN_PATTERN.fullmatch(name) is not None


class ThumbnailCache:
    """某个库（或某个库的系统级）缩略图缓存目录。"""

    def __init__(
        self,
        directory: Path,
        *,
        max_edge: int = DEFAULT_THUMBNAIL_MAX_EDGE,
        spec_version: int = THUMBNAIL_SPEC_VERSION,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        self._directory = Path(directory)
        self._max_edge = max_edge
        self._spec_version = spec_version
        self._max_bytes = max_bytes
        #: 记账下来的缓存总量。``None`` = 还没数过（必须真的扫一遍目录）。
        #:
        #: **第一次必须播种**：进程重启后计数归零，不扫一遍就会以为缓存是空的，
        #: 上限彻底失效、缓存无限涨。之后每写成功一张 ``+= len(data)``，
        #: 只有真的越过上限时才去扫，扫完用结果重置，计数不会越走越偏。
        self._total_bytes: int | None = None

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def max_edge(self) -> int:
        return self._max_edge

    def get(self, ref: PhotoRef, *, should_cancel: Callable[[], bool] | None = None) -> RasterImage:
        """取缩略图：命中缓存直接读，未命中则解码并落盘。

        :param should_cancel: 解码途中问一句「还要继续吗」（切库、关窗时用）。
        :raises gallery.core.errors.ImageDecodeError: 源图无法解码。
        """
        digest = self._key(ref)
        for suffix in _ENTRY_SUFFIXES:
            cached = self._read_entry(self._entry_path(digest, suffix))
            if cached is not None:
                return cached
        raster = decode_rgba(
            ref.path, max_edge=self._max_edge, use_draft=True, should_cancel=should_cancel
        )
        self._write_entry(digest, raster)
        return raster

    def relocate(self, old: PhotoRef, new: PhotoRef) -> bool:
        """把 ``old`` 的缓存条目挪到 ``new`` 的键上（照片改名后调用）。

        缓存 key 含绝对路径，改名后旧条目永远不会再被命中，却会一直占着空间直到被淘汰。
        挪一下既省掉重新解码，也让「清空缩略图缓存」不必背这个锅。
        撤销改名时反向调用一次即可。返回是否真的挪动了条目。
        """
        moved = False
        for suffix in _ENTRY_SUFFIXES:
            source = self._entry_path(self._key(old), suffix)
            if not source.is_file():
                continue
            destination = self._entry_path(self._key(new), suffix)
            try:
                if destination.exists():
                    # 新键下已经有（多半是同一张照片之前被复制过），旧的直接丢。
                    # 字节数因此变了，记账得作废 —— 这是本函数唯一会让记账失准的分支。
                    source.unlink()
                    self._total_bytes = None
                else:
                    source.rename(destination)
            except OSError:
                continue  # 缓存挪不动只是慢一点，不影响正确性
            moved = True
        return moved

    def clear(self) -> int:
        """删除缓存目录中的全部缩略图，返回删除的条目数。

        只删自己认识的条目（``.jpg`` / ``.png``，以及原子写崩在中间留下的 ``.tmp``
        孤儿 —— 它们同样是本模块自己造出来的文件，见 :func:`_is_orphan`），不碰用户
        放进来的其他文件。判据逐段对上 ``mkstemp`` 实际会起的名字，所以用户随手丢进去
        一个 ``某某.jpg.tmp`` 是不会被误删的。
        """
        removed = 0
        for path, _, _ in self._entries():
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        self._total_bytes = 0
        return removed

    def total_bytes(self) -> int:
        """当前缓存占用的字节数（真的数一遍，顺手把记账纠正成准数）。"""
        total = sum(size for _, size, _ in self._entries())
        self._total_bytes = total
        return total

    def enforce_limit(self) -> int:
        """按修改时间从旧到新淘汰，直到低于上限。返回删除的条目数。

        扫描结果会写回记账，于是下一次「是否超限」的判断又是从准数出发。
        """
        if self._max_bytes <= 0:
            return 0
        entries = self._entries()
        total = sum(size for _, size, _ in entries)
        self._total_bytes = total
        if total <= self._max_bytes:
            return 0
        target = int(self._max_bytes * _EVICT_TARGET_RATIO)
        removed = 0
        for path, size, _ in sorted(entries, key=lambda item: item[2]):
            if total <= target:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        self._total_bytes = total
        return removed

    def _over_limit(self) -> bool:
        """记着账就能答的问题；只有「还没数过」才需要真的去数一遍。"""
        if self._max_bytes <= 0:
            return False
        if self._total_bytes is None:
            return True  # 未知 → 当作超限，逼着 enforce_limit 去数一遍并播种
        return self._total_bytes > self._max_bytes

    def _key(self, ref: PhotoRef) -> str:
        raw = "\x00".join(
            (
                resolved_str(ref.path),
                str(ref.mtime_ns),
                str(ref.size),
                str(self._max_edge),
                str(self._spec_version),
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _entry_path(self, digest: str, suffix: str) -> Path:
        return self._directory / f"{digest}{suffix}"

    def _read_entry(self, entry: Path) -> RasterImage | None:
        if not entry.is_file():
            return None
        try:
            with Image.open(entry) as image:
                return RasterImage.from_pil(
                    image,
                    # 只有带透明的源图才会被写成 PNG（见 _write_entry）。
                    has_alpha=entry.suffix == ".png",
                )
        except (OSError, ValueError):
            # 缓存坏了不是错误，删掉重生成即可 —— 但**删本身也可能失败**（只读的
            # 条目或只读的目录），而本模块的承诺是「缓存出问题最多让看图慢一点，
            # 绝不失败」。清不掉就留着：下次照样读不出来、再走这一遍，仅此而已。
            with contextlib.suppress(OSError):
                entry.unlink(missing_ok=True)
            return None

    def _write_entry(self, digest: str, image: RasterImage) -> None:
        data, suffix = _encode(image)
        try:
            # durable=False：缓存条目是**可再生的**（源图还在，重解一次就有），为它付一次
            # fsync 纯属浪费 —— 那是每张图都要等的一次系统调用。换来的是：崩在断电上
            # 至多留下半截文件，而 :meth:`_read_entry` 对半截文件是能自愈的（PIL 解不开
            # 就删掉重生成，OSError 与 UnidentifiedImageError 都是 OSError）。
            write_bytes_atomic(self._entry_path(digest, suffix), data, durable=False)
        except OSError:
            # 缓存写不进去只意味着下次还得重新解码，不影响正确性。
            return
        if self._total_bytes is not None:
            self._total_bytes += len(data)
        if self._over_limit():
            # 只有真的越过上限才付一次全目录扫描。扫描完 enforce_limit 会把记账
            # 重置成准数，于是下一次超限判断又回到 O(1) 的整数比较。
            self.enforce_limit()

    def _entries(self) -> list[tuple[Path, int, float]]:
        """列出缓存条目：``(路径, 字节数, 修改时间)``。

        **一次 ``stat()`` 走到底**：``Path.is_file()`` 本身就是一次 ``stat``，
        紧接着 ``path.stat()`` 又是同一个 inode 的第二次。三千个条目的目录要多付
        三千次系统调用，而 :meth:`enforce_limit`（每次越过上限写图都会走一遍）
        正是这条路上最热的一处。

        **孤儿（``.tmp``）也算条目**：见 :func:`_is_orphan`。这里认它，记账、淘汰、
        清空三处就一起对上了 —— 漏掉任何一处，2 GiB 上限都只是看着像上限。
        """
        try:
            candidates = list(self._directory.iterdir())
        except OSError:
            return []
        entries: list[tuple[Path, int, float]] = []
        for path in candidates:
            if path.suffix not in _ENTRY_SUFFIXES and not _is_orphan(path.name):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not stat_module.S_ISREG(stat.st_mode):
                continue
            entries.append((path, stat.st_size, stat.st_mtime))
        return entries


def _encode(image: RasterImage) -> tuple[bytes, str]:
    """把像素编码成缓存文件内容，返回 ``(数据, 扩展名)``。

    JPEG 这条路刻意**不**开 ``optimize``：它会额外跑一遍哈夫曼表搜索，对一张
    512 px 的缩略图来说，编码时间翻倍（0.84 → 0.36 ms）换来的体积只少 2%。
    真正的大头是质量从 85 降到 82 —— 那个既省时间也省体积（ADR 0015）。
    """
    buffer = io.BytesIO()
    raster = Image.frombytes("RGBA", (image.width, image.height), image.rgba)
    try:
        if image.has_alpha:
            raster.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), ".png"
        rgb = raster.convert("RGB")
        try:
            rgb.save(
                buffer,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=False,
                subsampling=JPEG_SUBSAMPLING,
            )
        finally:
            rgb.close()
        return buffer.getvalue(), ".jpg"
    finally:
        raster.close()


__all__ = ["JPEG_QUALITY", "JPEG_SUBSAMPLING", "ThumbnailCache"]
