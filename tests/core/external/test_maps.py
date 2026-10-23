"""地图深链：坐标怎么写进 URL、出了地球的坐标怎么被拒。"""

from __future__ import annotations

import pytest

from gallery.core.external.maps import apple_deep_link, google_web_link


def test_apple_link_centers_the_map_and_drops_a_pin() -> None:
    """**``ll`` 与 ``q`` 都要**：前者挪中心，后者落标记点。"""
    assert (
        apple_deep_link(39.908722, 116.397499)
        == "maps://?ll=39.908722,116.397499&q=39.908722,116.397499"
    )


def test_google_link_uses_the_documented_search_endpoint() -> None:
    assert (
        google_web_link(48.8584, 2.2945)
        == "https://www.google.com/maps/search/?api=1&query=48.858400,2.294500"
    )


def test_google_link_converts_mainland_coordinates_to_gcj() -> None:
    """Google 的国内路网是 GCJ-02：递 WGS 会偏几百米（ADR 0077）。"""
    assert (
        google_web_link(31.2304, 121.4737)
        == "https://www.google.com/maps/search/?api=1&query=31.228458,121.478223"
    )


def test_google_link_leaves_hong_kong_alone() -> None:
    assert (
        google_web_link(22.2819, 114.1582)
        == "https://www.google.com/maps/search/?api=1&query=22.281900,114.158200"
    )


def test_apple_link_keeps_wgs84() -> None:
    """Apple 自己处理国内底图偏移；这里换成 GCJ 会让它偏到另一边去。"""
    assert (
        apple_deep_link(31.2304, 121.4737)
        == "maps://?ll=31.230400,121.473700&q=31.230400,121.473700"
    )


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(90.1, 0.0), (-90.1, 0.0), (0.0, 180.1), (0.0, -180.1)],
)
def test_coordinates_outside_the_globe_are_refused(latitude: float, longitude: float) -> None:
    with pytest.raises(ValueError):
        apple_deep_link(latitude, longitude)
    with pytest.raises(ValueError):
        google_web_link(latitude, longitude)


def test_the_poles_and_the_antimeridian_are_fine() -> None:
    """边界值本身是合法坐标 —— 只有**越过去**才拒。"""
    assert "ll=90.000000,180.000000" in apple_deep_link(90.0, 180.0)
    assert "query=-90.000000,-180.000000" in google_web_link(-90.0, -180.0)
