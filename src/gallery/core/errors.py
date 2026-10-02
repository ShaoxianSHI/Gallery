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


class RenameError(GalleryError):
    """重命名被拒绝或失败（名字非法、目标已存在、权限不足、撤销条件不满足）。"""


class DeleteError(GalleryError):
    """移到废纸篓被拒绝或失败（越权、废纸篓不可用、恢复时原位置已被占用）。"""


class AlbumError(GalleryError):
    """新建相册被拒绝或失败（目标不是一个文件夹、名字非法、没有写权限）。

    建出来的东西落在 ``album.CreatedAlbum`` 里：它只回答「建没建得成」，
    没有逐项的成败可言（与粘贴、移动到相册不同，那两个结果里有一堆条目）。

    名字非法那一条与改名的判据、文案是**同一份实现**
    （:func:`gallery.core.metadata.naming.clean_name`），只是抛出的类型不同 ——
    两个入口的失败类型各自说得清自己做了什么。
    """


class PasteError(GalleryError):
    """粘贴被拒绝（目标不是一个文件夹）。逐项的成败不走异常，落在 ``paste.PasteResult``。"""


class TransferError(GalleryError):
    """移动到相册被拒绝或失败（目标不是一个相册、撤销时原位置已被占用）。

    逐项的成败不走异常，落在 ``transfer.MoveResult`` 里。
    """


__all__ = [
    "AlbumError",
    "ConfigError",
    "DeleteError",
    "GalleryError",
    "ImageDecodeError",
    "LibraryError",
    "LibraryNotAccessibleError",
    "MetadataError",
    "PasteError",
    "RenameError",
    "TransferError",
    "UnsupportedFormatError",
]
