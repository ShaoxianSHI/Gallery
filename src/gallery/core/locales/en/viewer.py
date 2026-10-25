"""Detail page and video page."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "viewer.metadata.event_title": "{kind} · {name}",
    "viewer.metadata.copy_all": "Copy All",
    "viewer.metadata.edit": "Edit Metadata",
    "viewer.metadata.show_all": "Show All Fields",
    "viewer.metadata.empty": "No photo is open",
    "viewer.metadata.loading": "Reading metadata…",
    "viewer.metadata.loading.named": "Reading metadata…\n{name}",
    "viewer.metadata.edit_tooltip": (
        "Changing the capture time or location writes back to the photo (⌘Z can undo this)"
    ),
    "viewer.metadata.all_fields": "All Fields",
    "viewer.metadata.not_editable": "This cannot be edited",
    "viewer.metadata.clipboard.all_fields": "[All Fields]",
    "viewer.metadata.clipboard.event": "[{name}]",
    "viewer.metadata.clipboard.field": "{label}: {value}",
    "viewer.metadata.copied": "Copied",
    "viewer.video.play_tooltip": "Play / pause (Space)",
    "viewer.video.unplayable": "Could not play this video",
    "viewer.video.unplayable.hint": "{reason}. Press ⇧⏎ to open it with the default app.",
}
