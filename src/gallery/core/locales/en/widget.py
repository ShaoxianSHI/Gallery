"""Window widgets: title bar, breadcrumb, welcome page, search field, play button."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "widget.breadcrumb.filtered": "Filtered",
    "widget.breadcrumb.search_results": "Search Results",
    "widget.breadcrumb.count": "{title} ({count})",
    "widget.breadcrumb.filtered_tooltip": (
        "Photos in the current level that match the conditions; press Esc to remove conditions "
        "one at a time"
    ),
    "widget.breadcrumb.search_tooltip": (
        "Search results for the whole library, not just one folder; press Esc to remove "
        "conditions one at a time"
    ),
    "widget.breadcrumb.group_tooltip": "Current group (press Esc to go up one level)",
    "widget.external_button.tooltip": "Open the current page in an external app",
    "widget.play_button.tooltip": "Start a slideshow of every photo in this level",
    "widget.play_button.tooltip_pause": "Pause slideshow",
    "widget.play_button.tooltip_resume": "Resume slideshow",
    "widget.play_button.accessible_name": "Slideshow",
    "widget.search_field.placeholder_search": "Search photos…",
    "widget.search_field.placeholder_filter": "Filter photos…",
    "widget.search_field.tooltip_filter": (
        "Filter the current level with these conditions (on “All Photos” this filters the whole "
        "library; in “Calendar” it filters the calendar)"
    ),
    "widget.search_field.tooltip_search": (
        "Search the whole library (switch to filtering to limit it to the current level)"
    ),
    "widget.welcome.subtitle": (
        "Your photos stay in your own folders; Gallery only lets you browse them."
    ),
    "widget.welcome.open_button": "Open Folder…",
    "widget.welcome.hint": (
        "Gallery never moves, changes, or deletes your photos on its own; renaming and moving to "
        "the Trash happen only when you do it yourself. The first time you open a library, "
        "Gallery creates a {config_dir} folder inside it for runtime data."
    ),
    "widget.welcome.recent_title": "Recently Opened",
}
