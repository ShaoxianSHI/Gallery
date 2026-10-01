"""界面文案的纯格式化函数：照片卡片的悬停提示。

文件大小与时间的格式化在 ``core.formatting`` 里 —— 元数据面板也要用同一套，
两边各写一份迟早会在界面里对不上。这里再导出一次，界面侧照旧从这里取。
"""

from __future__ import annotations

from gallery.core.formatting import format_bytes, format_timestamp
from gallery.core.photos import PhotoRef


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


__all__ = ["format_bytes", "format_timestamp", "photo_tooltip"]
