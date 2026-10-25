"""Display names for apps and system services (Finder, Maps, Calendar, ...)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "app.finder": "Finder",
    "app.google_maps": "Google Maps",
    "app.google_calendar": "Google Calendar",
    "app.qspace": "QSpace",
    "app.maps": "Maps",
    "app.calendar": "Calendar",
    "app.preview": "Preview",
    "app.imageglass": "ImageGlass",
    "app.picview": "Picview",
    "app.quicktime": "QuickTime Player",
    "app.iina": "IINA",
    "app.cli.description": (
        "Gallery — a local-first photo app: your photos stay in your own folders."
    ),
    "app.cli.library_help": (
        "Library folder to open on launch (omit it to show the welcome page and reopen the "
        "last library)"
    ),
    "app.cli.version_help": "Print version information and exit",
    "app.macos.no_source": (
        "Could not find the Gallery source folder. Run this from the project folder, or use "
        "uv run python -m gallery.macos_app."
    ),
    "app.macos.icon_failed": "Could not generate the icon: {tool} exited with code {code}",
    "app.macos.no_icon_source": "Could not find the app icon source file.",
    "app.macos.missing_sips": "Could not find the macOS sips tool, so the .icns cannot be made.",
    "app.macos.missing_iconutil": (
        "Could not find the macOS iconutil tool, so the .icns cannot be made."
    ),
    "app.macos.destination_taken": (
        "The destination already has something there, and it is not Gallery.app: {destination}. "
        "Choose another location, or delete it first."
    ),
    "app.macos.no_interpreter": (
        "Could not find the virtual environment interpreter: {interpreter}. Run uv sync in the "
        "project folder first."
    ),
    "app.macos.no_icns": "Could not find an existing .icns file.",
    "app.macos.description": (
        "Package Gallery as a macOS Gallery.app so the Dock, Finder, and ⌘Tab show the app icon."
    ),
    "app.macos.destination_help": ("Destination (default: <project folder>/{build_dir}/{app}.app)"),
    "app.macos.generated": "Generated {bundle}",
    "app.macos.launch_hint": (
        "Double-click to launch; the Dock, Finder, and ⌘Tab all show the Gallery icon."
    ),
    "app.macos.venv_hint": (
        "This bundle uses the repository's .venv and does not include dependencies; regenerate "
        "it after moving the repository or rebuilding it with uv sync."
    ),
}
