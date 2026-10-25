"""Copy from the window flows: banners, errors, and status text."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "window.map.no_location": "This photo has no shooting location, so the map cannot be opened.",
    "window.external.open_failed": "Could not open {name}: {detail}",
    "window.calendar.apple_open_failed": "Could not open “Calendar”: {detail}",
    "window.external.app_not_found": (
        "Could not find {name}. It may have been moved or uninstalled."
    ),
    "window.folder.no_library": "No library is open, so there is no folder to open.",
    "window.thumb.label.small": "Small",
    "window.thumb.label.medium": "Medium",
    "window.thumb.label.large": "Large",
    "window.thumb.describe": "Thumbnails: {label}, target {edge} px",
    "window.thumb.describe_actual": "Thumbnails: {label} · actual {edge} px",
    "window.watch.failed": (
        "{detail}. The interface will not update automatically for now. Press ⌘R to rescan the "
        "current level; reopening the library restores automatic updates."
    ),
    "window.calendar.reading": (
        "Reading the capture time of {count} photo for the calendar…",
        "Reading the capture time of {count} photos for the calendar…",
    ),
    "window.calendar.read_failed": "The calendar could not read capture times: {detail}",
    "window.title.drill": ("{label} · {count} photo", "{label} · {count} photos"),
    "window.fill_window": "Fill Window",
    "window.restore_window": "Restore Window",
    "window.slideshow.empty": "There are no photos at this level.",
}
