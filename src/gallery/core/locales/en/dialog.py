"""Dialogs: metadata, events, backgrounds, movie import, and the rest."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "dialog.close": "Close",
    "dialog.cancel": "Cancel",
    "dialog.save": "Save",
    "dialog.done": "Done",
    "dialog.delete": "Delete",
    "dialog.rename": "Rename…",
    "dialog.remove": "Remove",
    "dialog.name": "Name",
    "dialog.light": "Use as Light",
    "dialog.dark": "Use as Dark",
    "dialog.event.edit_title": "Edit Event",
    "dialog.event.new_title": "New {kind}",
    "dialog.event.edit_named_title": "Edit “{name}”",
    "dialog.event.clear_cover": "Clear Cover",
    "dialog.event.no_cover": "Not set (uses the first photo)",
    "dialog.event.cover_pending": "(pending)",
    "dialog.event.include_range": "Include photos between the start and end dates",
    "dialog.event.multi_values": "Separate multiple values with commas",
    "dialog.event.delete": "Delete Event…",
    "dialog.order.title": "External App Order",
    "dialog.order.hint": ("Drag to reorder; the first app appears on the left half of the button."),
    "dialog.order.reset": "Restore This Page's Default Order",
    "dialog.appearance.wrong_side": (
        "Pick a light color for the light background and a dark color for the dark background: "
        "text color does not follow the background, so the wrong side becomes hard to read."
    ),
    "dialog.appearance.light_slot": "Light Mode",
    "dialog.appearance.dark_slot": "Dark Mode",
    "dialog.appearance.title": "Background Color",
    "dialog.appearance.hint": (
        "Choose one color for each side; “Default” follows the system light or dark mode."
    ),
    "dialog.appearance.reset": "Restore Defaults",
    "dialog.appearance.slot_title": "{slot} Background Color",
    "dialog.appearance.default": "Default",
    "dialog.appearance.follow_system": "Follows the system light or dark mode",
    "dialog.appearance.change": "{value} (click to change)",
    "dialog.metadata.title": "Edit Metadata",
    "dialog.metadata.title_named": "Edit Metadata for “{name}”",
    "dialog.metadata.capture_time": "Capture Time",
    "dialog.metadata.timezone": "Time Zone",
    "dialog.metadata.country": "Country",
    "dialog.metadata.region": "State / Province",
    "dialog.metadata.city": "City",
    "dialog.metadata.latitude": "Latitude",
    "dialog.metadata.longitude": "Longitude",
    "dialog.metadata.names_to_coordinates": "Place → Coordinates",
    "dialog.metadata.names_to_coordinates_hint": (
        "Look up the city named above and fill in its state, country, and coordinates "
        "(levels missing from the city database are cleared)."
    ),
    "dialog.metadata.coordinates_to_names": "Coordinates → Place",
    "dialog.metadata.coordinates_to_names_hint": (
        "Look up the nearest city for the coordinates below and fill in the three place fields "
        "(levels missing from the city database are cleared; for example, a municipality has no "
        "state level)."
    ),
    "dialog.metadata.copy": "Copy",
    "dialog.metadata.copy_time": "Copy Time",
    "dialog.metadata.copy_place": "Copy Location",
    "dialog.metadata.paste": "Paste",
    "dialog.metadata.paste_time": "Paste Time",
    "dialog.metadata.paste_place": "Paste Location",
    "dialog.metadata.clear_place": "Clear Location",
    "dialog.metadata.copied": "Copied",
    "dialog.metadata.error.city_required": (
        "Enter a city first: the city database is searched by city name."
    ),
    "dialog.metadata.error.coordinates_required": "Enter both latitude and longitude first.",
    "dialog.metadata.error.no_nearby_city": (
        "No city was found nearby; the place fields were left unchanged."
    ),
    "dialog.metadata.error.nothing_to_copy": "This photo has nothing to copy.",
    "dialog.metadata.error.clipboard_empty": "The clipboard has nothing usable.",
    "dialog.metadata.error.clipboard_no_time": "The clipboard has no usable capture time.",
    "dialog.metadata.error.clipboard_no_place": "The clipboard has no usable location.",
    "dialog.metadata.error.bad_time": (
        "Could not read the capture time “{text}”. Use the format 2024-05-16 20:52:02; "
        "the seconds may be omitted."
    ),
    "dialog.metadata.error.bad_timezone": (
        "Could not read the time zone “{text}”. Use the format +08:00 or -05:00; "
        "leave it empty to record no time zone."
    ),
    "dialog.metadata.error.coordinates_pair": (
        "Latitude and longitude must be filled in together; one alone cannot locate a place."
    ),
    "dialog.metadata.error.not_a_number": (
        "{label} is not a valid number: “{text}”. Use decimal degrees, for example 30.27415."
    ),
    "dialog.metadata.error.out_of_range": "{label} is out of range ({limit}): got {value}.",
    "dialog.metadata.error.city_not_found": (
        "The city database has no “{city}”; the state, country, and coordinates were left as "
        "they were."
    ),
    "dialog.backdrop.generate_title": "Generate Ink Background",
    "dialog.backdrop.palette_hint": (
        "Choose a palette; double-click a swatch to edit its color, or delete the palette. "
        "Palettes added with “+” and edits to presets are both saved."
    ),
    "dialog.backdrop.add_palette": "Add a Palette",
    "dialog.backdrop.reset_palettes": "Restore Preset Palettes",
    "dialog.backdrop.reset_palettes_tip": (
        "Restore the eight presets to their original colors; palettes added with “+” are untouched"
    ),
    "dialog.backdrop.reroll": "Shuffle",
    "dialog.backdrop.chip_tip": "{title} (double-click to edit colors)",
    "dialog.backdrop.new_palette": "New Palette",
    "dialog.backdrop.name_required": "Give this background a name.",
    "dialog.backdrop.palette_colors_hint": (
        "Top to bottom runs light to dark; use at least two colors, and click a swatch to change "
        "it."
    ),
    "dialog.backdrop.add_color": "Add Color…",
    "dialog.backdrop.colour_dialog_title": "Palette Color",
    "dialog.backdrop.add_colour_title": "Add a Color",
    "dialog.backdrop.delete_palette": "Delete This Palette",
    "dialog.backdrop.delete_palette_tip": (
        "Delete this palette; backgrounds already saved are not affected"
    ),
    "dialog.backdrop.colour_swatch_tip": "{colour} (click to change)",
    "dialog.backdrop.photo_title": "Use a Photo as Background",
    "dialog.backdrop.photo_hint": (
        "A copy of the photo will be added to the Gallery cache; the original file is not changed."
    ),
    "dialog.backdrop.save_only": "Save Only",
    "dialog.backdrop.manage_title": "Manage Backgrounds",
    "dialog.backdrop.manage_hint": (
        "Renaming and deleting only affect copies in the Gallery cache; the original photos are "
        "not changed. Changes apply when you click Done; Cancel saves nothing."
    ),
    "dialog.backdrop.empty": "No saved backgrounds",
    "dialog.backdrop.edit_title": "Edit Colors · {title}",
    "dialog.backdrop.custom": "Custom",
    "dialog.backdrop.palette_limit": (
        "You can add at most {count} palettes (delete one of your own to add another)."
    ),
    "dialog.movie.start": "Import",
    "dialog.movie.folder": "Save to: {path}",
    "dialog.movie.example_required": "Enter at least one ID or link, for example {example}.",
    "dialog.movie.bilibili.title": "Import Bilibili Movie",
    "dialog.movie.bilibili.heading": "Import a Bilibili movie by BV ID or link",
    "dialog.movie.bilibili.hint": (
        "One BV ID or Bilibili video address per line (open b23.tv short links in a browser first "
        "and paste the full address). Covers are saved to the folder below."
    ),
    "dialog.movie.youtube.title": "Import YouTube Movie",
    "dialog.movie.youtube.heading": "Import a YouTube movie by link or videoId",
    "dialog.movie.youtube.hint": (
        "One YouTube address or 11-character videoId per line (both youtu.be and watch links "
        "work). Covers are saved to the folder below."
    ),
    "dialog.movie.douyin.title": "Import Douyin Movie",
    "dialog.movie.douyin.heading": "Import a Douyin movie by link or ID",
    "dialog.movie.douyin.hint": (
        "One Douyin video address or share link (starting with v.douyin.com) per line; you can "
        "also enter the video ID. A short link must be expanded online once to find its video. "
        "Covers are saved to the folder below."
    ),
    "dialog.movie.tiktok.title": "Import TikTok Movie",
    "dialog.movie.tiktok.heading": "Import a TikTok movie by link or ID",
    "dialog.movie.tiktok.hint": (
        "One TikTok video address or share link (starting with vm.tiktok.com or tiktok.com/t) "
        "per line; you can also enter the video id. Covers are saved to the folder below."
    ),
    "dialog.movie.javdb.title": "Import JavDB Movie",
    "dialog.movie.javdb.heading": "Import a JavDB movie by code",
    "dialog.movie.javdb.hint": (
        "One code per line, for example IPX-660; case and hyphens are up to you. Covers are "
        "saved to the folder below."
    ),
    "dialog.movie.iqiyi.title": "Import iQIYI Movie",
    "dialog.movie.iqiyi.heading": "Import an iQIYI movie by link",
    "dialog.movie.iqiyi.hint": (
        "One iQIYI video address per line; the part after v_ is the ID. Album addresses for a "
        "whole series (starting with a_) are not supported; open one episode and copy that "
        "address. Covers are saved to the folder below."
    ),
    "dialog.movie.tencent.title": "Import Tencent Video Movie",
    "dialog.movie.tencent.heading": "Import a Tencent Video movie by link or vid",
    "dialog.movie.tencent.hint": (
        "One Tencent Video address or 11-character vid per line; series collection addresses are "
        "not supported, so open one episode and copy that address. Covers are saved to the folder "
        "below."
    ),
    "dialog.movie.youku.title": "Import Youku Movie",
    "dialog.movie.youku.heading": "Import a Youku movie by link or ID",
    "dialog.movie.youku.hint": (
        "One Youku video address or ID (the string starting with X) per line. Covers are saved "
        "to the folder below."
    ),
    "dialog.movie.mango.title": "Import Mango TV Movie",
    "dialog.movie.mango.heading": "Import a Mango TV movie by link or ID",
    "dialog.movie.mango.hint": (
        "One Mango TV video address (the /b/… page) or episode ID per line. Series pages "
        "(starting with /h/) are not supported; open one episode and copy that address. Mango "
        "does not provide a release date for some movies, so that field is left empty. Covers "
        "are saved to the folder below."
    ),
    "dialog.movie.pornhub.title": "Import Pornhub Movie",
    "dialog.movie.pornhub.heading": "Import a Pornhub movie by link or ID",
    "dialog.movie.pornhub.hint": (
        "One Pornhub video address per line, or the ID after viewkey= in the address (both "
        "view_video.php and /embed/ addresses work). For author and collection pages, open one "
        "video first and copy that address. Covers are saved to the folder below."
    ),
}
