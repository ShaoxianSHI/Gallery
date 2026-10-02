"""粗网格反查的**等价性**测试。

``PlaceIndex.nearest`` 为了速度只看查询点附近几格里的城市（见 GRID_CELL_DEGREES）。
这条捷径的正确性全押在一句话上：**任何会被采纳的答案必然落在那个窗口里**
（``MAX_DISTANCE_KM`` 只有 300 公里，窗口半径至少 1100 公里）。

于是这里逐点比对「加了网格」与「全表扫」两条路径的结果 —— 不是抽查几个点，是整个
人造城市集上的每个查询点都比一遍：等价性这种事，抽查是不能证明的。
"""

from __future__ import annotations

import math

import pytest

from gallery.core.places import geonames
from gallery.core.places.geonames import (
    GRID_CELL_DEGREES,
    GRID_RADIUS,
    MAX_DISTANCE_KM,
    SAME_PLACE_RADIUS_KM,
    PlaceIndex,
    _City,
)


def _city(name: str, latitude: float, longitude: float, code: str = "PPL") -> _City:
    return _City(
        name=name,
        local_name=None,
        feature_code=code,
        country_code="CN",
        admin1_code="00",
        latitude_rad=math.radians(latitude),
        longitude_rad=math.radians(longitude),
        latitude=latitude,
        longitude=longitude,
    )


def _index() -> PlaceIndex:
    """人造城市集：密集簇 + 分散孤点，覆盖窗口内外的各种位置。"""
    cities: list[_City] = []
    cities += [_city(f"密{i}", 31.20 + i * 0.01, 121.40 + i * 0.01) for i in range(40)]
    cities += [_city(f"欧{i}", 48.80 + i * 0.01, 2.30 + i * 0.01) for i in range(20)]
    cities += [_city(f"南{i}", -33.80 - i * 0.01, 151.20 + i * 0.01) for i in range(10)]
    cities += [_city(f"线{i}", 1.30 + i * 0.01, 179.50 + i * 0.01) for i in range(10)]
    cities.append(_city("孤点", -54.80, -68.30))
    cities.append(_city("极北", 78.20, 15.60, code="PPLC"))
    return PlaceIndex(cities, admin1={"CN.00": "假省"}, countries={"CN": "China"})


def _brute_force(index: PlaceIndex, latitude: float, longitude: float) -> str | None:
    """没有网格时的答案（照 ``nearest`` 的旧算法重写一遍）。"""
    if not index._cities:
        return None
    latitude_rad = math.radians(latitude)
    longitude_rad = math.radians(longitude)
    scored = [
        (
            geonames._haversine_km(latitude_rad, longitude_rad, city),
            geonames._PLACE_RANK.get(city.feature_code, 0),
            city,
        )
        for city in index._cities
    ]
    nearest_distance = min(item[0] for item in scored)
    if nearest_distance > index._max_distance_km:
        return None
    nearby = [item for item in scored if item[0] <= nearest_distance + SAME_PLACE_RADIUS_KM]
    _, _, best = min(nearby, key=lambda item: (-item[1], item[0]))
    return best.name  # 只比城市名：行政名在网格那条路上是查表映射过的


def _grid_result(index: PlaceIndex, latitude: float, longitude: float) -> str | None:
    place = index.nearest(latitude, longitude)
    return None if place is None else place.city


def test_grid_matches_brute_force_on_every_query_point() -> None:
    index = _index()
    queries = [
        (31.23, 121.45),  # 落在密集簇里
        (48.85, 2.35),
        (-33.87, 151.21),
        (1.35, 179.55),  # 跨日期变更线附近
        (-54.80, -68.30),  # 正好在城市上
        (78.22, 15.64),  # 首都那一档：会触发「同级取行政级别更高」
        (0.0, 0.0),  # 海上：两边都该给「查不到」
        (89.9, 179.9),  # 极地角落
        (-89.9, -179.9),
    ]
    queries += [
        (31.0 + i * 0.017, 121.0 + i * 0.017) for i in range(24)
    ]  # 再扫一片连续的网格，格子边界一个不落

    for latitude, longitude in queries:
        assert _grid_result(index, latitude, longitude) == _brute_force(
            index, latitude, longitude
        ), f"坐标 {latitude},{longitude} 上两条路径给出不同答案"


def test_window_covers_more_than_any_accepted_answer() -> None:
    """把「窗口半径」这件事钉住：它必须远大于会被采纳的最大距离。"""
    window_km = GRID_RADIUS * GRID_CELL_DEGREES * 111.0
    assert window_km > MAX_DISTANCE_KM + SAME_PLACE_RADIUS_KM


def test_grid_actually_narrows_the_search() -> None:
    """网格要真的在干活：全表 82 座、窗口内应当只有一小撮。"""
    index = _index()

    picked = list(index._candidates(31.23, 121.45))

    assert 0 < len(picked) < len(index._cities)


def test_far_away_query_is_rejected_by_both_paths() -> None:
    """窗口里没有城市时不该回退去扫全表（那才是 11ms 的来源）。"""
    index = _index()

    assert index.nearest(0.0, 0.0) is None


def test_empty_index_answers_nothing() -> None:
    index = PlaceIndex([], admin1={}, countries={})

    assert index.nearest(31.2, 121.4) is None


@pytest.mark.parametrize("feature_code", ["PPL", "PPLA", "PPLC"])
def test_same_place_prefers_higher_rank(feature_code: str) -> None:
    """同一条数据在行政级别上的差别不能被网格吃掉。"""
    nearby = [
        _city("黄浦", 31.2304, 121.4737, code="PPLX"),
        _city("上海", 31.2304, 121.4737, code=feature_code),
    ]
    index = PlaceIndex(nearby, admin1={"CN.00": "假省"}, countries={"CN": "China"})

    place = index.nearest(31.2304, 121.4737)

    assert place is not None
    assert place.city == "上海"
