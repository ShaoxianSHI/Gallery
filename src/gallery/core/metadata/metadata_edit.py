"""用户在编辑器里改的那份元数据，翻译成要写进文件的字段（ADR 0030 / 0031 / 0032）。
**这一层只做翻译，不碰文件**：输出一组 :class:`~gallery.core.metadata.writeback.Assignment`，
写盘的是 :func:`gallery.core.metadata.writeback.write_metadata`。位置与拍摄时间各有翻译逻辑
（:mod:`~gallery.core.metadata.place_edit`、:mod:`~gallery.core.metadata.capture_time`），但它们
在同一个对话框里，所以同一次保存必须是**一次写回**、一条 journal、一下 ⌘Z；:class:`MetadataEdit`
收的是「那几个框里是什么」，不是补丁 —— **空 = 照片里不记这一项**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gallery.core.metadata.capture_time import (
    CaptureTime,
    CaptureTimeEdit,
    plan_capture_time_edit,
)
from gallery.core.metadata.exif import normalize_capture_time, normalize_offset
from gallery.core.metadata.place_edit import PlaceEdit, PlacePlan, PlaceState, plan_place_edit
from gallery.core.metadata.writeback import CAPTURE_TIME_TAGS, Assignment

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver

#: 计划里「这次动的是不是拍摄时间」用的那组标签。
_CAPTURE_TAG_SET = frozenset(CAPTURE_TIME_TAGS)


@dataclass(frozen=True, slots=True)
class MetadataState:
    """照片此刻的元数据（**只有可编辑的那些**：拍摄时间与位置）。

    别的字段（相机、镜头、曝光……）不在这儿：它们压根不可编辑，也就没什么「此刻的值」
    需要记下来（ADR 0030 / 0032）。
    """

    capture: CaptureTime = CaptureTime()
    place: PlaceState = PlaceState()


@dataclass(frozen=True, slots=True)
class MetadataEdit:
    """用户在编辑器里填完的**那一份**（不是「改了哪些」，理由见模块文档）。"""

    capture: CaptureTimeEdit = CaptureTimeEdit()
    place: PlaceEdit = PlaceEdit()


@dataclass(frozen=True, slots=True)
class MetadataPlan:
    """一次编辑的结果。"""

    #: 要写进文件的字段。**为空表示什么都没改**，调用方不该走到写盘那一步。
    assignments: tuple[Assignment, ...] = ()
    #: 写完之后照片里会有的拍摄时间（**写进去的那一份**，不是推断的那一份）。
    capture: CaptureTime = CaptureTime()
    #: 写完之后照片里会有的地点与坐标。
    place: PlacePlan = PlacePlan()

    @property
    def is_empty(self) -> bool:
        return not self.assignments

    @property
    def capture_changed(self) -> bool:
        """这一次动的是不是拍摄时间。横幅要按它说清「改了时间」还是「改了位置」。"""
        return any(item.tag in _CAPTURE_TAG_SET for item in self.assignments)

    @property
    def place_changed(self) -> bool:
        return any(item.tag not in _CAPTURE_TAG_SET for item in self.assignments)


def plan_metadata_edit(
    state: MetadataState,
    edit: MetadataEdit,
    *,
    resolver: PlaceResolver | None = None,
) -> MetadataPlan:
    """把「用户填完的那一份」翻译成要写进文件的字段。

    位置的联动（正查 / 反查）与时间的归一化互不影响，各算各的再拼成一份计划。

    :raises MetadataWriteError: 坐标不合法，或时间 / 时区看不懂。
    """
    capture = CaptureTime(
        taken_at=normalize_capture_time(edit.capture.taken_at) if edit.capture.taken_at else None,
        offset=normalize_offset(edit.capture.offset) if edit.capture.offset else None,
    )
    place = plan_place_edit(state.place, edit.place, resolver=resolver)
    return MetadataPlan(
        assignments=(
            *plan_capture_time_edit(state.capture, edit.capture),
            *place.assignments,
        ),
        capture=capture,
        place=place,
    )


__all__ = [
    "MetadataEdit",
    "MetadataPlan",
    "MetadataState",
    "plan_metadata_edit",
]
