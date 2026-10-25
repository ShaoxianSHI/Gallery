"""Window action flows: paste, events, ordering, search, thumbnails, and the rest.

以中文为准翻译（ADR 0106 决策 2）：结果词用 ``could not`` / ``cannot`` / ``not yet``，
引号用 ``“”``，句末用 ``.``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "flow.common.cancel": "Cancel",
    "flow.common.skip_duplicates": "Skip Duplicates",
    "flow.common.import_all": "Import All",
    # ---- write_ops ---------------------------------------------------------
    "flow.write_ops.group_not_file": (
        "Groups are not files on disk, so they cannot be renamed or deleted. Double-click to "
        "enter the group, select photos inside it, and try again."
    ),
    "flow.write_ops.nothing_selected": "There is nothing to act on.",
    "flow.write_ops.no_paste_target": "There is no folder to paste into.",
    "flow.write_ops.clipboard_empty": "The clipboard has no files to paste.",
    "flow.write_ops.busy": "The previous operation has not finished yet.",
    "flow.write_ops.pasting": (
        "Pasting {count} item into “{target}”…",
        "Pasting {count} items into “{target}”…",
    ),
    "flow.write_ops.paste_failed": "Could not paste: {reason}",
    "flow.write_ops.duplicating": ("Creating {count} copy…", "Creating {count} copies…"),
    "flow.write_ops.duplicate_failed": "Could not copy: {reason}",
    "flow.write_ops.no_album_target": "There is no folder to create an album in.",
    "flow.write_ops.album_created": "Created album “{name}”.",
    "flow.write_ops.log_failed": "Could not write the operation log.",
    "flow.write_ops.no_cover_photo": "There is no photo to set as the album cover.",
    "flow.write_ops.no_editable_photo": "There is no photo whose metadata can be edited.",
    "flow.write_ops.reading_metadata": "Reading metadata for {name}…",
    "flow.write_ops.metadata_target_changed": (
        "The current photo has changed. Run “Edit Metadata” again."
    ),
    "flow.write_ops.metadata_unreadable": (
        "Could not read the metadata of “{name}”, so it cannot be edited."
    ),
    "flow.write_ops.transferring.copy": (
        "Copying {count} item to “{target}”…",
        "Copying {count} items to “{target}”…",
    ),
    "flow.write_ops.transferring.move": (
        "Moving {count} item to “{target}”…",
        "Moving {count} items to “{target}”…",
    ),
    "flow.write_ops.transfer_failed.copy": "Could not copy: {reason}",
    "flow.write_ops.transfer_failed.move": "Could not move: {reason}",
    "flow.write_ops.nothing_to_undo": "There is nothing to undo.",
    "flow.write_ops.events_unavailable.movies": (
        "Events are unavailable in this library, so movies cannot be imported."
    ),
    "flow.write_ops.import_busy": "The previous import has not finished yet.",
    "flow.write_ops.no_library": "Open a library first.",
    "flow.write_ops.planning_movies": (
        "Fetching details for {count} movie…",
        "Fetching details for {count} movies…",
    ),
    "flow.write_ops.writing_movies": (
        "Downloading and writing covers for {count} movie…",
        "Downloading and writing covers for {count} movies…",
    ),
    "flow.write_ops.movie_import_failed": "Could not import movies: {reason}",
    "flow.write_ops.movie_import_title": "Import Movies",
    "flow.write_ops.movie_import_duplicates": (
        "Found {count} movie, {duplicates} already in the library (same source and ID).\n\n"
        "Skip them or import everything again?",
        "Found {count} movies, {duplicates} already in the library (same source and ID).\n\n"
        "Skip them or import everything again?",
    ),
    # ---- event_flow --------------------------------------------------------
    "flow.event_flow.nothing_to_bind": "Select photos before recording them to an event.",
    "flow.event_flow.nothing_to_unbind": "Select the photos to remove from this event first.",
    "flow.event_flow.import_filter": "Excel Spreadsheet (*.xlsx *.xlsm)",
    "flow.event_flow.import_movie.latin": "Import {source}…",
    "flow.event_flow.import_movie.cjk": "Import {source}…",
    "flow.event_flow.action.edit_event": "Edit Event…",
    "flow.event_flow.action.leave_event": "Remove from This Event",
    "flow.event_flow.action.clear_event_cover": "Clear Event Cover",
    "flow.event_flow.action.import_shows": "Import from “演迹”…",
    "flow.event_flow.event_missing": "That event no longer exists.",
    "flow.event_flow.movie_no_page": (
        "This movie has no web page to open: the ID may be empty, or the site only provides "
        "pages in its app."
    ),
    "flow.event_flow.menu.record_as": "Record As",
    "flow.event_flow.menu.edit_event": "Edit Event",
    "flow.event_flow.menu.new_event": "New Event",
    "flow.event_flow.menu.import_movies": "Import Movies",
    "flow.event_flow.menu.import_shows": "Import Shows",
    "flow.event_flow.menu.set_event_cover": "Set as Event Cover",
    "flow.event_flow.choose_shows_file": "Choose a spreadsheet exported from “演迹”",
    "flow.event_flow.events_unavailable.shows": (
        "Events are unavailable in this library, so shows cannot be imported."
    ),
    "flow.event_flow.duplicate_shows": (
        "The spreadsheet has {count} show, {duplicates} with the same name, date, and venue "
        "as an existing record.\n\nSkip them or import everything again?",
        "The spreadsheet has {count} shows, {duplicates} with the same name, date, and venue "
        "as an existing record.\n\nSkip them or import everything again?",
    ),
    "flow.event_flow.new_event_item": "New {kind}…",
    "flow.event_flow.delete_event.plain": ("Delete “{name}”?\n\n⌘Z can restore these records."),
    "flow.event_flow.delete_event.with_photos": (
        "Delete “{name}” and its records on {count} photo?\n\n⌘Z can restore these records.",
        "Delete “{name}” and its records on {count} photos?\n\n⌘Z can restore these records.",
    ),
    "flow.event_flow.orphan.keep": "Keep Them",
    "flow.event_flow.orphan.clear_missing": "Clean Up These",
    "flow.event_flow.orphan.clear_vanished_too": "Also Clean Up Missing Albums",
    "flow.event_flow.orphan.clear_all": "Clean Up These Too",
    # ---- ordering ----------------------------------------------------------
    "flow.ordering.purpose.filter": "filtering",
    "flow.ordering.purpose.group": "grouping",
    "flow.ordering.purpose.sort": "sorting",
    "flow.ordering.facet_progress": (
        "Reading metadata for {count} photo for {purpose}…",
        "Reading metadata for {count} photos for {purpose}…",
    ),
    "flow.ordering.exiftool_required": (
        "Reading this field requires exiftool. Install it with: brew install exiftool"
    ),
    "flow.ordering.group.none": "No Grouping",
    "flow.ordering.group": "Group",
    "flow.ordering.sort": "Sort",
    "flow.ordering.ascending": "Ascending",
    "flow.ordering.descending": "Descending",
    "flow.ordering.layout.cards": "Cards",
    "flow.ordering.layout.sections": "Sections",
    "flow.ordering.empty.filtered": "No photos in this level match the filters.",
    "flow.ordering.empty.search": "No photos match the current filters.",
    "flow.ordering.empty.group": "There are no photos in this group.",
    "flow.ordering.empty.album": "This folder has no subfolders and no photos to show.",
    "flow.ordering.empty.level": (
        "No photos or videos to show. Supported formats: JPEG, PNG, HEIC, TIFF, GIF, WebP, and "
        "BMP; MP4, MOV, M4V, AVI, and 3GP."
    ),
    "flow.ordering.progress": "Reading metadata… {done} / {total}",
    "flow.ordering.facet_failed": ("Could not read metadata; showing the default order: {reason}"),
    # ---- library_flow ------------------------------------------------------
    "flow.library_flow.choose_folder": "Choose Photos Folder",
    "flow.library_flow.config_dir_failed": (
        "Could not write to “{name}”: Gallery cannot create its configuration folder there. "
        "Browsing, grouping, and search still work, but the index will not be saved this time "
        "and will need to be read again the next time you open it."
    ),
    "flow.library_flow.scan_issues": "Could not read some content: {summary}",
    "flow.library_flow.scan_failed": "Scan failed: {reason}",
    "flow.library_flow.place_progress": (
        "Resolving the location of {count} photo…",
        "Resolving the locations of {count} photos…",
    ),
    "flow.library_flow.place_progress_processed": ("Resolving photo locations… {count} processed"),
    "flow.library_flow.recent_empty": "(No recently opened libraries)",
    "flow.library_flow.clear_recent": "Clear Recent Libraries",
    # ---- search_flow -------------------------------------------------------
    "flow.search_flow.filter_failed": "Could not finish filtering: {reason}",
    "flow.search_flow.search_failed": "Could not finish searching: {reason}",
    "flow.search_flow.unusable": (
        "Could not use “{field}”: {reason} The unrecognized part will not be added to the filters."
    ),
    "flow.search_flow.exiftool_required": (
        "exiftool is not installed, so the “{field}” field cannot be read. File name, album, "
        "format, and size are unaffected. Install it with: brew install exiftool"
    ),
    "flow.search_flow.no_library.filter": "Open a library first, then filter.",
    "flow.search_flow.no_library.search": "Open a library first, then search.",
    "flow.search_flow.index_progress": (
        "Building the search index… {count} photo",
        "Building the search index… {count} photos",
    ),
    "flow.search_flow.index_progress_processed": (
        "Building the search index… {count} processed",
        "Building the search index… {count} processed",
    ),
    "flow.search_flow.index_unavailable.filter": (
        "This library cannot build the search index, so filtering is not available yet."
    ),
    "flow.search_flow.index_unavailable.search": (
        "This library cannot build the search index, so search is not available yet."
    ),
    # ---- context_menu_flow -------------------------------------------------
    "flow.context_menu.copy_paths": ("Copied {count} path.", "Copied {count} paths."),
    "flow.context_menu.metadata_read_failed": "Could not read metadata: {reason}",
    "flow.context_menu.metadata_copied": "Copied the metadata of “{name}”.",
    "flow.context_menu.copy_to_album": "Copy to Album",
    "flow.context_menu.move_to_album": "Move to Album",
    "flow.context_menu.group_condition": (
        "Grouped by “{field}”: {label} · {count} photo",
        "Grouped by “{field}”: {label} · {count} photos",
    ),
    "flow.context_menu.group_copied": ("Copied the grouping condition: grouped by “{field}”."),
    # ---- thumbnail_flow ----------------------------------------------------
    "flow.thumbnail_flow.failed": (
        "Could not show a thumbnail for {count} file: {reason}",
        "Could not show thumbnails for {count} files: {reason}",
    ),
    "flow.thumbnail_flow.clear_title": "Clear Thumbnail Cache",
    "flow.thumbnail_flow.clear_body": (
        "This will delete the thumbnail cache for “{name}”.\n"
        "The photos themselves are unaffected, and the cache will be rebuilt automatically "
        "the next time you browse."
    ),
    "flow.thumbnail_flow.clear_busy": "The previous cleanup has not finished yet.",
    "flow.thumbnail_flow.clearing": "Clearing the thumbnail cache…",
    "flow.thumbnail_flow.cleared": (
        "Cleared {count} thumbnail cache file.",
        "Cleared {count} thumbnail cache files.",
    ),
    "flow.thumbnail_flow.clear_failed": "Could not clear the thumbnail cache: {reason}",
    # ---- metadata_flow -----------------------------------------------------
    "flow.metadata_flow.no_city": "No city records near these coordinates",
    "flow.metadata_flow.distance": "{label} (nearest city about {distance:.0f} km away)",
    "flow.metadata_flow.exiftool_required": (
        "Changing a photo's location requires exiftool. Install it with: brew install exiftool"
    ),
    "flow.metadata_flow.looking_up_city": "Looking up the nearest city…",
    "flow.metadata_flow.no_coordinates": ("Could not infer a location: no coordinates were read"),
    "flow.metadata_flow.lookup_failed": "Could not infer a location: {reason}",
}
