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

    **抛出来的时候，文件已经回到写之前的样子**（除非 :attr:`GalleryError.detail` 说明回滚
    本身也失败了，横幅必须照实说，见 ``writeback.write_metadata``）。与 :class:`MetadataError`
    分开：那条是「读不到」，这条是「改不了」，界面给出的下一步动作完全不同。
    """


class RenameError(GalleryError):
    """重命名被拒绝或失败（名字非法、目标已存在、权限不足、撤销条件不满足）。"""


class DeleteError(GalleryError):
    """移到废纸篓被拒绝或失败（越权、废纸篓不可用、恢复时原位置已被占用）。"""


class AlbumError(GalleryError):
    """新建相册被拒绝或失败（目标不是一个文件夹、名字非法、没有写权限）。

    建出来的东西落在 ``album.CreatedAlbum`` 里，只回答「建没建得成」，没有逐项成败可言（与粘贴、
    移动到相册不同）。名字非法那一条与改名的判据、文案是**同一份实现**
    （:func:`gallery.core.metadata.naming.clean_name`），只是抛出的类型不同。
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


class CoverNotBoundError(GalleryError):
    """要把一张照片设成事件封面，而它不属于这个事件（ADR 0048 决策 1）。

    界面那一处只对**已绑定的**照片开放，所以这条多半来自别的入口或者手工改过的库。
    它与 :class:`EventsUnavailableError` 分开：事件库这一次完全正常，是**这一张**挑错了。
    """


class ShowImportError(GalleryError):
    """从「演迹」导出的表格里读演出失败（文件不在、不是 .xlsx、或者不是演出列表）。

    与 :class:`MetadataError` 分开：这条要用户做的动作是「换一个文件 / 从演迹重新导出一次」，
    与「装 exiftool」完全不是一回事，混在一起只能给一句没用的提示（ADR 0047）。
    """


class EventNotFoundError(GalleryError):
    """按 id 找一个事件，而库里没有这一条。

    界面通常先拿手上那份清单判一次（清单是缓存的，可能已经过期），所以这一条多半来自
    「看着旧的清单点了删除」：**必须给一句中文**，不能抛裸的 ``KeyError`` ——
    跑在主线程的槽里，逃出去没有第二个人接得住，用户看到的是「点了没反应」。
    """


class MovieImportError(GalleryError):
    """按番号抓取影片元数据、或把封面写进库里失败。

    两种来路分开说：找源网站要么是网络不通/被挡，要么是番号在源站查不到；写封面则是目标文件夹
    写不进去。合并成一条只会让用户没法判断下一步该改什么（重试 / 换个番号 / 检查目录权限）。
    """


__all__ = [
    "AlbumError",
    "ConfigError",
    "CoverNotBoundError",
    "DeleteError",
    "EventNotFoundError",
    "EventsUnavailableError",
    "GalleryError",
    "ImageDecodeError",
    "IndexUnavailableError",
    "LibraryNotAccessibleError",
    "MetadataError",
    "MetadataWriteError",
    "MovieImportError",
    "PasteError",
    "RenameError",
    "ShowImportError",
    "TransferError",
]
