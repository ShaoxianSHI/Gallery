"""界面文案的纯格式化函数：文件大小、时间、工具提示。

不依赖 Qt，可以直接单测 —— 这类「差一个单位」「时区错一小时」的问题，
靠肉眼在界面上看是发现不了的。
"""

from __future__ import annotations

from datetime import datetime

from gallery.core.photos import PhotoRef

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(count: int) -> str:
    """把字节数格式化成人读的大小，例如 ``1.2 MB``。"""
    if count < 0:
        return "未知大小"
    size = float(count)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover - 上面的循环已经覆盖到最后一项


def format_timestamp(mtime_ns: int) -> str:
    """把 ``st_mtime_ns`` 格式化成本地时间。"""
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")


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
