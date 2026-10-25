"""Decode and frame-extraction errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "media.decode.cancelled": "Reading cancelled: {name}.",
    "media.decode.gone": "Could not decode {name}: the file is no longer there.",
    "media.decode.corrupt": ("Could not decode {name}: the file may be damaged or truncated."),
    "media.decode.bomb": (
        "This image has more pixels than a photo would; it was not opened for safety: {name}."
    ),
    "media.decode.unreachable": (
        "Could not read {name}: the file may not have finished downloading, for example it is "
        "still syncing from the cloud, or another app may be using it. Wait for the download to "
        "finish, then open it again."
    ),
    "media.decode.heif_missing": (
        "HEIC/HEIF decoding support is missing, so {name} cannot be opened. Install pillow-heif "
        "to open it."
    ),
    "media.decode.unidentified": (
        "{name} is not a recognizable image. Its contents may not match the extension."
    ),
    "media.video.unsupported": "This machine cannot extract video frames: {name}.",
    "media.video.unreadable": (
        "Could not read the first frame of {name}: the system does not support its codec, or the "
        "file has not finished downloading. Wait for the download to finish, then open it again."
    ),
}
