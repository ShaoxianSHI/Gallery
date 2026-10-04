"""用户在编辑器里改的那份元数据，翻译成要写进文件的字段（ADR 0030 / 0031 / 0032）。

**这一层只做翻译，不碰文件**：输入是「照片此刻的元数据」与「用户填完的那一份」，输出是一组
:class:`~gallery.core.metadata.writeback.Assignment`。真正写盘的是
:func:`gallery.core.metadata.writeback.write_metadata`，它记账、校验、回滚、可撤销。

## 它是合流的那一层

两件事各有各的翻译逻辑，也各有各的理由，放在各自的模块里：

- **位置**（:mod:`gallery.core.metadata.place_edit`，ADR 0031）：坐标与三层地名的双向联动、
  「查不到就只写地名」这些取舍；
- **拍摄时间**（:mod:`gallery.core.metadata.capture_time`，ADR 0032）：墙上时间与时区偏移
  是两个独立字段，各自可留空。

它们在**同一个对话框**里（``ui.panels.metadata_edit``），于是「用户在一次保存里改了两处」
必须是**一次写回**：一个 ``write_metadata`` 调用、一条 journal 记录、一下 ⌘Z。

分成两次写回的话，用户按了一次保存却要撤两下、横幅也要说两遍 —— 那不是一次操作。

## 「最终状态」而不是「改了哪些」

:attr:`MetadataEdit` 收的是「编辑器关掉时那几个框里是什么」，不是一份补丁。因此：

- **空 = 照片里不记这一项**（删掉那个字段），与「没动它」是同一件事 ——
  正因为如此，界面才必须在打开时把照片此刻的值全填进去（访达「显示简介」的用法）；
- 「哪个改了」由本模块拿 :class:`MetadataState` 比出来，界面不用传这个信息，
  也就没有机会把它算错。
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
    """一次编辑的落定结果。"""

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

    **两件事算完才拼成一份计划**：位置的联动（正查 / 反查）与时间的归一化互不影响，
    也没有哪个要先做出来才能做另一个 —— 所以就是各算各的，字段接在一起。

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
