"""用户在编辑器里改的**拍摄时间**，翻译成要写进文件的字段（ADR 0032）。

**这一层只做翻译，不碰文件**：输入是「照片此刻的拍摄时间」与「用户填完的那一份」，输出是
一组 :class:`~gallery.core.metadata.writeback.Assignment`。真正写盘的是
:func:`gallery.core.metadata.writeback.write_metadata`，它记账、校验、回滚、可撤销。

## 为什么时间可以改，而相机与曝光不行

ADR 0030 的判据是「这条值是设备的事实记录，还是人的标注」，并据此把拍摄时间划进了
「不可写」那一侧。那条判据本身不推翻，它的适用范围被收窄了（ADR 0032）：

- 相机型号、光圈、快门、ISO、焦距是**测量结果** —— 机身测出来的客观量，改它等于伪造证据；
- 拍摄时间是**机内时钟的一次读数** —— 对不对取决于用户有没有把时区设对、有没有换过电池、
  GPS 有没有对过时。它**本来就需要人来校准**，而现实中有一整类照片的时间是确定错了的。

## 时区：单独一个字段，**绝不替用户猜**

EXIF 的 ``DateTimeOriginal`` 存的是**墙上时间**（拍照地的钟点，``2024:05:16 20:52:02``），
既不是 UTC 也没有换算。时区在单独的可选字段 ``OffsetTimeOriginal`` 里，相机十有八九不写它。

于是这里是两个字段、两个独立的问题：

| 用户填的 | 写下去的结果 |
|---|---|
| 时间 + 偏移 | 两个字段都写 |
| 时间，留空偏移 | 只写时间，偏移被**删掉** |
| 两个都留空 | 两个字段都删掉 |
| 只改偏移 | 只写偏移 —— 「相机时区设错了」正是这一种 |

**留空 = 照片里不记这一项**，与位置那一侧的空框语义一致（ADR 0031）：编辑器始终是
「访达的显示简介」那一种，打开时看到的就是最终状态，不需要另设「改了这一项」的勾。

照片没写偏移时，编辑器的偏移栏就是空的 —— **绝不按本机时区补一个**。凭空替用户猜时区，
猜错的后果是排序整体偏几个小时（见 :func:`~gallery.core.metadata.facets.parse_capture_time`
对「没写偏移」的处置），比空着严重得多。

## 只写 ``DateTimeOriginal``，不碰 ``CreateDate`` / ``DateTimeDigitized`` / ``ModifyDate``

后几个记的是**文件处理过程**（「什么时候被创建的」「什么时候被数字化的」），与「这张照片
什么时候拍的」不是一回事。用户改拍摄时间时不动它们。
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

    def display(self) -> str:
        """面板上那一行的写法：``2024-05-16 20:52:02``，带得上偏移就跟在后面。"""
        if self.taken_at is None:
            return ""
        text = self.taken_at.replace(":", "-", 2)
        return f"{text} {self.offset}" if self.offset else text


@dataclass(frozen=True, slots=True)
class CaptureTimeEdit:
    """用户在编辑器里填完的那一份。**空 = 照片里不记这一项**（与位置同一套语义）。"""

    taken_at: str | None = None
    offset: str | None = None


def plan_capture_time_edit(state: CaptureTime, edit: CaptureTimeEdit) -> tuple[Assignment, ...]:
    """把「用户填完的那一份」翻译成要写进文件的字段。

    **空串 = 把这个字段删掉**（exiftool 的写法）。

    值没变的字段一个字都不写：那正是「打开对话框什么都没动就按保存」，把同样的值再写一遍
    只会让日志里多一条空记录，而 ⌘Z 会因此去撤一件空事。

    :raises MetadataWriteError: 时间或时区看不懂（不存在的日期也会在这里被挡住）。
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
