"""WGS-84 → GCJ-02：中国大陆地图的坐标偏移。

EXIF 是卫星定位的 **WGS-84**，中国大陆公开地图依法用 **GCJ-02**（「火星坐标」）。两者
相差几百米，**不是 GPS 被加了偏差** —— 相机记的是真实位置，偏移只发生在底图上。华东
华南一带 GCJ 落在 WGS 的东南，把 WGS 画上去会偏西北；交给谁、要不要转见 ADR 0077。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: 克拉索夫斯基椭球参数（公开的 GCJ-02 算法就用这两个数）。
_A = 6378245.0
_EE = 0.00669342162296594323

#: 中国大陆的粗略范围：(经度最小, 经度最大, 纬度最小, 纬度最大)。
_MAINLAND = (73.66, 135.05, 3.86, 53.55)

#: 港澳的公开地图是 WGS-84，不加偏移。**用多边形而不是方框**：它们和深圳、珠海只隔一条
#: 河/一座关口，方框会把对岸那些城市一起挡在外面。
_HONG_KONG = (
    (22.500, 113.940),
    (22.500, 114.030),
    (22.510, 114.070),
    (22.530, 114.110),
    (22.550, 114.160),
    (22.550, 114.200),
    (22.545, 114.230),
    (22.530, 114.270),
    (22.440, 114.440),
    (22.300, 114.400),
    (22.200, 114.300),
    (22.150, 114.150),
    (22.130, 113.950),
    (22.160, 113.850),
    (22.350, 113.830),
    (22.440, 113.970),
)
_MACAU = (
    (22.220, 113.545),
    (22.220, 113.565),
    (22.190, 113.600),
    (22.110, 113.575),
    (22.100, 113.545),
    (22.160, 113.535),
)

#: 台湾本岛 + 澎湖、马祖。**金门不按坐标排除**：它与厦门只隔几公里，框大了会把厦门挡住。
_TAIWAN = (119.30, 122.10, 21.80, 25.40)
_MATSU = (119.90, 120.10, 25.90, 26.40)


def _inside_polygon(
    latitude: float, longitude: float, polygon: Sequence[tuple[float, float]]
) -> bool:
    """射线法：这个点在不在这块多边形里（每个点是 ``(纬度, 经度)``）。"""
    inside = False
    for index, (lat_a, lon_a) in enumerate(polygon):
        lat_b, lon_b = polygon[(index + 1) % len(polygon)]
        if (lat_a > latitude) != (lat_b > latitude):
            crossing = lon_a + (latitude - lat_a) * (lon_b - lon_a) / (lat_b - lat_a)
            if longitude < crossing:
                inside = not inside
    return inside


def _outside_mainland(latitude: float, longitude: float) -> bool:
    """这个点是不是不在 GCJ-02 的适用范围内。"""
    if not (_MAINLAND[0] < longitude < _MAINLAND[1]):
        return True
    if not (_MAINLAND[2] < latitude < _MAINLAND[3]):
        return True
    if _inside_polygon(latitude, longitude, _HONG_KONG):
        return True
    if _inside_polygon(latitude, longitude, _MACAU):
        return True
    for box in (_TAIWAN, _MATSU):
        if box[0] <= longitude <= box[1] and box[2] <= latitude <= box[3]:
            return True
    return False


def _axis_latitude(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    value += (
        (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    )
    return value


def _axis_longitude(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    value += (
        (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    )
    return value


def _delta(latitude: float, longitude: float) -> tuple[float, float]:
    """GCJ-02 相对 WGS-84 的偏移量（度）。"""
    x = longitude - 105.0
    y = latitude - 35.0
    delta_latitude = _axis_latitude(x, y)
    delta_longitude = _axis_longitude(x, y)
    radians = math.radians(latitude)
    magic = 1.0 - _EE * math.sin(radians) ** 2
    sqrt_magic = math.sqrt(magic)
    delta_latitude = (delta_latitude * 180.0) / (
        (_A * (1.0 - _EE)) / (magic * sqrt_magic) * math.pi
    )
    delta_longitude = (delta_longitude * 180.0) / (_A / sqrt_magic * math.cos(radians) * math.pi)
    return (delta_latitude, delta_longitude)


def wgs_to_gcj(latitude: float, longitude: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02；**中国大陆以外（含港澳台）原样返回**。

    **只在交给用 GCJ 底图的那一家之前用**（Google 地图）。Apple 地图自己按 WGS-84 收，
    预先转换会让它偏到另一边去。
    """
    if _outside_mainland(latitude, longitude):
        return (latitude, longitude)
    delta_latitude, delta_longitude = _delta(latitude, longitude)
    return (latitude + delta_latitude, longitude + delta_longitude)


__all__ = ["wgs_to_gcj"]
