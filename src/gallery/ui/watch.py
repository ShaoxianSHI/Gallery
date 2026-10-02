"""文件变化 → 主线程。

``core`` 的监听器在自己的线程上回调（去抖的定时器也不是主线程），而界面绝不能在那个
线程上被碰 —— 这正是 ``ui/tasks.py`` 开头那三条规矩的由来。这里只有一个类，做同一件事：
用**排队连接**把一批变化搬回主线程，顺带管着监听器的生老病死（切库、关窗都得停）。

合并与去抖已经在 :mod:`gallery.core.library.watcher` 里做完：它们是纯逻辑，与线程无关，
放在 core 才能脱离 Qt 单测。这个模块因此薄得近乎什么都没做 —— 薄才对。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, SignalInstance

from gallery.core.library.facade import Library
from gallery.core.library.watcher import LibraryChange, LibraryWatcher

logger = logging.getLogger(__name__)

#: 一批变化交上来了。**用排队连接接它**（见 ``MainWindow``）：发出时在 watchdog 线程上。
type ChangeBatch = tuple[LibraryChange, ...]


def start_watch(
    library: Library,
    on_change: Callable[[ChangeBatch], None],
    on_error: Callable[[str], None] | None = None,
) -> LibraryWatcher:
    """默认的启动方式：经库门面开监听，范围跟着库的 ``recursive`` 配置。"""
    return library.watch(on_change=on_change, on_error=on_error)


#: 造监听器的方式：给一个库与两个回调，返回一个监听器。
#:
#: 测试注入假的，于是「外部改了磁盘 → 网格变了」这条链可以同步跑完，不必等真的事件。
type WatchStarter = Callable[
    [Library, Callable[[ChangeBatch], None], Callable[[str], None] | None],
    LibraryWatcher,
]


class WatchBridge(QObject):
    """持有一个 :class:`~gallery.core.library.watcher.LibraryWatcher`，并把它的回调搬回主线程。"""

    changed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        starter: WatchStarter = start_watch,
    ) -> None:
        super().__init__(parent)
        self._starter = starter
        self._watcher: LibraryWatcher | None = None

    @property
    def watching(self) -> bool:
        return self._watcher is not None

    def start(self, library: Library) -> None:
        """开始监听这个库。已经在监听就什么也不做。"""
        if self._watcher is not None:
            return
        watcher = self._starter(library, self._on_change, self._on_failed)
        watcher.start()
        self._watcher = watcher
        logger.info("开始监听 %s", library.root)

    def stop(self) -> None:
        """停止监听（切库、关窗时调用）。

        漏掉这一步的后果不是「多一个线程」，而是那个线程会继续往一个已经销毁的窗口
        发信号。
        """
        watcher, self._watcher = self._watcher, None
        if watcher is None:
            return
        watcher.stop()
        logger.debug("已停止监听")

    def _on_change(self, changes: ChangeBatch) -> None:
        # 发在 watchdog 的线程上；接的人必须用排队连接（也就是回到主线程）。
        self._emit(self.changed, changes)

    def _on_failed(self, message: str) -> None:
        self._emit(self.failed, message)

    @staticmethod
    def _emit(signal: SignalInstance, *args: object) -> None:
        """发信号，并容忍「接收方已经没了」。

        关窗与切库都可能和去抖定时器撞上：``stop()`` 取消得了还没到期的那个定时器，
        却拦不住**已经开跑**的那一个 —— 它照样会走到这里，而此时这个 ``WatchBridge``
        的 C++ 对象可能已经被销毁（这正是监听器头文件里那句「回调期间销毁监听器是
        最容易被忽略的一种」在界面这一侧的翻版）。发信号会因此抛 ``RuntimeError``，
        它若顺着监听线程一路抛出去，就是一个没人接的线程异常。

        处理办法与 :meth:`gallery.ui.tasks._Job._emit` 一致：记一条 debug 就够了 ——
        这一批变化本来就随那个窗口一起作废了，没有人需要知道。
        """
        try:
            signal.emit(*args)
        except RuntimeError:
            logger.debug("监听到文件变化时接收方已销毁，这一批作废")


__all__ = ["ChangeBatch", "WatchBridge", "WatchStarter", "start_watch"]
