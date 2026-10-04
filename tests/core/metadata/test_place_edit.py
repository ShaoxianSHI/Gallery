"""编辑位置的那层翻译（ADR 0031）：两个方向怎么合流、什么时候什么都不做。

**反查器是假的**：它只回答「这个坐标最近的城市是什么」「这个名字在哪」，不碰文件、不碰
城市库 —— 城市库那一半在 ``tests/core/places/test_dataset.py`` 里单独测。

每条用例都盯两件事：**写出去的字段**（有没有写、有没有写全、有没有多写）与
**要说给用户的话**（``notes``）。后者与前者同样重要：改完坐标而用户不知道地点被重算了，
下次打开就会以为程序记错了。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from gallery.core.errors import MetadataWriteError
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState, plan_place_edit
from gallery.core.metadata.writeback import COORDINATE_TAGS, PLACE_TAGS, Assignment
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


def test_only_coordinates_given_still_fills_in_the_place(library_state: PlaceState) -> None:
    """**坐标 → 地点**：只改坐标，地点也一并重算并写进去，于是两者从此说同一句话。"""
    resolver = FakeResolver()

    plan = plan_place_edit(
        library_state,
        fill(library_state, latitude=30.2741, longitude=120.1551),
        resolver=resolver,
    )

    written = tags(plan.assignments)
    assert written["EXIF:GPSLatitude"] == "30.274100"
    assert written["EXIF:GPSLatitudeRef"] == "N"
    assert written["XMP:City"] == "杭州"
    assert written["IPTC:City"] == "杭州"
    assert plan.city == "杭州" and plan.coordinates == (30.2741, 120.1551)
    assert any("地点已按新坐标更新" in note for note in plan.notes)
    assert any("3.1 公里" in note for note in plan.notes)  # 「这只是最近的城市」要说出来


def test_a_place_given_updates_the_coordinates(library_state: PlaceState) -> None:
    """**地点 → 坐标**：城市库里有这个名字，就把它的位置写进去。"""
    resolver = FakeResolver(by_name={"苏州": SUZHOU})

    plan = plan_place_edit(
        library_state, PlaceEdit(city="苏州", region="江苏省", country="中国"), resolver=resolver
    )

    assert plan.coordinates == (31.311, 120.621)
    assert tags(plan.assignments)["EXIF:GPSLatitude"] == "31.311000"
    assert any("市级精度" in note or "不是照片的具体位置" in note for note in plan.notes)


def test_a_name_the_database_does_not_know_leaves_the_coordinates_alone(
    library_state: PlaceState,
) -> None:
    """**查不到就是查不到**：绝不编一个坐标，只写地名，并说清为什么。"""
    resolver = FakeResolver(by_name={})

    plan = plan_place_edit(library_state, PlaceEdit(city="西湖·断桥"), resolver=resolver)

    written = tags(plan.assignments)
    assert set(written) <= set(PLACE_TAGS)
    assert plan.coordinates == library_state.coordinates()
    assert any("城市库里没有" in note for note in plan.notes)


def test_a_name_the_database_does_not_know_is_told_about_foreign_spelling(
    library_state: PlaceState,
) -> None:
    """输「京都」查不到，是因为库里写的是 ``Kyoto``（ADR 0011）—— 不说这句像是功能坏了。"""
    resolver = FakeResolver(by_name={})

    plan = plan_place_edit(library_state, PlaceEdit(city="京都"), resolver=resolver)

    assert any("Kyoto" in note for note in plan.notes)


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


def test_emptying_every_field_removes_the_whole_place(library_state: PlaceState) -> None:
    """**三层地名与两个坐标框全清空，就是「把位置整个清掉」** —— 不需要单独的勾（ADR 0031）。

    那一个勾选框只会多出一种要记的用法；清空本来就是编辑器里唯一表达得出「全删」的形状。
    """
    resolver = FakeResolver()

    plan = plan_place_edit(
        library_state,
        fill(library_state, city="", region="", country="", clear_coordinates=True),
        resolver=resolver,
    )

    assert {item.tag for item in plan.assignments} == set(COORDINATE_TAGS) | set(PLACE_TAGS)
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
    assert set(written) == set(COORDINATE_TAGS)
    assert plan.city == library_state.city


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
    assert set(tags(plan.assignments)) & set(PLACE_TAGS) == set()
    assert any("没有改动" in note for note in plan.notes)


# ---- 不合法的坐标 ---------------------------------------------------------------


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
    """造一份「用户在对话框里填完的那一份」。

    **对话框打开时会把三层地名与坐标全都填上**（它显示的就是照片此刻的值），所以
    ``PlaceEdit`` 收到的一直是**最终状态**而不是补丁。这里的 ``fill`` 就是照那个行为写的
    —— 用它而不是手写 ``PlaceEdit(latitude=…)``，才不会在用例里悄悄表达成
    「把三层地名清空」。
    """
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
