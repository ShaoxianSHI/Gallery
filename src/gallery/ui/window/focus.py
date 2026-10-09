"""大图页正看着哪一行、这一层该选中哪一格（ADR 0053）。# guard: allow-long-comment（跨字段不变式）

**两件事合起来才说得清**：「换层了，没人正看着任何一张」与「这一层第一次摆好时该选中
哪一格」必须同时发生 —— 只做前者的话新的一层谁也不选，只做后者的话大图页指着上一层的
行号，而那一行在新的模型里是另一张照片。

``_current_row`` 与网格自己的当前项是**两份记账**（代码里已经写明：对不上时 ⌘Z 取回来的
照片会插到别处去）。本模块收的是这一侧的写入口，不动那两份记账本身。

``FocusMixin`` 那个属性是**只读**的：22 处测试在读 ``window._current_row``，
而写入口必须是具名的方法 —— 留着 setter 就等于把 ADR 0051 决策 4 那一刀架空。
"""

from __future__ import annotations

from gallery.ui.window.navigation import EntryKey, LevelKey

__all__ = ["FocusMixin", "FocusState"]


class FocusState:
    """:attr:`current` 是大图页正看着的行号，其余三个是「这一层该选中哪一格」的记账。

    :param current: 大图页正看着的行号（``-1`` = 没在看任何一张）。
    """

    def __init__(self, current: int = -1) -> None:
        self._current = current
        #: 模型里此刻摆着的是哪一层（用来分清「换了一层」与「还是这一层，重排一遍」）。
        self._level_key: LevelKey | None = None
        #: 这一层第一次排好时该选中的一格（``None`` = 谁也不选，见 :meth:`arm`）。
        self._armed: EntryKey | None = None
        #: 上面那个**已经摆过了没有**。它是 :attr:`_armed` 的伴生字段，不是独立状态。
        self._armed_used = False
        #: 重排之后要回到哪一格（分组重进，ADR 0013）。
        self._pending: EntryKey | None = None

    # ---- 读 ---------------------------------------------------------------

    @property
    def current(self) -> int:
        """大图页正看着第几行（``-1`` = 没在看）。**网格页上问不到答案** ——
        那时该问网格自己的当前项，两者是两份记账（见模块文档）。"""
        return self._current

    @property
    def pending(self) -> EntryKey | None:
        return self._pending

    def is_same_level(self, level_key: LevelKey) -> bool:
        """模型里摆的是否仍是 ``level_key`` 那一层（换过层就是 ``False``）。

        「换了一层」与「还是这一层重排一遍」在屏幕上长得一样，只有比**层标识**才分得开
        （见 :func:`gallery.ui.window.navigation.level_key`）。
        """
        return self._level_key is not None and self._level_key == level_key

    # ---- 写：换层（一次做完两件事）--------------------------------------------

    def arm(self, level_key: LevelKey, remembered: EntryKey | None, focus: EntryKey | None) -> None:
        """到了新的一层：记下它该选中哪一格，并把「还没摆过」重新 armed。

        :param remembered: **还是同一层重排一遍**时网格上停着的那一格（``None` = 不是）。
        :param focus: 显式指定（往上一层退时给的那个相册 / 那个组）；不给就用 ``remembered``。
        **两件事必须一起做**：只重置 armed 而不记 ``level_key`` 的话，下一次
        :meth:`take_focus` 分不出「换层了要重来一遍」与「还是这一层，别再动了」。
        """
        self._level_key = level_key
        self._armed = focus if focus is not None else remembered
        self._armed_used = False

    def take_focus(self) -> EntryKey | None:
        """取出「这一层该选中哪一格」，**取过就不再给**（``None` = 谁也不选）。

        **一层只摆一次**：摆完之后就是用户自己的选择了，后台重排不该把它拽回去 ——
        那种重排由 ``_reorder_level`` 的锚点负责。所以这里把「已经摆过」自己记上，
        调用点不必记得配一句清零（漏一句的症状是新的一层永远选不中）。
        """
        if self._armed_used:
            return None
        self._armed_used = True
        armed, self._armed = self._armed, None
        return armed

    def clear_current(self) -> None:
        """**这一层换了，没人正看着任何一张。**

        换层、换库、换浏览方式、点日历格、展开日历格、收日历格 —— 六处一模一样，
        而漏掉一处的症状是大图页指着上一层的行号，而那一行在新的模型里是另一张照片。
        """
        self._current = -1

    def forget_level(self) -> None:
        """关库 / 换库：把上一个库的选中与层标识都作废。

        不作废的话，用户在新库里会看到上一座库的相册被选中。
        """
        self._level_key = None
        self._armed = None
        self._armed_used = True

    # ---- 写：大图页翻到哪一行 -----------------------------------------------

    def set_current(self, row: int) -> None:
        """大图页正看着第 ``row`` 行（``_load_detail`` 之前先调它）。"""
        self._current = row

    # ---- 写：重排之后回到哪一格（分组重进）-----------------------------------

    def remember_pending(self, key: EntryKey | None) -> None:
        """记下重排完之后该回到哪一格（``None` = 记下「哪儿也不回」）。

        这一趟不是用户自己退出去的，所以停着的那一格要记下来 ——
        不然库里只是多了一张照片，人正在看的那张就被甩掉了。
        """
        self._pending = key

    def take_pending(self) -> EntryKey | None:
        """取出并作废（重排只发生一次，第二次来的时候那个组多半已经不在了）。"""
        pending, self._pending = self._pending, None
        return pending


class FocusMixin:
    """那一族在窗口上的只读面，加一句 :meth:`_init_focus`。

    ``_current_row`` 有 22 处测试在读，所以属性留着；**只读**：写入口是 :class:`FocusState`
    的那几个方法，而 ``self._current_row = …`` 不是方法。
    """

    def _init_focus(self) -> None:
        """建这一族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._focus_state = FocusState()

    @property
    def _current_row(self) -> int:
        """大图页正看着第几行（``-1`` = 没在看）。"""
        return self._focus_state.current

    @property
    def _pending_focus(self) -> EntryKey | None:
        """重排之后要回到哪一格（分组重进，ADR 0013）。"""
        return self._focus_state.pending
