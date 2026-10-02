"""按坐标给分组 / 排序用的地点。

元数据面板早就有一套按坐标反查的机制（ADR 0003，离线、不外传坐标）。分组要的是
同一份结论的**可比较版本**：国家、省州、城市三个字段各自成组。两者必须用同一次反查 ——
照片明明在面板上写着「Bordeaux · France」，分组却一片「未知」，那是同一个功能的两半。

为什么放在 ``core.places`` 而不是 ``core.metadata``：反查依赖自带的城市库，
那是 ``places`` 的事；``metadata`` 只管「照片里写了什么」。

**城市库随应用分发，用到才加载** —— 首次用时读一次那份 gzip（约 380 KB）建索引，
之后同一坐标只查一次（一次旅行的几百张照片共用一个坐标）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gallery.core.places.dataset import Place, PlaceIndex, is_real_coordinate

logger = logging.getLogger(__name__)

#: 反查一次要不要报状态（「正在加载城市库…」）。
Status = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ResolvedPlace:
    """一次反查的结论，字段与 ``PhotoFacets`` 里的三个地点字段同名同序。"""

    city: str | None = None
    region: str | None = None
    country: str | None = None
    #: 坐标离最近的城市有多远（公里）。面板要靠它说清「这只是最近的城市」。
    distance_km: float | None = None


class PlaceResolver(Protocol):
    """按坐标给出地点。``core.metadata.facets`` 只认这个形状，不认城市库。"""

    def place_for(
        self, latitude: float, longitude: float, *, status: Status | None = None
    ) -> ResolvedPlace | None:
        """查不到（附近没有城市记录）返回 ``None``；数据不可用时也返回 ``None``。"""
        ...

    @property
    def ready(self) -> bool:
        """城市库是否可用 —— **用来区分「查了没有」与「没查成」**。

        两种情况 :meth:`place_for` 都返回 ``None``，但含义完全不同：库齐了还查不到，
        那是一条结论，可以记进索引；库读不出来（安装损坏），**这次根本没查成**，
        记下来就等于把「未知」永久钉死（见 ADR 0010）。调用方在
        :meth:`place_for` **返回之后**问这个属性，那时的值才是这次查询的真实情况
        （懒加载在这一步已经试过了）。
        """
        ...


class LazyPlaceResolver:
    """用到才加载的地点反查器。

    **加载失败不抛异常，也不联网**：城市库是随包分发的只读资源，读不到只说明这个
    安装坏了。那是程序缺陷，但不该让地点分组整个不可用 —— 降级为「不显示地点」，
    其余功能照常，并且如实记进日志。
    """

    def __init__(self, index: PlaceIndex | None = None, *, path: Path | None = None) -> None:
        """
        :param index: 已经加载好的索引（交给元数据面板复用，省一次加载）。
        :param path: 城市库路径，仅测试用；默认走 :func:`gallery.core.paths.places_db_file`。
        """
        self._index = index
        self._path = path
        #: 同一批照片常常共用一个坐标（一次旅行几百张），反查过一次就别再问。
        self._cache: dict[tuple[float, float], ResolvedPlace | None] = {}
        #: 城市库这一轮**试过没有**。试过且失败就不再试第二次。
        self._load_attempted = index is not None

    @property
    def index(self) -> PlaceIndex | None:
        """已经加载好的索引（交给元数据面板复用，省一次加载）。"""
        return self._index

    @property
    def ready(self) -> bool:
        """城市库是否可用（能不能给出反查结论）。"""
        return self._index is not None

    def place_for(
        self, latitude: float, longitude: float, *, status: Status | None = None
    ) -> ResolvedPlace | None:
        # 约 11 米的粒度（4 位小数）：消费级 GPS 的抖动就在这个量级，同一次拍摄
        # 的几十张照片坐标几乎相同；不同城市相差以公里计，不会被这个粒度抹平。
        if not is_real_coordinate(latitude, longitude):
            # 定不到 GPS 的相机填的是 0 而不是空。把它当成「附近没有城市」记下来，
            # 等于给这些照片永久钉上一个来自几内亚湾的结论。
            return None
        key = (round(latitude, 4), round(longitude, 4))
        if key in self._cache:
            return self._cache[key]
        index = self._ensure_index(status=status)
        if index is None:
            # 城市库不可用时**不缓存「查不到」**：这次没数据、下次有了，不该还停在未知。
            return None
        result = _to_resolved(index.nearest(latitude, longitude))
        self._cache[key] = result
        return result

    def _ensure_index(self, *, status: Status | None) -> PlaceIndex | None:
        """城市库**最多试一次**。

        试过一次还没成就不再试：城市库是随包分发的只读文件，它要么在、要么不在，
        同一个进程里它不会自己变好。曾经这里是「缓存未命中就再读一次」，于是
        城市库缺失时每张照片都要重读重解析一遍那份 ~380 KB 的 gzip、写一条一样的
        警告、还往横幅里写一次「正在加载城市库…」—— 300 张照片就是 300 遍。
        真想重试就换一个 ``LazyPlaceResolver``，而它本来就只在窗口打开时造一次。
        """
        if self._index is not None:
            return self._index
        if self._load_attempted:
            return None
        self._load_attempted = True
        if status is not None:
            status("正在加载城市库…")
        self._index = PlaceIndex.load(self._path)
        return self._index


def _to_resolved(place: Place | None) -> ResolvedPlace | None:
    return (
        None
        if place is None
        else ResolvedPlace(
            city=place.city,
            region=place.region,
            country=place.country,
            distance_km=place.distance_km,
        )
    )


__all__ = ["LazyPlaceResolver", "PlaceResolver", "ResolvedPlace", "Status"]
