"""相册模式的导航计算（纯函数，不依赖 Qt）。

面包屑与「上一级」这类路径推导最容易出错（相对路径、走出库、根目录的名称），
所以它们都在这里用普通 pytest 覆盖，界面只负责把结果画出来。

「这一层」与「这一格」的标识也在这里算：返回上一层时要在上一层选中刚离开的那一格，
而网格里摆着的是照片、相册、分组卡片三种东西，它们认得出的字段各不相同
（照片与相册认路径，分组认字段 + 组标识）。把「怎么认」写成纯函数，界面那边就只剩
「记住」与「选中」两件事。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gallery.core.library.photos import GridEntry, GroupRef
from gallery.core.library.session import BROWSE_MODE_FLAT

#: 一层的标识（见 :func:`level_key`）。是元组而不是字符串：只为了能哈希、能当键用。
type LevelKey = tuple[str, ...]

#: 网格里一格的标识（见 :func:`entry_key`）。
type EntryKey = tuple[str, ...]

#: :data:`EntryKey` 的第一段：照片与相册（按路径认）。
ENTRY_KIND_PATH = "path"

#: :data:`EntryKey` 的第一段：分组卡片（按字段 + 组标识认）。
ENTRY_KIND_GROUP = "group"

#: :data:`LevelKey` 里插在目录层前面的一段：**正在看检索出来的照片**（ADR 0026）。
#: 它不是目录，因此单立一种，不与相册或分组混用。
ENTRY_KIND_SEARCH = "search"


def breadcrumb_parts(root: Path, current: Path) -> list[tuple[str, Path]]:
    """面包屑：``(显示名, 路径)``，从库根一直到当前层。

    ``current`` 不在 ``root`` 之下时只返回库根一项 —— 界面据此回退到库根，
    而不是画一条走不通的路径。
    """
    root = Path(root)
    current = Path(current)
    label = root.name or str(root)
    parts: list[tuple[str, Path]] = [(label, root)]
    try:
        relative = current.relative_to(root)
    except ValueError:
        return parts
    for index, name in enumerate(relative.parts):
        parts.append((name, root.joinpath(*relative.parts[: index + 1])))
    return parts


def parent_of(root: Path, current: Path) -> Path | None:
    """上一级目录；已经在库根（或不在库内）时返回 ``None``。"""
    root = Path(root)
    current = Path(current)
    if current == root or root not in current.parents:
        return None
    return current.parent


def level_key(
    browse_mode: str,
    directory: Path | None = None,
    *,
    group: tuple[str, str] | None = None,
    conditions: Sequence[tuple[str, str]] = (),
) -> LevelKey:
    """当前这一层的标识。

    :param browse_mode: ``albums`` 逐层进入，``flat`` 整库平铺。两者摆着的东西不一样
        （前者有相册卡片），于是是两层，不能互相顶掉对方的记忆。
    :param directory: 相册模式下当前所在目录。
    :param group: 正在看的分组，``(字段 id, 组标识)``；不在分组里就传 ``None``。
    :param conditions: 已生效的筛选条件，每条是 ``(字段 id, 用户敲的原文)``
        （见 ``core.library.search.SearchCondition.identity``）。**搜索结果是一层**，
        于是它也得进这个标识：否则「加一条条件」会被当成「还是这一层」，
        选中那一格的行为就与 ADR 0025 决策 6 说的「换了层就谁也不选」对不上了。

    分组那一层要连**它是从哪一层进来的**一起算进去：相册模式下 ``旅行/2024`` 与
    ``家庭/2024`` 都可能按月份分出一个 ``2024-05``，两者是不同的两层，只是碰巧
    组标识相同。只按组标识记，回来时就会串到别的相册里去。
    """
    base: LevelKey = ("flat",) if browse_mode == BROWSE_MODE_FLAT else ("albums", str(directory))
    if conditions:
        # 检索是**横跨整个库**的（ADR 0026 决策 1），因此它不属于任何一个目录层：
        # 底下那些目录各是各的，全算进去只会让两套毫不相干的条件撞出同一个标识。
        base = (ENTRY_KIND_SEARCH, *base, *conditions)
    if group is None:
        return base
    facet, key = group
    return (ENTRY_KIND_GROUP, *base, facet, key)


def path_key(path: Path) -> EntryKey:
    """一个路径的条目标识。

    相册还只是「刚要进去的那个目录」时（``Esc`` 往上一层退的那一刻），网格上并没有
    它的卡片可问 —— 我们知道的只有这个路径，而它正是那一格将来会用上的标识。
    """
    return (ENTRY_KIND_PATH, str(Path(path)))


def entry_key(entry: GridEntry) -> EntryKey:
    """这一格的标识：重扫、重排、换完排序之后都认得出是同一条。

    照片与相册按**路径**认 —— 它们在磁盘上有唯一的位置。分组卡片没有路径，
    按「字段 + 组标识」认：同一个字段下组标识是唯一的，换了字段就一定是另一个组
    （否则「2024 年」与「2024 年 5 月」这类会互相顶掉）。
    """
    if isinstance(entry, GroupRef):
        return (ENTRY_KIND_GROUP, entry.facet, entry.key)
    return path_key(entry.path)


def row_of_entry_key(entries: Sequence[GridEntry], key: EntryKey) -> int:
    """给定这些条目，那一格在第几行；没有就返回 ``-1``。

    按**标识**找而不是按行号：重排与换排序之后行号会变，标识不会。
    """
    for row, entry in enumerate(entries):
        if entry_key(entry) == key:
            return row
    return -1


__all__ = [
    "ENTRY_KIND_GROUP",
    "ENTRY_KIND_PATH",
    "ENTRY_KIND_SEARCH",
    "EntryKey",
    "LevelKey",
    "breadcrumb_parts",
    "entry_key",
    "level_key",
    "parent_of",
    "path_key",
    "row_of_entry_key",
]
