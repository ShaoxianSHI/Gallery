"""「现在开着哪个库」这一族的状态（ADR 0058 阶段 3 上半）。

四样东西一起换：打开的 :class:`~gallery.core.library.facade.Library`、挂在它上面的
缩略图流水线、窗口标题，以及从旧 ``state.json`` 读来的那一次库级偏好。
收进来之前它们是窗口上的字段，于是 ``MainWindow.__init__`` 与 ``library_flow``
各写一遍 —— 而「关库时该把这四样一起清掉」这件事只存在于读代码的人脑子里。
"""

from __future__ import annotations

from gallery.core.library.config import LibraryUIState
from gallery.core.library.facade import Library

if True:  # TYPE_CHECKING
    from gallery.ui.thumbs.pipeline import ThumbnailPipeline

__all__ = ["LibrarySlot"]


class LibrarySlot:
    """**当前打开的库**这一族的唯一存储（ADR 0058 阶段 3）。

    关库就是调 :meth:`clear` —— 四样一起清，而不是各处记得自己置 ``None``。
    """

    #: 打开着的库（``None`` = 还没开或者已经关了）。
    _library: Library | None
    #: 挂在那个库上的缩略图流水线。**与 ``_library`` 同生共死** —— 它持有那个库的
    #: 监听器，少清一次就是关库之后还在往旧库解码。
    _pipeline: ThumbnailPipeline | None
    #: 窗口标题上摆的名字。没开库时是产品名。
    _library_title: str
    #: 旧 ``state.json`` 里的库级偏好，**整个进程只读一次**（``None`` = 还没读）。
    _legacy_ui: LibraryUIState | None

    def __init__(self) -> None:
        self._library = None
        self._pipeline = None
        self._library_title = "Gallery"
        self._legacy_ui = None

    def clear(self) -> None:
        """**关库**：把这一族清回「没开库」。

        四样一起清 —— 漏掉 ``_pipeline`` 的症状是关库之后缩略图还在往上一个库解码，
        而界面上一个错都看不出来。
        """
        self._library = None
        self._pipeline = None
        self._library_title = "Gallery"
