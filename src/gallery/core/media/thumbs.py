"""缩略图磁盘缓存。

缓存是**可以随时删掉**的东西，所以这里的每条路径都必须能自愈：目录不存在就建，
缓存文件损坏就删掉重新生成，超过上限就按修改时间淘汰。缓存写不进去（磁盘满、目录只读）
只应导致「慢一点」，绝不能让看图这件事失败。

缓存 key 覆盖所有会影响像素结果的输入：
``(绝对路径, mtime_ns, size, max_edge, spec_version)``。
照片被外部工具改写（mtime/size 变化）或缩略图规格升级时，旧条目自动不再命中。

带透明通道的源图存 PNG（否则透明区域会变黑），其余存 JPEG 以省空间。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from gallery.core.atomic import write_bytes_atomic
from gallery.core.constants import (
    DEFAULT_MAX_CACHE_BYTES,
    DEFAULT_THUMBNAIL_MAX_EDGE,
    THUMBNAIL_SPEC_VERSION,
)
from gallery.core.library.photos import PhotoRef
from gallery.core.media.decode import RasterImage, decode_rgba

#: 缓存 JPEG 的质量。缩略图不需要更高质量，85 在体积与观感上最划算。
JPEG_QUALITY = 85

#: 每写入多少次检查一次缓存上限。避免每张图都去列目录。
_LIMIT_CHECK_INTERVAL = 64

#: 触发淘汰后把总量压到上限的这个比例，避免在阈值附近反复淘汰。
_EVICT_TARGET_RATIO = 0.9

_ENTRY_SUFFIXES = (".jpg", ".png")


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
        self._writes_since_check = 0

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def max_edge(self) -> int:
        return self._max_edge

    def get(self, ref: PhotoRef) -> RasterImage:
        """取缩略图：命中缓存直接读，未命中则解码并落盘。

        :raises gallery.core.errors.ImageDecodeError: 源图无法解码。
        """
        digest = self._key(ref)
        for suffix in _ENTRY_SUFFIXES:
            cached = self._read_entry(self._entry_path(digest, suffix))
            if cached is not None:
                return cached
        raster = decode_rgba(ref.path, max_edge=self._max_edge, use_draft=True)
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
                    source.unlink()
                else:
                    source.rename(destination)
            except OSError:
                continue  # 缓存挪不动只是慢一点，不影响正确性
            moved = True
        return moved

    def clear(self) -> int:
        """删除缓存目录中的全部缩略图，返回删除的条目数。

        只删自己认识的条目（``.jpg`` / ``.png``），不碰用户放进来的其他文件。
        """
        removed = 0
        for path, _, _ in self._entries():
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    def total_bytes(self) -> int:
        """当前缓存占用的字节数。"""
        return sum(size for _, size, _ in self._entries())

    def enforce_limit(self) -> int:
        """按修改时间从旧到新淘汰，直到低于上限。返回删除的条目数。"""
        if self._max_bytes <= 0:
            return 0
        entries = self._entries()
        total = sum(size for _, size, _ in entries)
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
        return removed

    def _key(self, ref: PhotoRef) -> str:
        raw = "\x00".join(
            (
                str(ref.path.expanduser().resolve()),
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
                rgba = image.convert("RGBA")
                try:
                    return RasterImage(
                        width=rgba.width,
                        height=rgba.height,
                        rgba=rgba.tobytes(),
                        # 只有带透明的源图才会被写成 PNG（见 _write_entry）。
                        has_alpha=entry.suffix == ".png",
                    )
                finally:
                    rgba.close()
        except (OSError, ValueError, UnidentifiedImageError):
            # 缓存坏了不是错误，删掉重生成即可。
            entry.unlink(missing_ok=True)
            return None

    def _write_entry(self, digest: str, image: RasterImage) -> None:
        data, suffix = _encode(image)
        try:
            write_bytes_atomic(self._entry_path(digest, suffix), data)
        except OSError:
            # 缓存写不进去只意味着下次还得重新解码，不影响正确性。
            return
        self._writes_since_check += 1
        if self._writes_since_check >= _LIMIT_CHECK_INTERVAL:
            self._writes_since_check = 0
            self.enforce_limit()

    def _entries(self) -> list[tuple[Path, int, float]]:
        """列出缓存条目：``(路径, 字节数, 修改时间)``。"""
        try:
            candidates = list(self._directory.iterdir())
        except OSError:
            return []
        entries: list[tuple[Path, int, float]] = []
        for path in candidates:
            if not path.is_file() or path.suffix not in _ENTRY_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path, stat.st_size, stat.st_mtime))
        return entries


def _encode(image: RasterImage) -> tuple[bytes, str]:
    """把像素编码成缓存文件内容，返回 ``(数据, 扩展名)``。"""
    buffer = io.BytesIO()
    raster = Image.frombytes("RGBA", (image.width, image.height), image.rgba)
    try:
        if image.has_alpha:
            raster.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), ".png"
        rgb = raster.convert("RGB")
        try:
            rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        finally:
            rgb.close()
        return buffer.getvalue(), ".jpg"
    finally:
        raster.close()


__all__ = ["JPEG_QUALITY", "ThumbnailCache"]
