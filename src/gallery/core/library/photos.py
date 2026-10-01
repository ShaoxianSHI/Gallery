"""照片的识别与轻量描述。

``PhotoRef`` 只携带「定位 + 判断缓存是否失效」所需的信息（路径、大小、修改时间），
**不读像素、不读 EXIF**：扫描几万张照片时，任何一次多余的 open() 都会让首屏变慢。
需要更丰富的元数据时再引入索引层（M2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: v1 支持的图片扩展名（小写，含点）。RAW 明确不在其中，见
#: docs/adr/0001-viewer-architecture.md。
SUPPORTED_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".tif",
        ".tiff",
        ".gif",
        ".webp",
        ".bmp",
    }
)

_FORMAT_LABELS: Final[dict[str, str]] = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".heic": "HEIC",
    ".heif": "HEIF",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".gif": "GIF",
    ".webp": "WebP",
    ".bmp": "BMP",
}


def format_label(suffix: str) -> str:
    """把扩展名转成界面展示用的格式名（``.jpg`` → ``JPEG``）。"""
    return _FORMAT_LABELS.get(suffix.lower(), suffix.lstrip(".").upper() or "未知")


def is_supported_photo(path: Path) -> bool:
    """按扩展名判断是否为 v1 支持的照片。不检查文件是否真的能解码。"""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


@dataclass(frozen=True, slots=True)
class PhotoRef:
    """对一张照片的轻量引用。"""

    path: Path
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> PhotoRef:
        """读取文件状态构造引用（会触发一次 stat）。"""
        path = Path(path)
        stat = path.stat()
        return cls(path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def format_label(self) -> str:
        return format_label(self.suffix)

    def is_supported(self) -> bool:
        return self.suffix in SUPPORTED_SUFFIXES


@dataclass(frozen=True, slots=True)
class FolderRef:
    """对子文件夹（相册）的轻量引用。

    相册模式逐层浏览时，同一层里会有文件夹和照片混排；两者都用「路径 + 名称」
    描述，界面据此画卡片、决定双击是进入相册还是打开照片。
    """

    path: Path
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> FolderRef:
        path = Path(path)
        stat = path.stat()
        return cls(path=path, mtime_ns=stat.st_mtime_ns)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def display_name(self) -> str:
        """文件夹名为空时的兜底（例如根目录）。"""
        return self.path.name or str(self.path)


#: 网格里可能出现的东西：一张照片，或一个相册（子文件夹）。
type GridEntry = PhotoRef | FolderRef


__all__ = [
    "SUPPORTED_SUFFIXES",
    "FolderRef",
    "GridEntry",
    "PhotoRef",
    "format_label",
    "is_supported_photo",
]
