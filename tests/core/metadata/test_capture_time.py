"""拍摄时间那一半的翻译（ADR 0032）：两个字段各自可留空，只写真正变了的那个。

盯的是**写出去的字段**：有没有写、有没有写全、以及没动的那一个是不是真的一个字都没动。
「哪些字段会被改」是这一层全部的责任 —— 横幅只负责说「成了吗、能不能撤」。

最后一条是**真的走一遍写回**（对着 ``tmp_path`` 里现场造的最小 JPEG，用假的 writer）：
这一层平常只交出一份计划，没人看着它的时候计划可能一直是错的，而红线 4 要求每一次
写回都留下可回滚的记录 —— 所以这条用例断言 journal 里确实多了一条。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.capture_time import (
    CaptureTime,
    CaptureTimeEdit,
    plan_capture_time_edit,
)
from gallery.core.metadata.journal import ACTION_METADATA, RESULT_OK, RESULT_PENDING, read_records
from gallery.core.metadata.writeback import Assignment, write_metadata


def tags(assignments: tuple[Assignment, ...]) -> dict[str, str]:
    return {item.tag: item.value for item in assignments}


def fill(state: CaptureTime, **changes: object) -> CaptureTimeEdit:
    """造一份「用户在对话框里填完的那一份」。

    **对话框打开时会把时间与时区两个框都填上**（它显示的就是照片此刻的值），所以
    ``CaptureTimeEdit`` 收到的一直是**最终状态**而不是补丁。照那个行为写，才不会在
    「只改时间」的用例里悄悄表达成「顺手把时区也清空了」。
    """
    base: dict[str, object] = {"taken_at": state.taken_at, "offset": state.offset}
    return CaptureTimeEdit(**{**base, **changes})  # type: ignore[arg-type]


@pytest.fixture
def state() -> CaptureTime:
    """一张「照片里写着时间、也记着时区」的照片。"""
    return CaptureTime(taken_at="2024:05:16 20:52:02", offset="+08:00")


class FakeWriter:
    """一份内存里的「文件」—— 测试不得依赖本机有没有装 exiftool（AGENTS）。"""

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


# ---- 「最终状态」而不是「改了哪些」 ---------------------------------------------


def test_an_untouched_state_produces_no_assignment(state: CaptureTime) -> None:
    """打开对话框什么都没动就按保存：**一个字都不写**。

    对话框打开时把照片此刻的值全填进去（访达「显示简介」的用法），于是「没动」在数据上
    与「改了但改成了同一个值」长得一模一样 —— 这里必须认得出来。把同样的值再写一遍
    只会让日志里多一条空记录，而 ⌘Z 会因此去撤一件空事。
    """
    plan = plan_capture_time_edit(state, fill(state))

    assert plan == ()


def test_changing_only_the_time_leaves_the_offset_alone(state: CaptureTime) -> None:
    """只改钟点：**只有 ``DateTimeOriginal`` 一个字段**，时区一个字都不动。

    时区是独立的一个字段（ADR 0032 决策 2），用户在对话框里没碰它就不该被顺带改掉 ——
    凭空替用户改时区，后果是整张照片的时间偏几个小时。
    """
    plan = plan_capture_time_edit(state, fill(state, taken_at="2003-07-19 09:12:00"))

    assert tags(plan) == {"EXIF:DateTimeOriginal": "2003:07:19 09:12:00"}


def test_changing_only_the_offset_leaves_the_time_alone(state: CaptureTime) -> None:
    """「相机时区设错了」正是只改偏移的那一种 —— 时间必须原样留着。"""
    plan = plan_capture_time_edit(state, fill(state, offset="+09:00"))

    assert tags(plan) == {"EXIF:OffsetTimeOriginal": "+09:00"}


def test_both_halves_come_out_in_one_plan(state: CaptureTime) -> None:
    """改了两处就**一次交出两个字段** —— 不是两次写回、不是两份计划（ADR 0032）。"""
    plan = plan_capture_time_edit(
        state, fill(state, taken_at="2003-07-19 09:12:00", offset="+09:00")
    )

    assert tags(plan) == {
        "EXIF:DateTimeOriginal": "2003:07:19 09:12:00",
        "EXIF:OffsetTimeOriginal": "+09:00",
    }


# ---- 留空 = 照片里不记这一项 ----------------------------------------------------


def test_blanking_the_offset_deletes_it_and_keeps_the_time(state: CaptureTime) -> None:
    """**留空 = 把那个字段删掉**，与位置那一侧的空框是同一套语义（ADR 0031 / 0032）。

    空串交给 exiftool 就是删除。所以「把时区那一栏清空」是一次明确的删除，不是「没动」。
    """
    plan = plan_capture_time_edit(state, fill(state, offset=None))

    assert tags(plan) == {"EXIF:OffsetTimeOriginal": ""}


def test_blanking_both_deletes_both_fields(state: CaptureTime) -> None:
    plan = plan_capture_time_edit(state, fill(state, taken_at=None, offset=None))

    assert tags(plan) == {"EXIF:DateTimeOriginal": "", "EXIF:OffsetTimeOriginal": ""}


def test_a_photo_without_an_offset_is_not_given_one() -> None:
    """照片没记时区时，空着的偏移栏**绝不会被补一个**。

    凭空替用户猜时区，猜错的后果是排序整体偏几个小时 —— 比空着严重得多（那正是
    「相机十有八九不写 ``OffsetTimeOriginal``」这条现实存在的原因）。
    """
    bare = CaptureTime(taken_at="2024:05:16 20:52:02")

    plan = plan_capture_time_edit(bare, CaptureTimeEdit(taken_at=bare.taken_at))

    assert plan == ()


# ---- 写法不对就当场报错 --------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "text", "complaint"),
    [
        ("taken_at", "昨天下午", "拍摄时间看不懂"),
        ("taken_at", "2024-13-45 99:99:99", "拍摄时间看不懂"),
        ("offset", "东八区", "时区看不懂"),
    ],
)
def test_something_that_makes_no_sense_is_refused_with_a_reason(
    state: CaptureTime, field: str, text: str, complaint: str
) -> None:
    with pytest.raises(MetadataWriteError, match=complaint):
        plan_capture_time_edit(state, CaptureTimeEdit(**{field: text}))  # type: ignore[arg-type]


def test_the_user_may_type_dashes_and_a_short_seconds(state: CaptureTime) -> None:
    """对话框里写的是连字符、秒常常省略 —— 归一之后仍然认得出来。"""
    plan = plan_capture_time_edit(state, fill(state, taken_at="2003-07-19 09:12"))

    assert tags(plan) == {"EXIF:DateTimeOriginal": "2003:07:19 09:12:00"}


# ---- 真的走一遍写回 -------------------------------------------------------------


def test_a_planned_edit_writes_back_and_leaves_a_journal_record(tmp_path: Path) -> None:
    """**对着临时目录里现场造的照片走一遍真的写回**（红线 4）。

    前面那些用例只交出一份计划；这一条把计划交给 :func:`write_metadata`，于是它要过
    白名单守卫、路径守卫、回读校验与记账四道关。任何一道漏了，红线 4 说的「每一次
    写回都要留下可回滚记录」就漏了一个地方。
    """
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(root / "一张.jpg")
    paths.config_dir(root).mkdir()
    photo = PhotoRef.from_path(root / "一张.jpg")
    state = CaptureTime(taken_at="2024:05:16 20:52:02", offset="+08:00")

    plan = plan_capture_time_edit(
        state, fill(state, taken_at="2003-07-19 09:12:00", offset="+09:00")
    )
    writer = FakeWriter(
        {
            "EXIF:DateTimeOriginal": state.taken_at or "",
            "EXIF:OffsetTimeOriginal": state.offset or "",
        }
    )
    result = write_metadata(root, photo, plan, writer=writer)

    assert result.written
    assert writer.fields["EXIF:DateTimeOriginal"] == "2003:07:19 09:12:00"
    assert writer.fields["EXIF:OffsetTimeOriginal"] == "+09:00"
    records = read_records(root)
    assert records, "写回没有留下任何 journal 记录 —— 那样就撤不回来了"
    # **两条**：先记「打算改」（原值与新值已经齐了），动完文件再记终态。
    # 少掉前面那一条，写入与落盘之间那一下崩溃就什么都留不下。
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_OK]
    assert records[-1].action == ACTION_METADATA
    assert {(change.tag, change.before, change.after) for change in records[-1].changes} == {
        ("EXIF:DateTimeOriginal", "2024:05:16 20:52:02", "2003:07:19 09:12:00"),
        ("EXIF:OffsetTimeOriginal", "+08:00", "+09:00"),
    }
