"""新建事件时按**选中的照片**预填目的地与起止日期（纯逻辑，不碰 Qt、不碰文件）。

用户右键「记录为 → 旅行 → 新建…」时，那批照片的拍摄日期与地点**已经说清了**这是哪一趟、
去的哪天；再让他把同样的信息敲一遍只是多一次决定。这里的结论**只是默认值**：推不出来就是空串
（用户自己填），而框里已经改过的字谁也不许覆盖（那一半由 ``ui.panels.event_edit`` 保证）。

**日期取「有拍摄时间的那几张」的最早与最晚**，没有拍摄时间的照片不进这个判断 —— 缺失不该
把结论拉成一个不存在的日子。**目的地取「有地点的那几张」的最低层公共位置**：城市一样就是城市，
城市不同但省州一样才是省州，只剩国家一样才用国家（见 :func:`common_place`）。

**不联网、不反查**：这一层只读索引里已经有的那三列 —— 与元数据面板上显示的地点是同一份数据
（ADR 0010），所以预填出来的目的地与用户在面板上看到的一定是同一个说法。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gallery.core.library.calendar import capture_day
from gallery.core.metadata.facets import PhotoFacets

#: 目的地的候选层级，**由细到粗**：城市 → 省州 → 国家。取第一个「全都一样且都非空」的。
_PLACE_LEVELS: tuple[str, ...] = ("city", "region", "country")


@dataclass(frozen=True, slots=True)
class EventPrefill:
    """新建事件时那几个框的默认值。**空串 = 不预填**（那一栏留给用户）。"""

    started_on: str = ""
    ended_on: str = ""
    destination: str = ""


def prefill_from_photos(facets: Iterable[PhotoFacets]) -> EventPrefill:
    """这一批照片能推出什么。照片的顺序不影响结论 —— 取的是最早 / 最晚与公共层级。"""
    items = list(facets)
    days = sorted({day for day in map(capture_day, items) if day is not None})
    return EventPrefill(
        started_on=days[0].isoformat() if days else "",
        ended_on=days[-1].isoformat() if days else "",
        destination=common_place(items),
    )


def common_place(facets: Iterable[PhotoFacets]) -> str:
    """这批照片**共有**的那一层地点，没有就是空串。

    **只看有地点的那几张** —— 一张没坐标的照片不该把整批拉成「未知」（与日期那一路同一个道理）。

    某一层要算数，**得每张都有、且全都一样**：一张写着杭州、另一张只写了国家，那两张并不共有
    城市，于是往省州那一层看。同名的层级（直辖市）返回城市本身而不是「中国 · 上海」——
    目的地那一栏填的是「去了哪儿」，用户要改的正是这一句。
    """
    located = [item for item in facets if item.city or item.region or item.country]
    for level in _PLACE_LEVELS:
        shared = _shared_level(located, level)
        if shared:
            return shared
    return ""


def _shared_level(items: Sequence[PhotoFacets], level: str) -> str:
    """某一层上「全都一样且都非空」的那个值；否则空串。"""
    values = {getattr(item, level) or "" for item in items}
    return values.pop() if len(values) == 1 and "" not in values else ""


__all__ = [
    "EventPrefill",
    "common_place",
    "prefill_from_photos",
]
