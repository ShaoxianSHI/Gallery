"""新建事件时按照片预填的默认值（纯逻辑，不碰 Qt、不碰文件）。"""

from __future__ import annotations

from gallery.core.events.prefill import EventPrefill, common_place, prefill_from_photos
from gallery.core.metadata.facets import PhotoFacets


def _photo(
    *,
    day: int | None = 16,
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
) -> PhotoFacets:
    """一张照片的元数据快照。年月日是**墙上时间**（``facets`` 那三个字段的口径）。"""
    return PhotoFacets(
        year=2026 if day is not None else None,
        month=5 if day is not None else None,
        day=day,
        city=city,
        region=region,
        country=country,
    )


# ---- 日期 -----------------------------------------------------------------------


def test_one_photo_fills_both_ends_with_its_own_day() -> None:
    """只选中一张：开始与结束都是它自己的拍摄日期。"""
    prefill = prefill_from_photos([_photo(day=16)])

    assert prefill.started_on == "2026-05-16"
    assert prefill.ended_on == "2026-05-16"


def test_several_photos_span_from_the_earliest_to_the_latest() -> None:
    """多选：最早的那天当开始、最晚的那天当结束 —— **与选区的顺序无关**。"""
    prefill = prefill_from_photos([_photo(day=20), _photo(day=16), _photo(day=18)])

    assert prefill.started_on == "2026-05-16"
    assert prefill.ended_on == "2026-05-20"


def test_photos_without_a_capture_time_do_not_move_the_range() -> None:
    """没拍摄时间的那几张不进这个判断 —— 缺失不该把结论拉成一个不存在的日子。"""
    prefill = prefill_from_photos([_photo(day=16), _photo(day=None), _photo(day=18)])

    assert prefill.started_on == "2026-05-16"
    assert prefill.ended_on == "2026-05-18"


def test_nothing_to_go_on_leaves_the_dates_blank() -> None:
    """一张都没有拍摄时间：两栏都留空，而不是替用户挑一天。"""
    prefill = prefill_from_photos([_photo(day=None), _photo(day=None)])

    assert prefill == EventPrefill()


# ---- 目的地 ---------------------------------------------------------------------


def test_the_same_city_gives_the_city() -> None:
    """城市都一样就用城市 —— 不必退到省州那一层。"""
    places = [_photo(city="杭州", region="浙江省", country="中国") for _ in range(3)]

    assert common_place(places) == "杭州"


def test_different_cities_in_one_province_give_the_province() -> None:
    """城市不同、省州一样 → 省州。"""
    places = [
        _photo(city="杭州", region="浙江省", country="中国"),
        _photo(city="宁波", region="浙江省", country="中国"),
    ]

    assert common_place(places) == "浙江省"


def test_different_provinces_in_one_country_give_the_country() -> None:
    """省州不同、国家一样 → 国家。"""
    places = [
        _photo(city="杭州", region="浙江省", country="中国"),
        _photo(city="南京", region="江苏省", country="中国"),
    ]

    assert common_place(places) == "中国"


def test_places_that_share_nothing_give_nothing() -> None:
    """连国家都不一样就没有公共位置 —— 留空让用户自己填，不挑一个。"""
    places = [
        _photo(city="杭州", region="浙江省", country="中国"),
        _photo(city="Bordeaux", region=None, country="France"),
    ]

    assert common_place(places) == ""


def test_a_photo_without_a_place_does_not_hide_the_answer() -> None:
    """**只看有地点的那几张**：一张没坐标的照片不该把整批拉成「未知」。"""
    places = [
        _photo(city="杭州", region="浙江省", country="中国"),
        _photo(),
        _photo(city="绍兴", region="浙江省", country="中国"),
    ]

    assert common_place(places) == "浙江省"


def test_one_photo_missing_the_city_level_is_not_counted_as_sharing_it() -> None:
    """一张写着城市、另一张只写了国家 —— 那两张并不共有城市，于是往省州看。"""
    places = [
        _photo(city="杭州", region="浙江省", country="中国"),
        _photo(region="浙江省", country="中国"),
    ]

    assert common_place(places) == "浙江省"


def test_nobody_has_a_place_at_all() -> None:
    assert common_place([_photo(), _photo()]) == ""


# ---- 两者一起 -------------------------------------------------------------------


def test_the_dates_and_the_destination_come_out_of_one_pass() -> None:
    """日期与目的地由同一个入口给出 —— 调用点只管把那份默认值递给对话框。"""
    prefill = prefill_from_photos(
        [
            _photo(day=16, city="杭州", region="浙江省", country="中国"),
            _photo(day=18, city="宁波", region="浙江省", country="中国"),
        ]
    )

    assert prefill.started_on == "2026-05-16"
    assert prefill.ended_on == "2026-05-18"
    assert prefill.destination == "浙江省"


def test_no_photos_at_all() -> None:
    assert prefill_from_photos([]) == EventPrefill()
