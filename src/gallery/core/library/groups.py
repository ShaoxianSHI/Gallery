"""把一层照片按元数据字段分组、排序。

纯函数：给它照片列表、字段和元数据快照，得出网格该显示什么。**不读文件、不碰缓存**，
因此可以直接单测，也不必知道自己被界面放在哪条线程上。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path

from gallery.core.events.store import EventRef
from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.core.metadata.facets import Facet, PhotoFacets, SortValue, input_for

#: 没有元数据时的占位：任何照片都能用它算 key，只是排到最后。
EMPTY_FACETS = PhotoFacets()


@dataclass(frozen=True, slots=True)
class SortOrder:
    """组内（未分组时是整层）的排序方式。

    ``descending`` 就是字面的反向。**所有字段的默认方向统一是升序**（名称 A→Z、数字小→大、
    时间旧→新），所以「逆序」翻过来就是「降序」；不需要每个字段各声明一次自己的方向
    —— 那套「自然方向」连同 :attr:`Facet.newest_first` 一起删掉了（ADR 0049）。
    """

    facet: Facet | None
    descending: bool = False

    def value(
        self, ref: PhotoRef, root: Path, values: Mapping[Path, PhotoFacets]
    ) -> SortValue | None:
        """这张照片的排序值；没有值就是 ``None``（一律排在最后，正序逆序都一样）。"""
        if self.facet is None:
            return ref.name.casefold()
        return self.facet.sort(input_for(ref, values.get(ref.path, EMPTY_FACETS), root))


def sort_photos(
    photos: Sequence[PhotoRef],
    *,
    order: SortOrder,
    root: Path,
    values: Mapping[Path, PhotoFacets] | None = None,
) -> list[PhotoRef]:
    """按 ``order`` 排一组照片，返回新列表（不改动入参）。

    **没值的一律沉到最后**，正序逆序都一样 —— 一堆没写拍摄时间的照片不该在「时间升序」里
    霸占最前面，逆序时把它们提到最前面同样没道理（ADR 0009）。同值时按文件名兜底，
    否则同一秒拍到的几十张照片每次重排网格都要抖一下。
    """
    facets = values if values is not None else {}
    if order.facet is None:
        return sorted(photos, key=lambda ref: ref.name.casefold(), reverse=order.descending)
    # **每张照片的排序值只算一次**：分区时问一遍、排序的 key 里再问一遍等于每张两次，
    # 五万张的库每次重排就多付五万遍（实测 3000 张 = 6000 次调用）。所以值顺手揣着。
    known: list[tuple[SortValue, str, PhotoRef]] = []
    unknown: list[PhotoRef] = []
    for ref in photos:
        value = order.value(ref, root, facets)
        if value is None:
            unknown.append(ref)
        else:
            known.append((value, ref.name.casefold(), ref))
    known.sort(key=lambda item: (item[0], item[1]), reverse=order.descending)
    unknown.sort(key=lambda ref: ref.name.casefold())
    return [ref for _, _, ref in known] + unknown


def build_groups(
    photos: Sequence[PhotoRef],
    *,
    facet: Facet,
    root: Path,
    order: SortOrder | None = None,
    values: Mapping[Path, PhotoFacets] | None = None,
    event_values: Mapping[Path, Sequence[EventRef]] | None = None,
) -> list[GroupRef]:
    """按 ``facet`` 把照片分组，组内按 ``order`` 排好序。

    ``event_values`` 是「照片属于哪些事件」那一份，**只给 :data:`FACET_EVENT` 看**（ADR 0043
    决策 7）。分组的算法一个字没为它改 —— 多归属的那层循环本来就是为关键词写的。

    **事件分组认领封面靠的是组键比对**（ADR 0048 决策 7）：一张照片属于好几个事件，
    它带的那几份封面各属于其中一个（组键 = 日期#事件 id），只有组键等于当前这个键的那份
    才是这一组的。**不把 id 从组键里解析出来** —— 那样「组键怎么拼」就有两处会漂。
    """
    if facet.group is None:
        raise ValueError(f"字段 {facet.id} 不能用来分组")
    facets = values if values is not None else {}
    events = event_values if event_values is not None else {}
    buckets: dict[str, list[PhotoRef]] = {}
    labels: dict[str, str] = {}
    # 组键 → 这一组指定的封面路径。认领条件是组键对得上（理由见下面那个循环里的注释）。
    covers: dict[str, Path] = {}
    for ref in photos:
        members = facet.group(
            input_for(
                ref, facets.get(ref.path, EMPTY_FACETS), root, tuple(events.get(ref.path, ()))
            )
        )
        if not members:
            key, label = "", facet.empty_label
            buckets.setdefault(key, []).append(ref)
            labels.setdefault(key, label)
            continue
        for key, label in members:
            buckets.setdefault(key, []).append(ref)
            labels.setdefault(key, label)
        # 只有组键对得上的那份封面才是这一组的（理由见上面那句 docstring）
        for event_ref in events.get(ref.path, ()):
            if event_ref.cover_path:
                for key, _label in members:
                    if event_ref.group_key == key:
                        covers[key] = Path(event_ref.cover_path)

    effective = order if order is not None else SortOrder(facet=None)
    groups = [
        GroupRef(
            facet=facet.id,
            key=key,
            label=labels[key],
            photos=tuple(sort_photos(members, order=effective, root=root, values=facets)),
            pinned_cover=_pinned_cover(covers.get(key), members),
        )
        for key, members in buckets.items()
    ]
    # guard: allow-long-comment（方向的两条约束都在这一段，少一行就会写错其中一条）
    # 组卡片的方向**跟着「逆序」一起翻**（ADR 0049）—— 它原先只由字段自己声明，于是「逆序」
    # 这一档对组卡片完全无效，而同一层里照片翻了、组卡片没翻，读起来像两样东西没对齐。
    # 没值的那一组单独摘出来接在末尾：``reverse`` 会把排序键的两端对调，靠键的大小表达
    # 「永远沉底」在两个方向下不可能同时成立（与 :func:`sort_photos` 同一处理）。
    descending = order.descending if order is not None else False
    valued: list[tuple[float | str, GroupRef]] = []
    unvalued: list[GroupRef] = []
    for group in groups:
        value = _group_value(group, facet) if group.key else None
        if value is None:
            unvalued.append(group)
        else:
            valued.append((value, group))
    valued.sort(key=itemgetter(0), reverse=descending)
    unvalued.sort(key=lambda group: group.label.casefold())
    return [group for _, group in valued] + unvalued


def _pinned_cover(wanted: Path | None, members: Sequence[PhotoRef]) -> PhotoRef | None:
    """把一个封面**路径**解析成组内那一张引用；解析不出就是 ``None``。

    **解析不出时宁可不钉**（而不是拿第一张顶上）：第一张本来就是 :attr:`GroupRef.cover`
    的兜底，钉上去只会让「这一组有封面」与「没有」在数据上分不开，而界面要靠这个区别
    决定「清除封面」那一行摆不摆。

    组内路径比对走 ``str()``：``PhotoRef.path`` 是同一个 ``Path`` 对象，比对两次是常数。
    """
    if wanted is None:
        return None
    target = str(wanted)
    return next((ref for ref in members if str(ref.path) == target), None)


def _group_value(group: GroupRef, facet: Facet) -> float | str | None:
    """组的排序键。**永远升序**，方向由调用方的 ``reverse`` 给。``None`` = 没有值，沉底。

    方向原先是在这里把时间类字段的键取负实现的（当时的 :attr:`Facet.newest_first`），
    而那一层与「逆序」无关 —— 用户翻了照片的顺序、组卡片却没动。方向因此上移到调用方，
    这里只负责给出可比的值。
    """
    return facet.group_order(group.key) if facet.group_order is not None else group.key


def flatten_groups(groups: Sequence[GroupRef]) -> list[PhotoRef]:
    """把若干组的成员摊平成一份照片列表（按路径去重）。

    按关键词分组时一张照片属于多个组，不去重组视图里会出现重影。
    """
    seen: set[Path] = set()
    result: list[PhotoRef] = []
    for group in groups:
        for ref in group.photos:
            if ref.path in seen:
                continue
            seen.add(ref.path)
            result.append(ref)
    return result


__all__ = [
    "EMPTY_FACETS",
    "SortOrder",
    "build_groups",
    "flatten_groups",
    "sort_photos",
]
