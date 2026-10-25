"""日历的年 / 月 / 周视图。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "calendar.today": "今天",
    "calendar.day_label": "{name}{day}日",
    "calendar.hour_label": "{name}{day}日 {hour:02d}:00",
    "calendar.month_label": "{name}",
    "calendar.photo_count": ("{count} 张", "{count} 张"),
    "calendar.week_header": "周{name}",
}
