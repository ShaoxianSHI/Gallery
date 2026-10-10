"""筛选 / 检索这一族在窗口上的只读面（ADR 0060 与 :mod:`~gallery.ui.window.state.filters` 分开）。

**四个属性只读**：44 处读一个字都不改（面包屑、日历工具条、排序判据、后台任务的快照），
而写的地方一律调 :class:`FilterState` 的方法 —— 留着 setter 就等于把 ADR 0051
决策 4「写入口必须收进轴」架空。
"""

from __future__ import annotations

from gallery.core.library.config import SEARCH_SCOPE_LIBRARY
from gallery.core.library.search import SearchCondition
from gallery.ui.window.state.filters import FilterState

__all__ = ["FiltersMixin"]


class FiltersMixin:
    """那一族字段在窗口上的只读面，加一句 :meth:`_init_filters`。"""

    def _init_filters(self) -> None:
        """建这一族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._filters = FilterState(SEARCH_SCOPE_LIBRARY)

    @property
    def _conditions(self) -> list[SearchCondition]:
        """已生效的条件，按录入顺序。"""
        return self._filters.conditions

    @property
    def _search_scope(self) -> str:
        """检索条件的作用域：整库检索还是筛选当前这一层。**库级**偏好。"""
        return self._filters.scope

    @property
    def _editing_condition(self) -> int | None:
        """正在改第几条条件（``None`` = 没在改）。"""
        return self._filters.editing

    @property
    def _search_total(self) -> int | None:
        """上一次命中了多少张（面包屑上那枚「搜索结果（128）」）。"""
        return self._filters.total

    def _is_library_search(self) -> bool:
        """条件正**接管**这一层（整库检索），而不是在收窄它。"""
        return self._filters.is_library_search()

    def _is_filtering(self) -> bool:
        """条件正**收窄**这一层，且这一层仍然是它自己。"""
        return self._filters.is_filtering()
