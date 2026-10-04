"""把一层照片按元数据字段分组、排序。

纯函数：给它照片列表、字段和元数据快照，得出网格该显示什么。**不读文件、不碰缓存**，
因此可以直接单测，也不必知道自己被界面放在哪条线程上。

两条约定（见 ADR 0009）：

- **没值的排在最后**，正序逆序都一样 —— 一堆没写拍摄时间的照片不应该在「时间升序」里
  霸占最前面。
- **组本身也有顺序**：时间类新 → 旧，其余按名称升序。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.core.metadata.facets import Facet, PhotoFacets, SortValue, input_for

#: 没有元数据时的占位：任何照片都能用它算 key，只是排到最后。
EMPTY_FACETS = PhotoFacets()


@dataclass(frozen=True, slots=True)
class SortOrder:
    """组内（未分组时是整层）的排序方式。"""

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

    **没值的一律沉到最后**，正序逆序都一样 —— 一堆没写拍摄时间的照片不该在「时间
    升序」里霸占最前面；而逆序时把它们放到最前面同样没道理。

    同值时按文件名兜底：同一秒拍到的几十张照片也得有个稳定、可预期的先后，
    否则每次重排网格都要抖一下。
    """
    facets = values if values is not None else {}
    if order.facet is None:
        return sorted(photos, key=lambda ref: ref.name.casefold())
    # **每张照片的排序值只算一次。** 原本分两步：分区循环里问一遍「有没有值」，
    # 排的时候在 key 里再问一遍 —— 也就是每张两次。`facet.sort` 要组装
    # ``FacetInput``、查一次映射、取一次字段（拍摄时间还要 ``parse_capture_time``），
    # 五万张的库每次重排就多付五万遍（实测 3000 张 = 6000 次调用）。
    #
    # 所以分区的时候顺手把值**摘出来揣着**，排序直接用它。文件名一起揣上，
    # 「同值按文件名兜底」那条也跟着不用再查。
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
) -> list[GroupRef]:
    """按 ``facet`` 把照片分组，组内按 ``order`` 排好序。"""
    if facet.group is None:
        raise ValueError(f"字段 {facet.id} 不能用来分组")
    facets = values if values is not None else {}
    buckets: dict[str, list[PhotoRef]] = {}
    labels: dict[str, str] = {}
    for ref in photos:
        members = facet.group(input_for(ref, facets.get(ref.path, EMPTY_FACETS), root))
        if not members:
            key, label = "", facet.empty_label
            buckets.setdefault(key, []).append(ref)
            labels.setdefault(key, label)
            continue
        for key, label in members:
            buckets.setdefault(key, []).append(ref)
            labels.setdefault(key, label)

    effective = order if order is not None else SortOrder(facet=None)
    groups = [
        GroupRef(
            facet=facet.id,
            key=key,
            label=labels[key],
            photos=tuple(sort_photos(members, order=effective, root=root, values=facets)),
        )
        for key, members in buckets.items()
    ]
    groups.sort(key=lambda group: _group_sort_key(group, facet))
    return groups


def _group_sort_key(group: GroupRef, facet: Facet) -> tuple[bool, float | str]:
    """组的排序键：没有值的那个组永远在最后，正序逆序都一样。"""
    if not group.key:
        return (True, 0.0)
    value = facet.group_order(group.key) if facet.group_order is not None else group.key
    # 字段自己声明了方向（时间类新 → 旧），不用在这里猜。
    if facet.newest_first and isinstance(value, float):
        value = -value
    return (False, value)


def flatten_groups(groups: Sequence[GroupRef]) -> list[PhotoRef]:
    """把若干组的成员摊平成一份照片列表（按路径去重）。

    按关键词分组时一张照片属于多个组，摊平后要按路径去重，否则组视图里会出现重影。

    **只收组，不收那一层照片**：原来这里还挂着一个 ``photos`` 形参，可它从头到尾没被读
    过一次 —— 签名承诺了一个并不存在的过滤（原意大概是「只保留还留在这一层里的那些」），
    而调用方照着那个承诺传进来，得到的是「全都要」。一个不生效的形参比没有形参更糟：
    它让人以为这函数会筛，于是写出对着它写的代码，然后发现筛不掉。把形参去掉，
    承诺与行为就又一致了。
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
