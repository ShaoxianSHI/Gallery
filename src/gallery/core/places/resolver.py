"""按坐标给分组 / 排序用的地点。

元数据面板早就有一套按坐标反查的机制（ADR 0003，离线、不外传坐标）。分组要的是
同一份结论的**可比较版本**：国家、省州、城市三个字段各自成组。两者必须用同一次反查 ——
照片明明在面板上写着「Bordeaux · 法国」，分组却一片「未知」，那是同一个功能的两半。

为什么放在 ``core.places`` 而不是 ``core.metadata``：反查依赖 GeoNames 数据与行政区划
映射，那是 ``places`` 的事；``metadata`` 只管「照片里写了什么」。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gallery.core.places.geonames import (
    Fetcher,
    Place,
    PlaceIndex,
    download_data,
    geo_data_dir,
)

logger = logging.getLogger(__name__)

#: 反查一次要不要报状态（「正在下载城市数据…」）。
Status = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ResolvedPlace:
    """一次反查的结论，字段与 ``PhotoFacets`` 里的三个地点字段同名同序。"""

    city: str | None = None
    region: str | None = None
    country: str | None = None
    #: 坐标离最近城市有多远（公里）。面板要靠它说清「这只是最近的城市」。
    distance_km: float | None = None


class PlaceResolver(Protocol):
    """按坐标给出地点。``core.metadata.facets`` 只认这个形状，不认 GeoNames。"""

    def place_for(
        self, latitude: float, longitude: float, *, status: Status | None = None
    ) -> ResolvedPlace | None:
        """查不到（附近没有城市记录）返回 ``None``；数据不可用时也返回 ``None``。"""
        ...

    @property
    def ready(self) -> bool:
        """数据是否可用 —— **用来区分「查了没有」与「没查成」**。

        两种情况 :meth:`place_for` 都返回 ``None``，但含义完全不同：数据齐了还查不到，
        那是一条结论，可以记进索引；数据还没下好或下载失败，这次根本没查成，
        记下来就等于把「未知」永久钉死（见 ADR 0010）。调用方在
        :meth:`place_for` **返回之后**问这个属性，那时的值才是这次查询的真实情况
        （懒加载在这一步已经试过了）。
        """
        ...


class LazyPlaceResolver:
    """用到才加载、必要时先下载数据，调用方不必操心数据在不在。

    **下载必须是有人在场时发生的事**：数据没就绪时当场下 3.3 MB，并在 ``status``
    里如实说明 —— 悄悄联网下东西、或者默默反查出一堆「未知」，两种都不可接受。
    """

    def __init__(
        self,
        index: PlaceIndex | None = None,
        *,
        directory: Path | None = None,
        download: bool = True,
        fetch: Fetcher | None = None,
    ) -> None:
        """
        :param fetch: 下载用的取数函数（测试注入假实现，测试不得依赖网络）。
        """
        self._index = index
        self._directory = Path(directory) if directory is not None else geo_data_dir()
        self._download = download
        self._fetch = fetch
        #: 同一批照片常常共用一个坐标（一次旅行几百张），反查过一次就别再问。
        self._cache: dict[tuple[float, float], ResolvedPlace | None] = {}
        #: 数据这一轮是否可用；``False`` 时不缓存「查不到」，等下次有机会再试。
        self._usable = index is not None or not download

    @property
    def index(self) -> PlaceIndex | None:
        """已经加载好的索引（交给元数据面板复用，省一次加载）。"""
        return self._index

    @property
    def ready(self) -> bool:
        """数据是否已就绪（能不能给出反查结论）。"""
        return self._index is not None

    def place_for(
        self, latitude: float, longitude: float, *, status: Status | None = None
    ) -> ResolvedPlace | None:
        # 约 11 米的粒度（4 位小数）：消费级 GPS 的抖动就在这个量级，同一次拍摄
        # 的几十张照片坐标几乎相同；不同城市相差以公里计，不会被这个粒度抹平。
        key = (round(latitude, 4), round(longitude, 4))
        if key in self._cache:
            return self._cache[key]
        index = self._ensure_index(status=status)
        if index is None:
            # 数据不可用时**不缓存「查不到」**：这次没数据、下次有了，不该还停在未知。
            return None
        result = _to_resolved(index.nearest(latitude, longitude))
        self._cache[key] = result
        return result

    def _ensure_index(self, *, status: Status | None) -> PlaceIndex | None:
        if self._index is not None:
            return self._index
        if self._download and not PlaceIndex.data_present(self._directory):
            if status is not None:
                status("正在下载城市数据（约 3.3 MB，用于按坐标推断地点）…")
            try:
                download_data(self._directory, fetch=self._fetch)
            except Exception as exc:  # 网络失败不该让分组整体失败
                logger.info("城市数据下载失败：%s", exc)
                self._download = False
                return None
        index = PlaceIndex.load(self._directory)
        if index is None:
            return None
        self._index = index
        self._usable = True
        return index


def _to_resolved(place: Place | None) -> ResolvedPlace | None:
    return (
        None
        if place is None
        else ResolvedPlace(
            city=place.display_city,
            region=place.admin1,
            country=place.country,
            distance_km=place.distance_km,
        )
    )


__all__ = ["LazyPlaceResolver", "PlaceResolver", "ResolvedPlace", "Status"]
