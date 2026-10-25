"""Grid and cards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "browser.photo_count": ("{count} photo", "{count} photos"),
    "browser.album_badge": "Album",
}
