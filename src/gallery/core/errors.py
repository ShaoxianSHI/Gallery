"""领域异常。

约定：``GalleryError.message`` 是**给用户看的**中文描述，必须能指向下一步动作
（「这个文件可能已损坏」比「发生错误」有用）。程序缺陷不要用这些异常表达 —— 让它带着堆栈进日志。

界面层捕获 ``GalleryError`` 并原样展示 ``message``；``detail`` 只进日志，不进界面。
"""

from __future__ import annotations

from pathlib import Path


class GalleryError(Exception):
    """Gallery 领域错误的基类。"""

    def __init__(
        self, message: str, *, path: Path | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        #: 面向用户的中文描述。
        self.message = message
        #: 出问题的路径（如果有）。
        self.path = path
        #: 面向开发者的补充信息，只进日志。
        self.detail = detail

    def __str__(self) -> str:
        return self.message


class LibraryError(GalleryError):
    """库本身不可用（不存在、不是目录、没有读取权限）。"""


class LibraryNotAccessibleError(LibraryError):
    """库根目录不存在、不是目录，或无法读取。"""


class ConfigError(GalleryError):
    """``config.toml`` 缺失以外的配置问题（语法错误、字段类型或取值非法）。"""


class UnsupportedFormatError(GalleryError):
    """文件扩展名不在支持列表内。"""


class ImageDecodeError(GalleryError):
    """文件是支持的格式，但解码失败（损坏、截断、缺少解码器）。"""


class MetadataError(GalleryError):
    """元数据读取失败（exiftool 缺席且降级也失败、输出无法解析、超时）。"""


class GeoDataError(GalleryError):
    """城市数据下载或写入失败（网络不通、压缩包损坏、缓存目录不可写）。"""


__all__ = [
    "ConfigError",
    "GalleryError",
    "GeoDataError",
    "ImageDecodeError",
    "LibraryError",
    "LibraryNotAccessibleError",
    "MetadataError",
    "UnsupportedFormatError",
]
