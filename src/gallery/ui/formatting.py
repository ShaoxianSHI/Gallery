"""界面文案的纯格式化函数：照片卡片的悬停提示。

文件大小与时间的格式化在 ``core.formatting`` 里 —— 元数据面板也要用同一套，
两边各写一份迟早会在界面里对不上。这里再导出一次，界面侧照旧从这里取。
"""

from __future__ import annotations

from gallery.core.formatting import format_bytes, format_timestamp
from gallery.core.library.photos import GroupRef, PhotoRef


def photo_tooltip(ref: PhotoRef) -> str:
    """照片卡片上的悬停提示。"""
    return "\n".join(
        (
            ref.name,
            f"{ref.format_label} · {format_bytes(ref.size)}",
            format_timestamp(ref.mtime_ns),
            str(ref.path.parent),
        )
    )


def group_tooltip(group: GroupRef) -> str:
    """分组卡片上的悬停提示：叫什么、有多少张、双击会怎样。"""
    cover = group.cover
    return "\n".join(
        (
            group.label,
            f"{group.count} 张照片",
            "双击进入这个分组",
            f"封面：{cover.name}" if cover is not None else "",
        )
    ).rstrip("\n")


__all__ = ["format_bytes", "format_timestamp", "group_tooltip", "photo_tooltip"]
