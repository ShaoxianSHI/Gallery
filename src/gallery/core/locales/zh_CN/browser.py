"""网格与卡片。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "browser.photo_count": ("{count} 张", "{count} 张"),
    "browser.album_badge": "相册",
}
