"""Settings menu (ADR 0073 / 0099)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "settings.title": "Settings",
    "settings.theme": "Theme",
    "settings.animation": "Animation Speed",
    "settings.slideshow": "Slideshow Interval",
    "settings.movie_site": "Movie Site",
    "settings.cache_limit": "Cache Limit",
    "settings.background": "Background",
    "settings.external_order": "External App Order…",
    "settings.open_last": "Open Last Library at Launch",
    "settings.log_folder": "Open Log Folder",
    "settings.about": "About {name}",
    "settings.about.body": "{name} {version}\n\nLibrary: {where}",
    "settings.custom": "Custom…",
    "settings.speed.slow": "Slow",
    "settings.speed.normal": "Normal",
    "settings.speed.fast": "Fast",
    "settings.speed.off": "Off",
    "settings.appearance.system": "Follow System",
    "settings.appearance.light": "Light",
    "settings.appearance.dark": "Dark",
    "settings.page.detail": "Photos",
    "settings.page.video": "Videos",
    "settings.page.grid": "Grid",
    "settings.page.calendar": "Calendar",
    "settings.cache.unlimited": "Unlimited",
    "settings.cache.custom": "Custom Size",
    "settings.cache.set": "Thumbnail cache limit set to {label}.",
    "settings.language": "Language",
    "settings.language.system": "Follow System",
    "settings.language.restart": "Takes effect after restarting Gallery.",
    "settings.seconds": "{seconds} s",
    "settings.save_failed": ("The settings could not be saved; they still apply to this run."),
    "settings.movie_site.prompt": "Address prefix for code pages (for example https://jable.tv/videos):",
    "settings.log_folder.failed": "Could not open the log folder: {folder}",
    "settings.no_library": "No library is open",
    "settings.photo_filter": (
        "Images (*.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff *.webp);;All Files (*)"
    ),
    "settings.background.solid": "Set Solid Background…",
    "settings.background.light": "Light Background",
    "settings.background.dark": "Dark Background",
    "settings.background.generate": "Generate Ink Background…",
    "settings.background.photo": "Use a Photo as Background…",
    "settings.background.manage": "Manage Backgrounds…",
    "settings.background.use_solid": "Use a Solid Color",
    "settings.background.empty": "No saved backgrounds",
    "settings.background.empty_banner": "There are no saved backgrounds.",
    "settings.background.saved": (
        "Saved the background “{name}”; choose it under “Background › Light Background / "
        "Dark Background”."
    ),
    "settings.background.solid_banner.light": "The light background is now a solid color.",
    "settings.background.solid_banner.dark": "The dark background is now a solid color.",
    "settings.background.set_banner.light": "The light background is now “{name}”.",
    "settings.background.set_banner.dark": "The dark background is now “{name}”.",
    "settings.background.pick_title": "Choose a Photo as Background",
}
