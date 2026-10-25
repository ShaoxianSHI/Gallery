"""Calendar year, month, and week views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "calendar.today": "Today",
    "calendar.day_label": "{month}/{day}",
    "calendar.hour_label": "{month}/{day} {hour:02d}:00",
    "calendar.month_label": "Month {month}",
    "calendar.photo_count": ("{count} photo", "{count} photos"),
    "calendar.week_header": "{name}",
}
