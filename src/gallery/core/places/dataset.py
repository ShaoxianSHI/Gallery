"""自带的城市库：读 ``resources/places.tsv.gz``，按坐标查最近的城市。

**这里没有任何翻译，也没有任何「这是不是一个城市」的判断** —— 那些全在生成期做完了
（见 ``scripts/build_places_db.py`` 与 ADR 0012）。本模块只做一件事：在两万条
「省 · 城市 · 国家 · 坐标」里找出最近的那一条。

**为什么自带而不是运行时下载：** 反查的难点不在算距离，而在「最近的那个到底算什么」。
中国的区、街道、镇、村在 GeoNames 里都标成了居民点（全球 974 条 ``PPLA4`` 里 777 条
是中国），深圳松岗的照片会得到「松岗」—— 可松岗不是一座城市；而各地把区划标成什么
代码各不相同，光看代码分不出来。源数据里那条「松岗属于深圳」的隶属关系是**写的**，
把它和地名一起在生成期算完，运行时就只剩一次最近邻查询。

于是三件事一并消失：3.3 MB 的首次下载、缓存目录、以及「正在下载城市数据…」那条横幅。
坐标仍然一个都不出本机（与在线反查服务的本质区别，见 ADR 0003）。
"""

from __future__ import annotations

import gzip
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.places.countries import place_label

logger = logging.getLogger(__name__)

#: 最近的城市超过这个距离就不给结论：说「离这里 800 公里的城市」比不说更糟。
MAX_DISTANCE_KM: Final = 300.0

#: 粗网格的格子边长（度）。约 555 公里。
#:
#: 一次反查要把两万条全扫一遍（约 6ms），分组时一次要给几千张照片查，那样光反查就要
#: 一分钟。粗网格把候选缩到查询点周围 ±2 格（≥ 1100 公里），而
#: :data:`MAX_DISTANCE_KM` 只有 300 公里 —— **任何会被采纳的答案必然落在窗口里**，
#: 所以结果与全扫逐字一致，只是快了一个数量级。
GRID_CELL_DEGREES: Final = 5.0

#: 窗口半径（格数）。
GRID_RADIUS: Final = 2

#: 同一坐标上有多条记录时取人口多的那条：源数据里同名的重复记录不少，
#: 而坐标只精确到小数点后四位，靠它们分辨不了「这是同一个地方」。
_SAME_POINT_RADIUS_KM: Final = 1.0

#: 文件的列顺序，解析与生成脚本共用（见 ``scripts/build_places_db.py`` 的 ``COLUMNS``）。
_COLUMN_COUNT: Final = 6


@dataclass(frozen=True, slots=True)
class Place:
    """一次反查的结果。三个字段都是**已经定好的显示名**，不需要再加工。"""

    #: 城市名：国内是中文（``杭州``），境外是当地写法（``Bordeaux``）。
    city: str
    #: 省 / 州。直辖市、香港、澳门没有这一层，为 ``None``。
    region: str | None
    country: str
    distance_km: float

    def label(self) -> str:
        """展示用文案，由大到小：``中国 · 浙江省 · 杭州``（缺的层级自动省略）。

        直辖市、香港、澳门少一层，显示成 ``中国 · 上海``。
        """
        return place_label(self.city, self.region, self.country)


@dataclass(frozen=True, slots=True)
class _City:
    region: str | None
    city: str
    country: str
    population: int
    latitude_rad: float
    longitude_rad: float
    latitude: float
    longitude: float


def is_real_coordinate(latitude: float, longitude: float) -> bool:
    """这两个数是不是一个**真实存在**的位置。

    经纬度**同时为 0** 是「没有位置」的占位写法：相机与写手在定不到 GPS 时会填 0，
    而不是留空。那个点在几内亚湾里（Null Island），世上并不存在 —— 拿它去反查会得到
    一个**自信的错答案**，而不是「不知道」。

    **只判两个同时为零**：纬度 0 是赤道、经度 0 是本初子午线，那两个都是真实存在的位置。
    """
    return not (latitude == 0.0 and longitude == 0.0)


class PlaceIndex:
    """城市索引。加载一次，之后可以反复按坐标查最近城市。"""

    def __init__(
        self, cities: Iterable[_City], *, max_distance_km: float = MAX_DISTANCE_KM
    ) -> None:
        self._cities = tuple(cities)
        self._max_distance_km = max_distance_km
        self._grid = _build_grid(self._cities)

    @property
    def city_count(self) -> int:
        return len(self._cities)

    @classmethod
    def load(cls, path: Path | None = None) -> PlaceIndex | None:
        """读自带的城市库；文件缺失或损坏时返回 ``None``。

        返回 ``None`` 而不是抛异常，是因为这个文件是**随包分发的**：读不到说明
        安装坏了，那是程序缺陷，但不该让整个地点分组跟着崩 —— 调用方据此降级为
        「不显示地点」，其余功能照常。
        """
        target = paths.places_db_file() if path is None else Path(path)
        try:
            text = gzip.decompress(target.read_bytes()).decode("utf-8")
        except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError) as exc:
            logger.warning("城市库不可用，将按缺失处理：%s", exc)
            return None
        cities = list(_parse(text))
        if not cities:
            logger.warning("城市库是空的，将按缺失处理：%s", target)
            return None
        return cls(cities)

    def nearest(self, latitude: float, longitude: float) -> Place | None:
        """返回最近的城市；附近没有记录时返回 ``None``。

        「附近」是 :data:`MAX_DISTANCE_KM`。再近也不会为了凑出一条结果而编一个地名 ——
        海上、太空、或者一个偏远的山区村落，说不出城市比说错更好。

        查询点为 ``(0, 0)`` 时直接返回 ``None``：那是「没有位置」的占位写法，
        见 :func:`is_real_coordinate`。

        同一点附近有多条记录时**取人口多的那条**：城市总比它内部的某个区块人多
        （上海「黄浦」与「上海」就落在同一个坐标上）。先取最近、再在这个小窗口里
        按人口消歧 —— 两步分开做，结果与遍历顺序无关。
        """
        if not is_real_coordinate(latitude, longitude):
            return None
        latitude_rad = math.radians(latitude)
        longitude_rad = math.radians(longitude)
        scored = [
            (distance, city)
            for distance, city in (
                (_haversine_km(latitude_rad, longitude_rad, c), c)
                for c in self._candidates(latitude, longitude)
            )
            if distance <= self._max_distance_km
        ]
        if not scored:
            return None
        nearest_distance = min(item[0] for item in scored)
        same_point = [
            item for item in scored if item[0] <= nearest_distance + _SAME_POINT_RADIUS_KM
        ]
        distance, city = min(same_point, key=lambda item: (-item[1].population, item[0]))
        return Place(
            city=city.city,
            region=city.region,
            country=city.country,
            distance_km=distance,
        )

    def _candidates(self, latitude: float, longitude: float) -> Iterable[_City]:
        """查询点附近粗网格里的那些城市（见 :data:`GRID_CELL_DEGREES`）。"""
        cell_latitude = math.floor(latitude / GRID_CELL_DEGREES)
        cell_longitude = math.floor(longitude / GRID_CELL_DEGREES)
        picked: list[_City] = []
        for offset_latitude in range(-GRID_RADIUS, GRID_RADIUS + 1):
            for offset_longitude in range(-GRID_RADIUS, GRID_RADIUS + 1):
                bucket = self._grid.get(
                    (cell_latitude + offset_latitude, cell_longitude + offset_longitude)
                )
                if bucket:
                    picked.extend(self._cities[index] for index in bucket)
        return picked


def _parse(text: str) -> Iterable[_City]:
    """解析城市库。跳过注释行与坏行 —— 缺一列就丢这一条，不让整份文件作废。

    **坏行必须是「丢掉这一条」而不是「抛出去」**：这一段的调用方
    :meth:`PlaceIndex.load` 只把读文件与解压包在 ``try`` 里，解析在它外面；而它
    的承诺是「文件缺失或损坏时返回 ``None``」。一个坏行若能一路抛出来，整个地点
    分组就会崩在一个字都读不出来的坏数据上。
    """
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < _COLUMN_COUNT:
            continue
        region, city, country, latitude, longitude, population = fields[:_COLUMN_COUNT]
        if not city or not country:
            continue
        try:
            lat = float(latitude)
            lon = float(longitude)
        except ValueError:
            continue
        if not is_real_coordinate(lat, lon):
            continue  # 坐标为 (0,0) 的坏行：源数据里有十几条，见 is_real_coordinate
        yield _City(
            region=region or None,
            city=city,
            country=country,
            population=_population(population),
            latitude_rad=math.radians(lat),
            longitude_rad=math.radians(lon),
            latitude=lat,
            longitude=lon,
        )


def _population(raw: str) -> int:
    """人口列 → 整数；读不出来就算 ``0``（它只用来在同坐标的多条记录里挑大的那条）。

    **不能用 ``str.isdigit()`` 当判据**：它对上标数字（``"²".isdigit()`` 为真）
    也返回 ``True``，而 ``int("²")`` 抛 ``ValueError`` —— 守卫与转换说的不是同一件事，
    于是一条坏行能把整个城市库顶掉。直接转换、接住异常，判据与转换始终是同一次。
    """
    try:
        return int(raw)
    except ValueError:
        return 0


def _build_grid(cities: Sequence[_City]) -> dict[tuple[int, int], list[int]]:
    """经纬度 → 城市下标。一座城市只落在一个格子里，查的时候看周围几格。"""
    grid: dict[tuple[int, int], list[int]] = {}
    for index, city in enumerate(cities):
        key = (
            math.floor(city.latitude / GRID_CELL_DEGREES),
            math.floor(city.longitude / GRID_CELL_DEGREES),
        )
        grid.setdefault(key, []).append(index)
    return grid


def _haversine_km(latitude_rad: float, longitude_rad: float, city: _City) -> float:
    delta_latitude = city.latitude_rad - latitude_rad
    delta_longitude = city.longitude_rad - longitude_rad
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_rad) * math.cos(city.latitude_rad) * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(haversine)))


__all__ = [
    "GRID_CELL_DEGREES",
    "GRID_RADIUS",
    "MAX_DISTANCE_KM",
    "Place",
    "PlaceIndex",
    "is_real_coordinate",
]
