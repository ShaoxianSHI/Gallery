"""编辑位置的那层翻译（ADR 0031）：两个方向怎么合流、什么时候什么都不做。

**反查器是假的**；每条用例盯的是**写出去的字段**：有没有写、写没写全、不该写的一个字节都没动。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pytest

from gallery.core.errors import MetadataWriteError
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState, plan_place_edit
from gallery.core.metadata.writeback import (
    COORDINATE_TAGS,
    PLACE_TAGS,
    STALE_GPS_TAGS,
    Assignment,
)
from gallery.core.places.dataset import Place
from gallery.core.places.resolver import ResolvedPlace

HANGZHOU = ("杭州", "浙江省", "中国", 30.249, 120.205, 3.1)
SUZHOU = ("苏州", "江苏省", "中国", 31.311, 120.621, 0.0)


@dataclass
class FakeResolver:
    """按坐标 / 按名字各给一句，不多不少。"""

    by_coordinate: tuple[str, str, str, float, float, float] | None = HANGZHOU
    by_name: dict[str, tuple[str, str, str, float, float, float]] | None = None
    calls: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    @property
    def ready(self) -> bool:
        return True

    def place_for(
        self, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace | None:
        self.calls.append(f"坐标 {latitude},{longitude}")
        if self.by_coordinate is None:
            return None
        city, region, country, _lat, _lon, distance = self.by_coordinate
        return ResolvedPlace(city=city, region=region, country=country, distance_km=distance)

    def place_named(
        self,
        name: str,
        *,
        region: str | None = None,
        country: str | None = None,
        status: object = None,
    ) -> tuple[Place, ...]:
        self.calls.append(f"名字 {name}")
        found = (self.by_name or {}).get(name)
        if found is None:
            return ()
        city, province, nation, latitude, longitude, distance = found
        return (
            Place(
                city=city,
                region=province,
                country=nation,
                distance_km=distance,
                latitude=latitude,
                longitude=longitude,
            ),
        )


def tags(assignments: tuple[Assignment, ...]) -> dict[str, str]:
    return {item.tag: item.value for item in assignments}


def test_only_coordinates_given_writes_only_the_coordinates(library_state: PlaceState) -> None:
    """**只填坐标就只写坐标** —— 地名一个字都不动（ADR 0039 决策 4）。

    这一层顺手反查地名的话，「对话框显示的那一份」与「真正写下去的那一份」就不是同一份
    —— 界面上那三个 ``if place.region and ...`` 查不到就不动，而这里的空值是**删掉**。
    跨层填充放在对话框那两个按钮上（按钮改的就是框本身），所以这一层只做翻译。
    """
    resolver = FakeResolver()

    plan = plan_place_edit(
        library_state,
        fill(library_state, latitude=30.2741, longitude=120.1551),
        resolver=resolver,
    )

    written = tags(plan.assignments)
    assert written["EXIF:GPSLatitude"] == "30.274100"
    assert written["EXIF:GPSLatitudeRef"] == "N"
    assert not (set(written) & set(PLACE_TAGS)), "地名不该被顺手改掉"
    assert plan.coordinates == (30.2741, 120.1551)
    assert resolver.calls == [], "这一层不再反查"


def test_the_place_is_written_only_when_the_form_carries_it(library_state: PlaceState) -> None:
    """地点与坐标都在**框里**（用户按过对话框的按钮）时，两半一次写对。"""
    plan = plan_place_edit(
        library_state,
        PlaceEdit(
            city="杭州", region="浙江省", country="中国", latitude=30.2741, longitude=120.1551
        ),
    )

    written = tags(plan.assignments)
    assert written["XMP:City"] == "杭州"
    assert written["XMP:State"] == "浙江省"
    assert written["EXIF:GPSLatitude"] == "30.274100"
    assert plan.city == "杭州" and plan.coordinates == (30.2741, 120.1551)


def test_a_layer_the_form_left_blank_is_still_cleared(library_state: PlaceState) -> None:
    """**框里空就是「清掉它」** —— 对话框显示的就是将被写入的那一份（ADR 0039 决策 2）。

    城市库里上海那 151 条（直辖市之类）没有省，于是反查得到的省是空。用户在界面上
    看得见那一栏被清空了，保存时就该真的清掉，而不是替他保留一个「浙江省」。
    """
    plan = plan_place_edit(
        library_state,
        PlaceEdit(city="上海", region="", country="中国", latitude=31.2304, longitude=121.4737),
    )

    written = tags(plan.assignments)
    assert written["XMP:City"] == "上海"
    assert written["XMP:State"] == "", "框里是空的，写盘就该是删掉"
    assert written["IPTC:Province-State"] == ""


def test_a_place_given_does_not_look_a_coordinate_up(library_state: PlaceState) -> None:
    """**只填地名不会凭空多出一个坐标**（ADR 0031 决策 3、ADR 0039 决策 1）。

    用户按对话框那个「地点 → 经纬度」按钮，坐标才会出现在框里；只敲了城市就保存，
    坐标保持原样 —— 绝不配一个看起来精确、实际错得离谱的值。
    """
    resolver = FakeResolver(by_name={"苏州": SUZHOU})

    plan = plan_place_edit(
        library_state, PlaceEdit(city="苏州", region="江苏省", country="中国"), resolver=resolver
    )

    assert "EXIF:GPSLatitude" not in tags(plan.assignments)
    assert plan.coordinates == library_state.coordinates()
    assert resolver.calls == []


def test_a_name_the_database_does_not_know_leaves_the_coordinates_alone(
    library_state: PlaceState,
) -> None:
    """**查不到就是查不到**：绝不编一个坐标，只写地名，照片原来的坐标一个字节都不动。"""
    resolver = FakeResolver(by_name={})

    plan = plan_place_edit(library_state, PlaceEdit(city="西湖·断桥"), resolver=resolver)

    written = tags(plan.assignments)
    assert set(written) <= set(PLACE_TAGS)
    assert plan.coordinates == library_state.coordinates()
    assert resolver.calls == [], "这一层不再正查 —— 按不按按钮是用户的事"


def test_both_given_user_wins_and_nothing_is_looked_up(library_state: PlaceState) -> None:
    """两个方向都填了：**用户填的说了算**，不去反查覆盖他。"""
    resolver = FakeResolver()

    plan = plan_place_edit(
        library_state,
        fill(library_state, city="苏州", latitude=30.2741, longitude=120.1551),
        resolver=resolver,
    )

    assert resolver.calls == []  # 一次都没问
    assert plan.city == "苏州"
    assert plan.coordinates == (30.2741, 120.1551)


def test_editing_coordinates_clears_the_altitude_of_the_old_place(
    library_state: PlaceState,
) -> None:
    """海拔与定位时间记的是**另一个位置**上的测量值：留着就是一条自相矛盾的 GPS 记录。"""
    plan = plan_place_edit(
        library_state,
        fill(library_state, latitude=30.2741, longitude=120.1551),
        resolver=FakeResolver(),
    )

    written = tags(plan.assignments)
    assert written["EXIF:GPSAltitude"] == ""
    assert written["EXIF:GPSDateStamp"] == ""


#: 「改坐标」与「清空坐标」都要动的那一批 —— 含海拔与定位时间。
_ALL_GPS: Final = (*COORDINATE_TAGS, *STALE_GPS_TAGS)


def test_emptying_every_field_removes_the_whole_place(library_state: PlaceState) -> None:
    """**三层地名与两个坐标框全清空，就是「把位置整个清掉」**，不需要单独的勾（ADR 0031）——
    那个勾只会多出一种要记的用法，而清空本来就表达得出「全删」。
    """
    resolver = FakeResolver()

    plan = plan_place_edit(
        library_state,
        fill(library_state, city="", region="", country="", clear_coordinates=True),
        resolver=resolver,
    )

    # 坐标那四条**加上**海拔与定位时间 —— 见下面那条：这两条记的是旧位置上的测量值，
    # 留着就是一条自相矛盾的 GPS 记录（ADR 0031 决策 6）。
    assert {item.tag for item in plan.assignments} == set(_ALL_GPS) | set(PLACE_TAGS)
    assert set(tags(plan.assignments).values()) == {""}
    assert resolver.calls == []


def test_emptying_both_coordinate_boxes_removes_only_the_coordinates(
    library_state: PlaceState,
) -> None:
    """定位漂了、地名没错 —— 用户要的是「删坐标、留地名」，不是「全清」。"""
    plan = plan_place_edit(
        library_state, fill(library_state, clear_coordinates=True), resolver=FakeResolver()
    )

    written = tags(plan.assignments)
    assert set(written) == set(_ALL_GPS)
    assert plan.city == library_state.city


def test_clearing_the_coordinates_clears_the_altitude_too(library_state: PlaceState) -> None:
    """**「清空坐标」与「改坐标」动的是同一批标签** —— 海拔与定位时间一并清掉。

    只清 :data:`COORDINATE_TAGS` 那四条的话，文件里会留下一条「有海拔 3200 米、
    有 GPS 定位时间、但没有经纬度」的记录 —— 而「改坐标」那条
    :func:`_coordinate_assignments` 的 docstring 恰好写着「留着一条 GPS 记录就自相矛盾」。
    **两条路各写一份清单，迟早分叉** —— 所以只写一份（``place_edit._all_coordinate_tags``），
    这条用例钉住它们不再分叉。
    """
    from gallery.core.metadata.place_edit import _all_coordinate_tags

    cleared = tags(
        plan_place_edit(
            library_state, fill(library_state, clear_coordinates=True), resolver=FakeResolver()
        ).assignments
    )
    edited = tags(
        plan_place_edit(
            library_state,
            fill(library_state, latitude=30.2741, longitude=120.1551),
            resolver=FakeResolver(),
        ).assignments
    )

    assert set(_all_coordinate_tags()) == set(COORDINATE_TAGS) | set(STALE_GPS_TAGS)
    assert set(STALE_GPS_TAGS) <= set(cleared), "清空坐标没有清掉海拔与定位时间"
    assert set(STALE_GPS_TAGS) <= set(edited)
    assert set(cleared) == set(edited), "两条路发的标签集合分叉了"


def test_nothing_changed_writes_nothing(library_state: PlaceState) -> None:
    resolver = FakeResolver()

    plan = plan_place_edit(library_state, fill(library_state), resolver=resolver)

    assert plan.is_empty
    assert resolver.calls == []


def test_clearing_one_name_layer_deletes_that_field(library_state: PlaceState) -> None:
    """把「省 / 州」清空是可以的 —— 那与「没动它」是两件事（ADR 0031 的取舍）。"""
    plan = plan_place_edit(
        library_state, fill(library_state, region=""), resolver=FakeResolver(by_name={})
    )

    written = tags(plan.assignments)
    assert written["XMP:State"] == ""
    assert written["IPTC:Province-State"] == ""
    assert written["XMP:City"] == "南京"  # 其余两层照原样


def test_no_place_anywhere_nearby_keeps_the_written_name(library_state: PlaceState) -> None:
    """坐标挪到了海上：地名**不清空**。该由用户判断是哪个错了，替他删字段只会更难查。"""
    resolver = FakeResolver(by_coordinate=None)

    plan = plan_place_edit(
        library_state, fill(library_state, latitude=-30.0, longitude=-140.0), resolver=resolver
    )

    assert plan.city == library_state.city
    assert set(tags(plan.assignments)) & set(PLACE_TAGS) == set()  # 地名一个字都没写


@pytest.mark.parametrize(
    ("latitude", "longitude", "complaint"),
    [
        (30.0, None, "经度和纬度要一起填"),
        (None, 120.0, "经度和纬度要一起填"),
        (91.0, 120.0, "纬度要在 -90 到 90 之间"),
        (-91.0, 120.0, "纬度要在 -90 到 90 之间"),
        (30.0, 181.0, "经度要在 -180 到 180 之间"),
        (0.0, 0.0, "世上并不存在"),
    ],
)
def test_impossible_coordinates_are_refused_with_a_reason(
    library_state: PlaceState, latitude: float | None, longitude: float | None, complaint: str
) -> None:
    with pytest.raises(MetadataWriteError, match=complaint):
        plan_place_edit(library_state, fill(library_state, latitude=latitude, longitude=longitude))


def test_the_southern_hemisphere_writes_a_south_ref(library_state: PlaceState) -> None:
    plan = plan_place_edit(
        library_state,
        fill(library_state, latitude=-33.8688, longitude=151.2093),
        resolver=FakeResolver(),
    )

    written = tags(plan.assignments)
    assert written["EXIF:GPSLatitudeRef"] == "S"
    assert written["EXIF:GPSLongitudeRef"] == "E"
    assert written["EXIF:GPSLatitude"] == "33.868800"  # 绝对值 + Ref，与 exiftool 的形状一致


def fill(state: PlaceState, **changes: object) -> PlaceEdit:
    """造一份「用户在对话框里填完的那一份」：打开时三层地名与坐标都填着照片此刻的值，
    所以 ``PlaceEdit`` 收到的是**最终状态**而不是补丁 —— 用它写才不会把地名悄悄清空。"""
    base: dict[str, object] = {
        "city": state.city,
        "region": state.region,
        "country": state.country,
        "latitude": state.latitude,
        "longitude": state.longitude,
    }
    return PlaceEdit(**{**base, **changes})  # type: ignore[arg-type]


@pytest.fixture
def library_state() -> PlaceState:
    """一张「文件里写着南京、也有 GPS」的照片。"""
    return PlaceState(
        city="南京", region="江苏省", country="中国", latitude=32.0603, longitude=118.7969
    )
