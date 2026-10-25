"""人读的格式化：文件大小与时间。

放在 ``core`` 而不是界面层，是因为元数据面板（``core.metadata``）也要用同一套格式。
两边各写一份「差一个单位」的实现，迟早会在界面里对不上。
"""

from __future__ import annotations

from datetime import datetime

from gallery.core.i18n import t

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(count: int) -> str:
    """把字节数格式化成人读的大小，例如 ``1.2 MB``。"""
    if count < 0:
        return t("metadata.format.unknown_size")
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


__all__ = ["format_bytes", "format_timestamp"]
