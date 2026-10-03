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


def normalize_suffix(text: str) -> str | None:
    """用户敲的那个格式名 → 扩展名（``jpg`` / ``JPEG`` / ``.jpg`` 都认成 ``.jpg``）。

    ``None`` 表示认不出来 —— 与 :func:`format_label` 恰好互逆，把两张表合成一张，
    于是「格式叫什么」只有一处说了算（搜索的「#文件格式」条件与分组的格式字段共用它）。
    """
    cleaned = text.strip().casefold().lstrip(".")
    if not cleaned:
        return None
    suffix = f".{cleaned}"
    if suffix in SUPPORTED_SUFFIXES:
        return suffix
    for known, label in _FORMAT_LABELS.items():
        if label.casefold() == cleaned.casefold():
            return known
    return None


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


@dataclass(frozen=True, slots=True)
class GroupRef:
    """一个分组卡片：按元数据字段把一堆照片归到一起（见 ``library.groups``）。

    形态与 :class:`FolderRef` 一致 —— 网格只认「这一格画成什么、双击做什么」，
    所以分组在界面里就是一个能双击进去的卡片，而不是另一套交互。

    它**不是磁盘上的东西**：不可改名、不可移到废纸篓，操作只在进入之后对照片本身进行。
    """

    #: 分组字段的 id（``facets.Facet.id``）。
    facet: str
    #: 稳定的组标识（``"2024-05"``），用于比较与导航。
    key: str
    #: 界面上显示的组名（``"2024 年 5 月"``）。
    label: str
    #: 组内的照片，**已按当前排序排好**。
    photos: tuple[PhotoRef, ...]

    @property
    def name(self) -> str:
        """网格里的显示名。"""
        return self.label

    @property
    def count(self) -> int:
        return len(self.photos)

    @property
    def cover(self) -> PhotoRef | None:
        """封面：组内第一张（已经排好序，也就是「这一组的代表」）。"""
        return self.photos[0] if self.photos else None

    @property
    def display_name(self) -> str:
        return self.label


#: 网格里可能出现的东西：一张照片，一个相册（子文件夹），或一个分组。
type GridEntry = PhotoRef | FolderRef | GroupRef


__all__ = [
    "SUPPORTED_SUFFIXES",
    "FolderRef",
    "GridEntry",
    "GroupRef",
    "PhotoRef",
    "format_label",
    "is_supported_photo",
    "normalize_suffix",
]
