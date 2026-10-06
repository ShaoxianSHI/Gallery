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


class LibraryNotAccessibleError(GalleryError):
    """库根目录不存在、不是目录，或无法读取。

    **中间那层 ``LibraryError`` 已删**：它只有这一个子类，全仓库没有一处按它
    ``except`` 或拿它做类型判断 —— 一个从不作为「父类契约」被使用的基类，
    留着只会让人以为「捕获 ``LibraryError``」是一条真实的退路。
    """


class ConfigError(GalleryError):
    """``config.toml`` 缺失以外的配置问题（语法错误、字段类型或取值非法）。"""


class ImageDecodeError(GalleryError):
    """文件是支持的格式，但解码失败（损坏、截断、缺少解码器）。"""


class MetadataError(GalleryError):
    """元数据读取失败（exiftool 缺席且降级也失败、输出无法解析、超时）。"""


class MetadataWriteError(GalleryError):
    """元数据写回被拒绝或失败（越权、格式不支持、exiftool 拒绝、写完校验不过）。

    **抛出来的时候，文件已经回到写之前的样子**（除非 :attr:`GalleryError.detail` 里
    说明了回滚本身也失败了 —— 那种情况横幅必须照实说，见 ``writeback.write_metadata``）。

    与 :class:`MetadataError` 分开：那条是「读不到」，这条是「改不了」，界面给出的下一步
    动作完全不同（装 exiftool / 解除只读 / 换个格式），混在一起只能给一句没用的提示。
    """


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
    """移动到相册整体被拒绝或失败（例如目标不是一个文件夹）。

    逐项的成败不走异常：正向的落在 ``transfer.MoveResult``，撤销的落在
    ``transfer.UndoMoveResult.failures`` —— 包括「原位置已经被别的文件占了」那种，
    它是**一项**没成，不是整次操作失败。
    """


class IndexUnavailableError(GalleryError):
    """索引没落盘（库所在卷只读，或用户把缓存放到了库外），于是检索做不了。

    **分组 / 排序那一族在同样情况下照常可用** —— 那是把读到的值揣在内存里算的，
    不需要索引。于是这里不是「整个功能没了」，而是「这一次检索没法做」，
    界面据此说清是哪一件（ADR 0026）。
    """


class EventsUnavailableError(GalleryError):
    """事件库挂不上（配置目录建不出来），于是事件这一族做不了。

    **只有配置目录整个建不出来时才会走到**（只读卷 / SMB / 网络盘断开），因为事件库就住在
    那个目录里（ADR 0043 决策 1）。界面据此让「记录为」那一行**不出现**而不是报错 ——
    浏览、分组、搜索全都照常，用户其实没有第三个有意义的选择。
    """


__all__ = [
    "AlbumError",
    "ConfigError",
    "DeleteError",
    "EventsUnavailableError",
    "GalleryError",
    "ImageDecodeError",
    "IndexUnavailableError",
    "LibraryNotAccessibleError",
    "MetadataError",
    "MetadataWriteError",
    "PasteError",
    "RenameError",
    "TransferError",
]
