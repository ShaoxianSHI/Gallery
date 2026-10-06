"""相册模式的导航计算（纯函数，不依赖 Qt）。

面包屑、上一级与「这一层 / 这一格」的标识都在这里 —— 它们最容易出错（相对路径、走出库、三种条目
认得出的字段各不相同），放在这里才能用普通 pytest 覆盖，界面只负责把结果画出来。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gallery.core.library.config import (
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    SEARCH_SCOPE_LEVEL,
    SEARCH_SCOPE_LIBRARY,
)
from gallery.core.library.photos import GridEntry, GroupRef

#: 一层的标识（见 :func:`level_key`）。是元组而不是字符串：只为了能哈希、能当键用。
type LevelKey = tuple[str, ...]

#: ``LevelKey`` 的第一段：整库平铺（一张照片一格，没有相册卡片）。
LEVEL_KIND_FLAT = "flat"

#: ``LevelKey`` 的第一段：相册模式逐层进入（一层里有相册卡片与照片）。
LEVEL_KIND_ALBUMS = "albums"

#: ``LevelKey`` 的第一段：日历那一页（底下是整库，页面上按日期归拢）。
LEVEL_KIND_CALENDAR = "calendar"

#: 网格里一格的标识（见 :func:`entry_key`）。
type EntryKey = tuple[str, ...]

#: :data:`EntryKey` 的第一段：照片与相册（按路径认）。
ENTRY_KIND_PATH = "path"

#: :data:`EntryKey` 的第一段：分组卡片（按字段 + 组标识认）。
ENTRY_KIND_GROUP = "group"

#: :data:`LevelKey` 里插在目录层前面的一段：**正在看检索出来的照片**（ADR 0026）。
#: 它不是目录，因此不与相册或分组混用。
ENTRY_KIND_SEARCH = "search"

#: :data:`LevelKey` 里插在条件前面的一段：**条件正在收窄这一层，而不是取代它**（ADR 0040 决策 2）。
#:
#: **这一段不是可有可无的**：筛选时条件仍要进标识（否则「加一条条件」会被当成「还是这一层」，
#: 焦点恢复会往一个已被滤掉的行上跳），而条件一进标识就与不带条件的那一层长得不一样了。
ENTRY_KIND_FILTER = "filter"


def breadcrumb_parts(root: Path, current: Path) -> list[tuple[str, Path]]:
    """面包屑：``(显示名, 路径)``，从库根一直到当前层。

    ``current`` 不在 ``root`` 之下时只返回库根一项 —— 界面据此回退到库根，而不是画一条走不通
    的路径。
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
    scope: str = SEARCH_SCOPE_LIBRARY,
) -> LevelKey:
    """当前这一层的标识。

    :param browse_mode: ``albums`` 逐层进入，``flat`` 整库平铺，``calendar`` 日历。三者摆着
        的东西不一样，于是是三层，不能互相顶掉对方的记忆。
    :param directory: 相册模式下当前所在目录。
    :param group: 正在看的分组，``(字段 id, 组标识)``；不在分组里就传 ``None``。
    :param conditions: 已生效的筛选条件，每条是 ``(字段 id, 用户敲的原文)``。**两种作用域下都要
        进这个标识**：否则「加一条条件」会被当成「还是这一层」，焦点恢复往一个已经被滤掉的行上跳。
    :param scope: 条件的作用域（ADR 0040）。两种都要进标识，但**进的方式不同** —— 整库检索是
        **取代**了当前层（目录各是各的，全算进去只会让两套毫不相干的条件撞出同一个标识，
        ADR 0026 决策 1），而筛选是**这一层本身**，底下那些目录要留着。

    分组那一层要连**它是从哪一层进来的**一起算进去：``旅行/2024`` 与 ``家庭/2024`` 都可能分
    出一个 ``2024-05``，只按组标识记，回来时就会串到别的相册里去。

    **日历自成一个标识**：它底下装的是整库，与相册模式共用一个的话「还是这一层吗」会在两者
    之间误判成立，于是从日历切到相册时会把上一页停着的那一格恢复回来（ADR 0025 决策 6）。
    """
    if browse_mode == BROWSE_MODE_CALENDAR:
        base: LevelKey = (LEVEL_KIND_CALENDAR,)
    elif browse_mode == BROWSE_MODE_FLAT:
        base = (LEVEL_KIND_FLAT,)
    else:
        base = (LEVEL_KIND_ALBUMS, str(directory))
    if conditions:
        if scope == SEARCH_SCOPE_LEVEL:
            # **层还是那一层**：目录要留着（不留着的话「在相册 A 筛过」与「在相册 B 筛过」会撞成
            # 同一个标识），条件后面也缀一段 :data:`ENTRY_KIND_FILTER` 让它与不带条件的那一层
            # 分得开。
            base = (*base, ENTRY_KIND_FILTER, *conditions)
        else:
            base = (ENTRY_KIND_SEARCH, *base, *conditions)
    if group is None:
        return base
    facet, key = group
    return (ENTRY_KIND_GROUP, *base, facet, key)


def path_key(path: Path) -> EntryKey:
    """一个路径的条目标识。

    相册还只是「刚要进去的那个目录」时（``Esc`` 往上一层退的那一刻），网格上并没有它的卡片
    可问 —— 已知的只有这个路径，而它正是那一格将来会用上的标识。
    """
    return (ENTRY_KIND_PATH, str(Path(path)))


def entry_key(entry: GridEntry) -> EntryKey:
    """这一格的标识：重扫、重排、换完排序之后都认得出是同一条。

    照片与相册按**路径**认（它们在磁盘上有唯一的位置）；分组卡片按「字段 + 组标识」认，
    否则「2024 年」与「2024 年 5 月」这类会互相顶掉。
    """
    if isinstance(entry, GroupRef):
        return (ENTRY_KIND_GROUP, entry.facet, entry.key)
    return path_key(entry.path)


def row_of_entry_key(entries: Sequence[GridEntry], key: EntryKey) -> int:
    """给定这些条目，那一格在第几行；没有就返回 ``-1``（按**标识**找，重排后行号会变）。"""
    for row, entry in enumerate(entries):
        if entry_key(entry) == key:
            return row
    return -1


__all__ = [
    "ENTRY_KIND_FILTER",
    "ENTRY_KIND_GROUP",
    "ENTRY_KIND_PATH",
    "ENTRY_KIND_SEARCH",
    "LEVEL_KIND_ALBUMS",
    "LEVEL_KIND_CALENDAR",
    "LEVEL_KIND_FLAT",
    "EntryKey",
    "LevelKey",
    "breadcrumb_parts",
    "entry_key",
    "level_key",
    "parent_of",
    "path_key",
    "row_of_entry_key",
]
