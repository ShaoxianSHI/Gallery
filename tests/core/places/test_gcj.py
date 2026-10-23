"""WGS-84 → GCJ-02：转的是底图那一层偏移，不是 GPS 本身。

**方向也要锁住**：华东华南一带 GCJ 落在 WGS 的东南，所以把 WGS 直接画到 GCJ 底图上，
看到的点会偏西北 —— 用户报的「真实位置在显示位置的东南」就是这一条。
"""

from __future__ import annotations

import math

import pytest

from gallery.core.places.gcj import wgs_to_gcj


def _distance_m(latitude: float, longitude: float, other: tuple[float, float]) -> float:
    """两个坐标之间的大致距离（米）。几百米的量级，等距圆柱近似足够。"""
    delta_latitude = (other[0] - latitude) * 111_320.0
    delta_longitude = (other[1] - longitude) * 111_320.0 * math.cos(math.radians(latitude))
    return math.hypot(delta_latitude, delta_longitude)


def test_known_points_match_the_public_algorithm() -> None:
    """天安门与上海人民广场：对着公开算法算出来的值。"""
    assert wgs_to_gcj(39.9087, 116.3975) == pytest.approx((39.910103, 116.403744), abs=3e-6)
    assert wgs_to_gcj(31.2304, 121.4737) == pytest.approx((31.228458, 121.478223), abs=3e-6)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(39.9087, 116.3975), (31.2304, 121.4737), (23.1066, 113.3245), (30.2458, 120.1500)],
)
def test_the_offset_is_hundreds_of_metres(latitude: float, longitude: float) -> None:
    """不是「差一点点」：几百米，肉眼在地图上就是错了一两个街区。"""
    distance = _distance_m(latitude, longitude, wgs_to_gcj(latitude, longitude))
    assert 300.0 < distance < 800.0


def test_the_gcj_point_is_south_east_in_eastern_china() -> None:
    """上广杭这一带 GCJ 在 WGS 的东南；画反了症状会变成另一种偏移。"""
    latitude, longitude = wgs_to_gcj(31.2304, 121.4737)
    assert latitude < 31.2304
    assert longitude > 121.4737


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(48.8584, 2.2945), (35.6812, 139.7671), (-33.8568, 151.2153)],
)
def test_outside_mainland_is_left_alone(latitude: float, longitude: float) -> None:
    assert wgs_to_gcj(latitude, longitude) == (latitude, longitude)


@pytest.mark.parametrize(
    ("name", "latitude", "longitude"),
    [
        ("香港中环", 22.2819, 114.1582),
        ("香港上水", 22.5010, 114.1280),
        ("澳门半岛", 22.1987, 113.5439),
        ("台北", 25.0330, 121.5654),
        ("高雄", 22.6273, 120.3014),
        ("马祖", 26.1600, 119.9500),
    ],
)
def test_hong_kong_macau_and_taiwan_are_not_shifted(
    name: str, latitude: float, longitude: float
) -> None:
    """港澳台的公开地图是 WGS-84 —— 转了就是凭空偏五百米。"""
    assert wgs_to_gcj(latitude, longitude) == (latitude, longitude), name


@pytest.mark.parametrize(
    ("name", "latitude", "longitude"),
    [
        ("深圳福田", 22.5431, 114.0579),
        ("深圳罗湖", 22.5480, 114.1180),
        ("珠海拱北", 22.2200, 113.5500),
        ("横琴", 22.1300, 113.5300),
        ("厦门", 24.4798, 118.0894),
    ],
)
def test_the_neighbours_across_the_border_are_still_converted(
    name: str, latitude: float, longitude: float
) -> None:
    """香港/澳门只隔一条河：排除区不能大到把深圳、珠海、横琴一起挡在外面。"""
    assert wgs_to_gcj(latitude, longitude) != (latitude, longitude), name
