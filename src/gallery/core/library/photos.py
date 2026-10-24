"""照片与视频的识别、轻量描述。

``PhotoRef`` 只携带「定位 + 判断缓存是否失效」所需的信息（路径、大小、修改时间），
**不读像素、不读 EXIF**：扫描几万张照片时，任何一次多余的 open() 都会让首屏变慢。
视频与照片共用同一个引用：网格、改名、事件、检索都只认「一条路径」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: v1 支持的图片扩展名（小写，含点）。RAW 明确不在其中（ADR 0001）。
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
    ".mp4": "MP4",
    ".m4v": "MP4",
    ".mov": "MOV",
    ".avi": "AVI",
    ".3gp": "3GP",
}

#: 能取第一帧的视频扩展名（ADR 0090）。**只收系统能解的容器**：AVFoundation 没有
#: Matroska 的解复用器，mkv / webm 进来只会得到一格灰。
VIDEO_SUFFIXES: Final[frozenset[str]] = frozenset({".mp4", ".m4v", ".mov", ".avi", ".3gp"})

#: 扫描与网格认的全部文件。**写回元数据仍然只认** :data:`SUPPORTED_SUFFIXES`。
MEDIA_SUFFIXES: Final[frozenset[str]] = SUPPORTED_SUFFIXES | VIDEO_SUFFIXES


def format_label(suffix: str) -> str:
    """把扩展名转成界面展示用的格式名（``.jpg`` → ``JPEG``）。"""
    return _FORMAT_LABELS.get(suffix.lower(), suffix.lstrip(".").upper() or "未知")


def normalize_suffix(text: str) -> str | None:
    """用户敲的格式名或扩展名 → 它的一个扩展名；``None`` 表示认不出来。

    **返回值不承诺唯一**：``JPEG`` 交回 ``.jpg`` 或 ``.jpeg`` 都只说明它属于哪个格式。
    要「这个格式的全部扩展名」用 :func:`format_suffixes`。视频也算 —— 检索的「#文件格式」
    与分组要能把 ``mp4`` 认出来。
    """
    cleaned = text.strip().casefold().lstrip(".")
    if not cleaned:
        return None
    suffix = f".{cleaned}"
    if suffix in MEDIA_SUFFIXES:
        return suffix
    for known, label in _FORMAT_LABELS.items():
        if label.casefold() == cleaned.casefold():
            return known
    return None


def format_suffixes(text: str) -> tuple[str, ...] | None:
    """用户敲的格式名或扩展名 → 这个格式的全部扩展名（``JPEG`` → ``(".jpeg", ".jpg")``）。

    ``None`` 表示认不出来。**同一个格式可能有好几个扩展名**（jpg / jpeg、tif / tiff），
    搜索的「#文件格式」与分组都按格式名认，于是 ``jpg`` 与 ``jpeg`` 不会变成两件事。
    """
    suffix = normalize_suffix(text)
    if suffix is None:
        return None
    label = format_label(suffix)
    return tuple(sorted(known for known, name in _FORMAT_LABELS.items() if name == label))


def is_supported_photo(path: Path) -> bool:
    """按扩展名判断是不是**图片**。不检查文件是否真的能解码。

    写回元数据那条路只认它：视频没有可写的 EXIF，也绝不能被当成照片写进去。
    扫描与网格用 :func:`is_supported_media`。
    """
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def is_supported_media(path: Path) -> bool:
    """按扩展名判断扫描要不要收它：照片或视频都算。不检查文件是否真的能解码。"""
    return Path(path).suffix.lower() in MEDIA_SUFFIXES


def is_video(path: Path) -> bool:
    """按扩展名判断它是不是视频。"""
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


@dataclass(frozen=True, slots=True)
class PhotoRef:
    """对一张照片或视频的轻量引用。"""

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

    @property
    def is_video(self) -> bool:
        return self.suffix in VIDEO_SUFFIXES

    def is_supported(self) -> bool:
        return self.suffix in MEDIA_SUFFIXES


@dataclass(frozen=True, slots=True)
class FolderRef:
    """对子文件夹（相册）的轻量引用。

    相册模式逐层浏览时文件夹与照片混排，两者都用「路径 + 名称」描述，界面据此画卡片、
    决定双击是进入相册还是打开照片。
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
        """文件夹名为空时的回退（例如根目录）。"""
        return self.path.name or str(self.path)


@dataclass(frozen=True, slots=True)
class GroupRef:
    """一个分组卡片：按元数据字段把一堆照片归到一起（见 ``library.groups``）。

    形态与 :class:`FolderRef` 一致 —— 网格只认「这一格画成什么、双击做什么」。
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
    #: 显式指定的封面（目前只有事件分组有，ADR 0048）。**已经解析成组内那一张** ——
    #: 存路径的话每次读封面都要在组里线性找一遍，而 ``data(DecorationRole)`` 每帧每张卡片
    #: 都读一次。解析不出（指定的照片不在这组里）是 ``None``，于是 :attr:`cover` 退回第一张。
    pinned_cover: PhotoRef | None = None

    @property
    def name(self) -> str:
        """网格里的显示名。"""
        return self.label

    @property
    def count(self) -> int:
        return len(self.photos)

    @property
    def cover(self) -> PhotoRef | None:
        """封面：指定的那张，没有就用组内第一张（已经排好序，也就是「这一组的代表」）。

        **卡片永远有一张封面**：指定的那张读不出来、不属于这组了、事件取消了封面 ——
        三种情形都退回第一张，而不是让这一格空着（与相册封面同一取向，ADR 0028 决策 4）。
        """
        return self.pinned_cover or (self.photos[0] if self.photos else None)

    @property
    def display_name(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class SectionRef:
    """分区标题：分组在分区式下占的那一行（不是卡片）。

    与 :class:`GroupRef` 的差别只在界面：卡片可以双击进入，标题只是一条分界线与一行字。
    ``group`` 留着是为了标题上的组级命令（复制分组条件、事件封面）不必再查一遍。
    """

    group: GroupRef
    #: 这一行下面**实际显示**的张数。一张照片属于多个组时只算第一个分区，所以它可能小于
    #: ``group.count``；所有分区的张数加起来等于这一层的照片数。
    count: int

    @property
    def facet(self) -> str:
        return self.group.facet

    @property
    def key(self) -> str:
        return self.group.key

    @property
    def label(self) -> str:
        return self.group.label

    @property
    def name(self) -> str:
        """网格里的显示名（与卡片、相册同一套读法）。"""
        return self.group.label


#: 网格里可能出现的东西：一张照片，一个相册（子文件夹），一个分组，或一条分区标题。
type GridEntry = PhotoRef | FolderRef | GroupRef | SectionRef


__all__ = [
    "MEDIA_SUFFIXES",
    "SUPPORTED_SUFFIXES",
    "VIDEO_SUFFIXES",
    "FolderRef",
    "GridEntry",
    "GroupRef",
    "PhotoRef",
    "SectionRef",
    "format_label",
    "format_suffixes",
    "is_supported_media",
    "is_supported_photo",
    "is_video",
    "normalize_suffix",
]
