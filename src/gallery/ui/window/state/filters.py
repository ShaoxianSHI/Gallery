"""筛选 / 检索这一族的**状态**（ADR 0052，ADR 0060 与那个 mixin 分开住）。

四件事合起来才是一条完整的状态，拆开就会各自漂移：条件空了而编辑态还指着第 0 枚，
下一次回车就把那条凭空顶掉。所以它们都在这里，写入口只有下面那几个具名方法 ——
**挪下标只有 :meth:`FilterState.remove` 一处会做**。``window/filters.py`` 那个 mixin
的四个属性是**只读**的（留着 setter 就架空了「写入口收进轴」那条）。
"""

from __future__ import annotations

from gallery.core.library.config import (
    SEARCH_SCOPE_LEVEL,
    SEARCH_SCOPE_LIBRARY,
    SEARCH_SCOPES,
)
from gallery.core.library.search import SearchCondition

__all__ = ["FilterState"]


class FilterState:
    """条件列表 + 编辑下标 + 作用域 + 上一次命中数。

    :param scope: 检索条件的默认作用域（库级偏好，打开别的库时被换掉）。
    """

    def __init__(self, scope: str = SEARCH_SCOPE_LIBRARY) -> None:
        self._conditions: list[SearchCondition] = []
        self._scope: str = scope
        self._editing: int | None = None
        self._total: int | None = None

    # ---- 读 ---------------------------------------------------------------

    @property
    def conditions(self) -> list[SearchCondition]:
        """已生效的条件，按录入顺序。**返回的是那一份本身**，读没问题，改会被下面的方法管住。"""
        return self._conditions

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def editing(self) -> int | None:
        """正在改第几条（``None`` = 没在改）。**它是下标**，所以增删条件时必须跟着挪。"""
        return self._editing

    @property
    def total(self) -> int | None:
        """上一次检索命中多少张（面包屑上那个「搜索结果（128）」）；``None`` = 还没查过。"""
        return self._total

    def is_library_search(self) -> bool:
        """条件正**接管**这一层（整库检索），而不是在收窄它。

        **全窗口唯一的那句判据**：条件非空只说明「有条件」，不说明条件管到哪儿 ——
        两者在筛选下必须分开，而分开的地方多了就一定会漏一处。
        """
        return bool(self._conditions) and self._scope != SEARCH_SCOPE_LEVEL

    def is_filtering(self) -> bool:
        """条件正**收窄**这一层，且这一层仍然是它自己（月视图仍然是月视图）。"""
        return bool(self._conditions) and self._scope == SEARCH_SCOPE_LEVEL

    def identities(self) -> list[tuple[str, str]]:
        """每条条件的标识（``level_key`` 用它判「还是这一层吗」）。

        **是「字段 id + 原文」两段** —— 写成 ``list[str]`` 的话声明在说谎。
        """
        return [condition.identity for condition in self._conditions]

    def chip_texts(self) -> list[str]:
        """每条条件在胶囊上写的那句话。"""
        return [condition.chip_text for condition in self._conditions]

    def needs_metadata(self) -> bool:
        """有没有一条必须真读 EXIF 才算得出来（排序判据要问，理由见 ``ordering``）。"""
        return any(condition.field.needs_metadata for condition in self._conditions)

    def snapshot(self) -> tuple[SearchCondition, ...]:
        """交给后台任务的那一份 —— **不可变**，任务在跑的时候用户还能改条件。"""
        return tuple(self._conditions)

    # ---- 写：全部汇到这几个方法 -----------------------------------------------

    def submit(self, condition: SearchCondition) -> None:
        """回车提交一条条件：**正在改哪一条就就地替换它**，否则追加到末尾。

        就地替换是为了面包屑上的顺序不跳（ADR 0040 决策 12）。**下标越界时当追加** ——
        「正在改的那一条」可能已经被别的路径拿掉了，而静默丢弃用户刚敲的条件更糟。
        """
        editing = self._editing
        if editing is not None and 0 <= editing < len(self._conditions):
            self._conditions[editing] = condition
        else:
            self._conditions.append(condition)
        self._editing = None

    def remove(self, position: int) -> bool:
        """撤掉第 ``position`` 枚（点它右端的 ✕）。**越界返回 ``False`` 且什么都不做。**

        **全仓库唯一会挪编辑态下标的地方** —— 删掉第 ``i`` 枚之后后面全部前移一位，
        不跟着挪的话「正在改的那一条」就变成了另一条。撤掉的正是正在改的那条时置
        ``None``：那条已经不在了。
        """
        if not 0 <= position < len(self._conditions):
            return False
        del self._conditions[position]
        editing = self._editing
        if editing == position:
            self._editing = None
        elif editing is not None and editing > position:
            self._editing = editing - 1
        return True

    def pop_last(self) -> bool:
        """撤掉最后一条（⇧Esc）。**清编辑态**：撤的不是正在改的那一条。"""
        if not self._conditions:
            return False
        self._conditions.pop()
        self._editing = None
        return True

    def clear(self) -> None:
        """一条不剩（清搜索框 / 弹掉搜索帧 / 换库）。

        **编辑态与命中数一起清**：条件空了而编辑态还指着第 0 枚的话，下一次回车会把一条
        不存在的条件「就地替换」；命中数留着的话面包屑会写「搜索结果（128）」而其实一条都没有。
        """
        self._conditions.clear()
        self._editing = None
        self._total = None

    def restore(self, conditions: tuple[SearchCondition, ...]) -> None:
        """把某一帧自己那份筛选条件装回来（弹掉搜索帧之后，ADR 0041 决策 2）。

        **只有切回「筛选」那一档才装**：此刻是整库检索的话，条件属于搜索帧而不是底下
        那一帧 —— 装回去就成了「弹掉了搜索帧，条件却还在」。
        """
        self._conditions = list(conditions) if self._scope == SEARCH_SCOPE_LEVEL else []

    def begin_edit(self, position: int) -> bool:
        """点胶囊上的那一枚：把它放回输入框接着改。越界返回 ``False``。"""
        if not 0 <= position < len(self._conditions):
            return False
        self._editing = position
        return True

    def cancel_edit(self) -> None:
        """放弃这一次改动（失焦、Esc、双击空白）。**没在改任何一条时什么都不做** ——
        失焦那一刻用户很可能正在输入一条全新的条件，那时把编辑态清了就是取消了一个
        还没开始的改动，而下一次回车会把它当成「替换」而不是「新增」。
        """
        self._editing = None

    def set_scope(self, scope: str) -> None:
        """换作用域（每个库各记各的，ADR 0040 决策 6）。**条件不清空**：
        这是同一批条件，用户改的只是「它们管到哪儿」。"""
        if scope in SEARCH_SCOPES:
            self._scope = scope

    def set_total(self, total: int | None) -> None:
        """记下这一次命中了多少张（``None`` = 还没查过 / 条件刚变）。"""
        self._total = total
