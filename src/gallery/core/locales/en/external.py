"""Messages and display names for external apps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "external.launch.browser_failed": "Could not open the browser",
    "external.launch.maps_failed": (
        "Could not open Maps. Check that the system Maps app is still installed."
    ),
    "external.launch.app_failed": "Could not launch that app",
    "external.launch.browser_not_started": "The browser did not start",
    "external.launch.window_unrecognized": "Could not recognize that app window",
    "external.launch.window_rewrite_failed": "Could not change that window's address",
    "external.launch.fallback_both_failed": ("{reason}, and the browser could not open either"),
    "external.launch.fallback_browser": "{reason}; opened it in the browser instead",
    "external.apple_calendar.not_authorized": (
        "Could not control Calendar. Allow Gallery to control Calendar in System Settings › "
        "Privacy & Security › Automation."
    ),
    "external.apple_calendar.not_responding": "Calendar did not respond",
    "external.apple_calendar.not_found": "Could not find the system Calendar app",
    "external.apple_calendar.open_failed": "Could not open the system Calendar app",
    "external.errors.source_page": "The source site has no such page: {url}",
}
