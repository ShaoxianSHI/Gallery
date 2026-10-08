"""地点反查那一遍：按坐标离线补全库里的国家 / 省州 / 城市（ADR 0010）。

它只服务 :meth:`~gallery.core.library.facade.Library.resolve_places` 与
:meth:`~gallery.core.library.facade.Library.photo_facets` 的「补齐」那一段，是一组纯函数：
**删掉缓存里那一行就会整体重算**，所以「查不到也是结论」这条判据必须完整。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.batch import BatchMetadataSource, read_facets
from gallery.core.metadata.facets import (
    PhotoFacets,
    mark_place_resolved,
    with_resolved_place,
)
from gallery.core.places.dataset import is_real_coordinate

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver, Status


def _has_coordinates(facets: PhotoFacets) -> bool:
    """这张照片有没有一个**能用来反查**的坐标。经纬度同时为 0 不算：那是相机定不到 GPS 时的占位
    写法（见 :func:`gallery.core.places.dataset.is_real_coordinate`），否则这些照片每开一次库都
    会被送去反查一次，而每次都得到同一个来自几内亚湾的错答案。
    """
    if facets.latitude is None or facets.longitude is None:
        return False
    return is_real_coordinate(facets.latitude, facets.longitude)


def _needs_place(facets: PhotoFacets) -> bool:
    """有没有坐标、却还有地点字段是空的 —— 那种行值得再反查一次。判据是「**还缺**」而不是
    「完全没有」：文件里只写了城市、省州还是空的，那就仍然该用反查补上。
    """
    if not _has_coordinates(facets):
        return False
    return facets.city is None or facets.region is None or facets.country is None


def _needs_lookup(facets: PhotoFacets | None) -> bool:
    """这一行还有没有反查的余地（索引里那一遍要不要理它）。四种情况从头到尾都要说清楚，少一种就
    会出现「每开一次库就重算一遍」：没有有效行（从没读过或文件被改过）→ 要读 EXIF；有坐标但
    没查过 → 要反查；没坐标 → **永远**没得反查，别为它起一次 EXIF；查过 → 已经有结论了
    （哪怕结论是「附近没有城市」），不再问。
    """
    if facets is None:
        return True
    if not _has_coordinates(facets):
        return False
    return not facets.place_checked


def _resolved(facets: PhotoFacets, resolver: PlaceResolver, status: Status | None) -> PhotoFacets:
    """按坐标补地点，并记下「这次查过了」。查不到就原样返回地点字段 —— 那是结论，不是错误。
    数据没下好时反查器会说自己没准备好（``ready`` 为假），那就**不记**：下次还得再来一遍。
    """
    if not _needs_place(facets):
        return facets
    place = resolver.place_for(facets.latitude, facets.longitude, status=status)  # type: ignore[arg-type]
    return mark_place_resolved(with_resolved_place(facets, place), usable=resolver.ready)


def _resolve_batch(
    index: FacetIndex,
    refs: Sequence[PhotoRef],
    *,
    resolver: PlaceResolver | None,
    status: Status | None,
    source: BatchMetadataSource | None,
    should_cancel: Callable[[], bool] | None,
    on_work: Callable[[int], None] | None = None,
) -> int:
    """把这一批里「还没有地点结论」的照片补齐，返回反查了几张。已有结论的行直接跳过，**连 EXIF
    都不读** —— 这是第二次运行几乎不要钱的原因。``on_work`` 在这一批真有活干时叫。
    """
    cached = index.lookup(refs)
    todo = [ref for ref in refs if _needs_lookup(cached.get(ref.path))]
    if not todo:
        return 0
    if on_work is not None:
        on_work(len(todo))
    fresh = read_facets(
        [ref.path for ref in todo],
        source=source,
        should_cancel=should_cancel,
    )
    resolved = 0
    rows: list[tuple[PhotoRef, PhotoFacets]] = []
    for ref in todo:
        facets = fresh.get(ref.path)
        if facets is None:
            continue  # 压根没读出来：下次还要再试，不进索引
        if _needs_place(facets):
            if resolver is not None:
                facets = _resolved(facets, resolver, status)
                resolved += 1
        elif _has_coordinates(facets):
            # guard: allow-long-comment（判据是「已经齐全」而不是「查过」，差一条就永久固定）
            # **文件自己就写全了地点**（Lightroom 导出的 XMP 如此）：那份地点同样是结论而且更准，
            # 照 ADR 0010「文件里写了就以文件为准」记下来就是。判据是「**已经齐全**」而不是
            # 「查过」—— 地点还缺着时不能记，否则等于把这张照片永久固定在「未知」上。
            facets = mark_place_resolved(facets)
        rows.append((ref, facets))
    index.store(rows)
    return resolved


def _offset_progress(
    offset: int, on_progress: Callable[[int, int], None]
) -> Callable[[int, int], None]:
    """把「这一批的进度」翻译成「整次读取的进度」。"""

    def report(done: int, total: int) -> None:
        on_progress(offset + done, offset + total)

    return report
