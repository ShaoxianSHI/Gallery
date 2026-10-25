"""**合流的那一层**（ADR 0030 / 0031 / 0032）：一次保存 = 一次写回 = 一条 journal = 一下 ⌘Z。

这里盯的是合流本身：两处都改 → **一份**计划、只改一半 → 另一半一个字节都没动。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.capture_time import CaptureTime, CaptureTimeEdit
from gallery.core.metadata.journal import ACTION_METADATA, RESULT_OK, RESULT_PENDING, read_records
from gallery.core.metadata.metadata_edit import (
    MetadataEdit,
    MetadataState,
    plan_metadata_edit,
)
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState
from gallery.core.metadata.writeback import Assignment, write_metadata
from gallery.core.places.resolver import ResolvedPlace

HANGZHOU = ResolvedPlace(city="杭州", region="浙江省", country="中国", distance_km=1.2)


class FakeResolver:
    """按坐标反查一律答杭州；按名字一概查不到（库里没有的名字绝不能配一个坐标）。"""

    def __init__(self, found: ResolvedPlace | None = HANGZHOU) -> None:
        self.found = found
        self.calls: list[str] = []

    @property
    def ready(self) -> bool:
        return True

    def place_for(
        self, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace | None:
        self.calls.append(f"坐标 {latitude},{longitude}")
        return self.found

    def place_named(
        self,
        name: str,
        *,
        region: str | None = None,
        country: str | None = None,
        status: object = None,
    ) -> tuple[object, ...]:
        self.calls.append(f"名字 {name}")
        return ()


class FakeWriter:
    """一份内存里的「文件」—— 测试不得依赖本机有没有装 exiftool。"""

    def __init__(self, fields: Mapping[str, str] | None = None) -> None:
        self.fields: dict[str, str] = dict(fields or {})

    def available(self) -> bool:
        return True

    def read(self, path: Path) -> Mapping[str, str]:
        return dict(self.fields)

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        for item in assignments:
            if item.value:
                self.fields[item.tag] = item.value
            else:
                self.fields.pop(item.tag, None)


def tags(assignments: tuple[Assignment, ...]) -> set[str]:
    return {item.tag for item in assignments}


@pytest.fixture
def state() -> MetadataState:
    """一张南京的、写着头像时刻的照片（时间与位置两半都有值）。"""
    return MetadataState(
        capture=CaptureTime(taken_at="2024:05:16 20:52:02", offset="+08:00"),
        place=PlaceState(
            city="南京", region="江苏省", country="中国", latitude=32.0603, longitude=118.7969
        ),
    )


def fill_edit(current: MetadataState, **changes: object) -> MetadataEdit:
    """造一份「用户在对话框里填完的那一份」：打开时两个框组全都填着照片此刻的值，所以这里
    收到的一直是**最终状态**而不是补丁 —— 与 ``test_place_edit.py`` 的 ``fill`` 同一个道理。
    """
    base: dict[str, object] = {
        "capture": CaptureTimeEdit(
            taken_at=current.capture.taken_at, offset=current.capture.offset
        ),
        "place": PlaceEdit(
            city=current.place.city,
            region=current.place.region,
            country=current.place.country,
            latitude=current.place.latitude,
            longitude=current.place.longitude,
        ),
    }
    return MetadataEdit(**{**base, **changes})  # type: ignore[arg-type]


def test_an_untouched_editor_produces_no_assignment(state: MetadataState) -> None:
    """打开对话框什么都没动就按保存：**一条字段都不该有**。

    ``is_empty`` 是给调用方的信号：为空就不该走到写盘那一步，否则 ⌘Z 要去撤一件没发生的事。"""
    plan = plan_metadata_edit(state, fill_edit(state))

    assert plan.assignments == ()
    assert plan.is_empty
    assert not plan.capture_changed
    assert not plan.place_changed


def test_changing_both_halves_comes_out_as_one_plan(state: MetadataState) -> None:
    """**两处都改 → 恰好一份计划，两半都在里面。**

    分成两次写回的话，用户按一次保存却要撤两下、横幅也要说两遍 —— 那不是一次操作。"""
    edit = fill_edit(
        state,
        capture=CaptureTimeEdit(taken_at="2003-07-19 09:12:00", offset="+09:00"),
        place=PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())

    assert not plan.is_empty
    assert plan.capture_changed and plan.place_changed
    # 地名那一半是三层各写 XMP 与 IPTC 两份（``place_edit._place_assignments``）。
    assert tags(plan.assignments) == {
        "EXIF:DateTimeOriginal",
        "EXIF:OffsetTimeOriginal",
        "XMP:City",
        "IPTC:City",
        "XMP:State",
        "IPTC:Province-State",
        "XMP:Country",
        "IPTC:Country-PrimaryLocationName",
    }
    # **写进去的那一份**就是写完之后照片里会有的那一份（横幅按它说话）。
    assert plan.capture == CaptureTime(taken_at="2003:07:19 09:12:00", offset="+09:00")
    assert plan.place.city == "杭州"


def test_the_same_value_on_both_sides_is_not_written_twice(state: MetadataState) -> None:
    """值没变的那一半，在合流之后仍然一个字都不写。"""
    edit = fill_edit(
        state,
        capture=CaptureTimeEdit(taken_at="2003-07-19 09:12:00", offset="+08:00"),
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())

    assert tags(plan.assignments) == {"EXIF:DateTimeOriginal"}
    assert plan.capture_changed
    assert not plan.place_changed


def test_changing_only_the_time_leaves_the_place_untouched(state: MetadataState) -> None:
    """只改时间：位置那六个字段一个都不在计划里，照片里的地名与坐标原样留着。"""
    edit = fill_edit(
        state, capture=CaptureTimeEdit(taken_at="2003-07-19 09:12:00", offset="+09:00")
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())

    assert tags(plan.assignments) == {"EXIF:DateTimeOriginal", "EXIF:OffsetTimeOriginal"}
    assert plan.place.city == "南京"
    assert plan.place.coordinates == (32.0603, 118.7969)


def test_changing_only_the_place_leaves_the_time_untouched(state: MetadataState) -> None:
    """只改地点：时间那一个字段都不在计划里，照片里的拍摄时间原样留着 —— 对话框把时刻原样
    填在框里，用户没碰它，顺带改掉会让整张照片偏几个小时。
    """
    edit = fill_edit(
        state,
        place=PlaceEdit(
            city="杭州", region="浙江省", country="中国", latitude=30.2741, longitude=120.1551
        ),
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())

    assert "EXIF:DateTimeOriginal" not in tags(plan.assignments)
    assert "EXIF:OffsetTimeOriginal" not in tags(plan.assignments)
    assert plan.capture == state.capture
    assert plan.place.city == "杭州"
    assert plan.place.coordinates == (30.2741, 120.1551)


def test_the_two_halves_do_not_interfere_when_one_is_cleared(state: MetadataState) -> None:
    """把时区那一栏清空（那是删除）不该顺手把地名也清掉 —— 两半各自算各自的。"""
    edit = fill_edit(
        state,
        capture=CaptureTimeEdit(taken_at=state.capture.taken_at, offset=None),
        place=PlaceEdit(city="南京", region="江苏省", country="中国"),
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())

    written = {item.tag: item.value for item in plan.assignments}
    assert written == {"EXIF:OffsetTimeOriginal": ""}
    assert plan.place.city == "南京"  # 地名那三框原样，坐标也原样
    assert plan.place.coordinates == (32.0603, 118.7969)


@pytest.mark.parametrize(
    ("edit", "complaint"),
    [
        (
            {"capture": CaptureTimeEdit(taken_at="昨天下午", offset="+08:00")},
            "无法识别拍摄时间",
        ),
        (
            {"place": PlaceEdit(city="南京", latitude=30.0)},
            "需同时填写",
        ),
    ],
)
def test_something_the_user_typed_wrong_is_refused_with_a_reason(
    state: MetadataState, edit: dict[str, object], complaint: str
) -> None:
    """一半错了就是整次保存不成立 —— 不该只写回去一半。"""
    with pytest.raises(MetadataWriteError, match=complaint):
        plan_metadata_edit(state, fill_edit(state, **edit), resolver=FakeResolver())


def test_one_save_writes_back_once_and_leaves_one_journal_run(tmp_path: Path) -> None:
    """**一次保存 = 一次写回 = 一条 journal**（先记账再动文件）。

    两半合成一次写回，「按一次保存」与「⌘Z 撤一次」才一一对应；分开写会让撤销栈多出一摞。"""
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(root / "一张.jpg")
    paths.config_dir(root).mkdir()
    photo = PhotoRef.from_path(root / "一张.jpg")
    state = MetadataState(
        capture=CaptureTime(taken_at="2024:05:16 20:52:02", offset="+08:00"),
        place=PlaceState(city="南京", region="江苏省", country="中国"),
    )
    edit = fill_edit(
        state,
        capture=CaptureTimeEdit(taken_at="2003-07-19 09:12:00", offset="+09:00"),
        place=PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    plan = plan_metadata_edit(state, edit, resolver=FakeResolver())
    writer = FakeWriter(
        {
            "EXIF:DateTimeOriginal": "2024:05:16 20:52:02",
            "EXIF:OffsetTimeOriginal": "+08:00",
            "XMP:City": "南京",
        }
    )
    result = write_metadata(root, photo, plan.assignments, writer=writer)

    assert result.written
    assert writer.fields["EXIF:DateTimeOriginal"] == "2003:07:19 09:12:00"
    assert writer.fields["EXIF:OffsetTimeOriginal"] == "+09:00"
    assert writer.fields["XMP:City"] == "杭州"
    records = read_records(root)
    assert records, "写回没有留下任何 journal 记录 —— 那样就撤不回来了"
    # **一摞两条**（终态顶掉 pending），且两半的字段都在这一摞里。
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_OK]
    assert {record.action for record in records} == {ACTION_METADATA}
    written = {change.tag for change in records[-1].changes}
    assert "EXIF:DateTimeOriginal" in written
    assert "XMP:City" in written


def test_an_empty_plan_is_not_handed_to_the_write_back(tmp_path: Path) -> None:
    """空计划**到不了** ``write_metadata``（那一步会直接拒收并抛错）—— 这是上一条的另一半：
    调用点必须先看 ``is_empty``，否则「什么都没改」就成了一次抛异常的操作。
    """
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(root / "一张.jpg")
    paths.config_dir(root).mkdir()
    photo = PhotoRef.from_path(root / "一张.jpg")
    state = MetadataState(
        capture=CaptureTime(taken_at="2024:05:16 20:52:02"),
        place=PlaceState(city="南京", region="江苏省", country="中国"),
    )

    plan = plan_metadata_edit(state, fill_edit(state))

    assert plan.is_empty
    with pytest.raises(MetadataWriteError, match="没有需要写入的改动"):
        write_metadata(root, photo, plan.assignments, writer=FakeWriter())
    assert read_records(root) == []  # 一次假写回也不该记账
