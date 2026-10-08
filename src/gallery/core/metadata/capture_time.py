"""用户在编辑器里改的**拍摄时间**，翻译成要写进文件的字段（ADR 0032）。

**这一层只做翻译，不碰文件**：输出一组 :class:`~gallery.core.metadata.writeback.Assignment`，
写盘的是 :func:`gallery.core.metadata.writeback.write_metadata`（记账、校验、回滚、可撤销）。
时区是单独的可选字段：**留空 = 照片里不记这一项，绝不按本机时区补一个** —— 猜错的后果是排序
整体偏几个小时。只写 ``DateTimeOriginal``，不碰 ``CreateDate`` / ``ModifyDate`` 等日期字段。
"""

from __future__ import annotations

from dataclasses import dataclass

from gallery.core.errors import MetadataWriteError
from gallery.core.metadata.exif import normalize_capture_time, normalize_offset
from gallery.core.metadata.writeback import CAPTURE_TIME_TAGS, Assignment

#: 与 :data:`writeback.CAPTURE_TIME_TAGS` **同一份**（那边才是可写字段的唯一来源），
#: 这里只是给两个下标起个名字 —— 免得在赋值处数下标，数错了写的就是另一个字段。
_TAKEN_AT_TAG, _OFFSET_TAG = CAPTURE_TIME_TAGS


@dataclass(frozen=True, slots=True)
class CaptureTime:
    """照片此刻的拍摄时间（**已归一**：时间是 EXIF 的写法，偏移是 ``+08:00``）。"""

    #: ``2024:05:16 20:52:02`` —— 墙上时间，不带时区。
    taken_at: str | None = None
    #: ``+08:00``。**照片没记时区时就是 ``None``**，不按本机时区补。
    offset: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureTimeEdit:
    """用户在编辑器里填完的那一份。**空 = 照片里不记这一项**（与位置同一套语义）。"""

    taken_at: str | None = None
    offset: str | None = None


def plan_capture_time_edit(state: CaptureTime, edit: CaptureTimeEdit) -> tuple[Assignment, ...]:
    """把「用户填完的那一份」翻译成要写进文件的字段。

    **空串 = 把这个字段删掉**（exiftool 的写法）；值没变的字段一个字都不写 —— 否则日志里多一条
    空记录，⌘Z 会去撤一件空事。:raises MetadataWriteError: 时间或时区看不懂。
    """
    taken = _time(edit.taken_at)
    offset = _offset(edit.offset)
    result: list[Assignment] = []
    if taken != state.taken_at:
        result.append(Assignment(_TAKEN_AT_TAG, taken or ""))
    if offset != state.offset:
        result.append(Assignment(_OFFSET_TAG, offset or ""))
    return tuple(result)


def _time(text: str | None) -> str | None:
    """输入框里的时间 → EXIF 的写法。**空就是「不记」**，看不懂就当场报错。"""
    if not text or not text.strip():
        return None
    normalized = normalize_capture_time(text)
    if normalized is None:
        raise MetadataWriteError(
            f"拍摄时间看不懂：「{text.strip()}」。"
            "请填成 2024-05-16 20:52:02 这样的写法（秒可以省）。",
            detail=text,
        )
    return normalized


def _offset(text: str | None) -> str | None:
    """输入框里的时区 → ``+08:00``。**空就是「照片不记时区」**。"""
    if not text or not text.strip():
        return None
    normalized = normalize_offset(text)
    if normalized is None:
        raise MetadataWriteError(
            f"时区看不懂：「{text.strip()}」。请填成 +08:00 或 -05:00 这样的写法。",
            detail=text,
        )
    return normalized


__all__ = [
    "CaptureTime",
    "CaptureTimeEdit",
    "plan_capture_time_edit",
]
