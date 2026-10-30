"""Library config, search, index, and backdrop store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # ---- Library configuration -------------------------------------------------
    "library.config.edge_range": (
        "The thumbnail_max_edge setting must be between {minimum} and {maximum}; "
        "the current value is {value}."
    ),
    "library.config.negative_cache": (
        "The max_cache_bytes setting cannot be negative; the current value is {value} "
        "(0 means no limit)."
    ),
    "library.config.parse_failed": (
        "The configuration file has a syntax error and cannot be parsed: {path}. Fix it and try "
        "again (or delete the file to restore the defaults)."
    ),
    "library.config.write_failed": (
        "Could not write the configuration file: {path} ({reason}). Check that the folder is "
        "writable."
    ),
    "library.config.covers_not_table": (
        "The {key} setting must be a table (of the form [{key}]); the current value is {value!r}."
    ),
    "library.config.cover.album_label": "the album “{album}” under {key}",
    "library.config.cover.photo_label": "the cover of {label}",
    "library.config.cover.not_string": (
        "The cover of {label} must be written as a relative path to a photo (a string); "
        "the current value is {value!r}."
    ),
    "library.config.cover.outside_album": (
        "The cover “{photo}” of {label} is not inside that album; the cover must be a photo "
        "inside it, for example “{key}/photo.jpg”."
    ),
    "library.config.relative.empty": "{label} cannot be an empty path.",
    "library.config.relative.absolute": (
        "{label} “{value}” must be a path relative to the library root: it cannot start with / "
        "or contain .."
    ),
    "library.config.not_bool": (
        "The {key} setting must be true or false; the current value is {value!r}."
    ),
    "library.config.not_int": (
        "The {key} setting must be an integer; the current value is {value!r}."
    ),
    "library.config.music_not_string": (
        "The slideshow_music setting must be the path to a music file (a string); "
        "the current value is {value!r}."
    ),
    "library.config.movie_site.required": (
        'The movie_site setting must be the prefix of a movie website, for example "{default}"; '
        "the current value is {value!r}."
    ),
    "library.config.movie_site.scheme": (
        "The movie_site setting must start with http:// or https://; the current value is "
        '{value!r}. For example "{default}".'
    ),
    "library.config.movie_site.host": (
        "The movie_site setting has a scheme but no host; the current value is {value!r}. "
        'For example "{default}".'
    ),
    # ---- Library facade --------------------------------------------------------
    "library.facade.open.missing": (
        "Could not open this folder: {path}. It may have been moved, renamed, or deleted."
    ),
    "library.facade.open.not_folder": (
        "This path is not a folder: {path}. Choose a folder that contains photos."
    ),
    "library.facade.open.no_permission": (
        "Gallery does not have permission to read this folder: {path}. Grant access in "
        "“System Settings → Privacy & Security → Files and Folders” and try again."
    ),
    "library.facade.cache_limit_invalid": (
        "The thumbnail cache limit must be an integer no smaller than 0 (0 means no limit)."
    ),
    "library.facade.config_not_writable": (
        "The configuration folder of this library is not writable, so settings cannot be saved: "
        "{path}. Libraries on read-only volumes or network shares can only be browsed; their "
        "settings cannot be changed."
    ),
    "library.facade.outside_library": ("Cannot browse a folder outside the library: {path}."),
    "library.facade.cover.photo_outside": (
        "“{name}” is not inside the current library, so it cannot be used as a cover."
    ),
    "library.facade.cover.unsupported": (
        "“{name}” is not a format supported by Gallery; choose another file and try again."
    ),
    "library.facade.cover.not_in_album": (
        "The cover must be a photo inside “{album}”; “{photo}” is not in that album."
    ),
    "library.facade.cover.not_folder": ("“{name}” is not a folder, so a cover cannot be set."),
    "library.facade.cover.root": (
        "The library root is not an album and is not a card in the grid, so it has no cover to set."
    ),
    "library.facade.cover.album_outside": (
        "“{name}” is not inside the current library, so a cover cannot be set."
    ),
    "library.facade.cover.no_config_dir": (
        "The current library cannot create the {name} folder, so the cover setting has nowhere "
        "to be saved. Move the folder to a writable location and try again."
    ),
    "library.facade.music.missing": (
        "“{name}” is not there, so it cannot be used as slideshow music. Choose the file again."
    ),
    "library.facade.movie.failure": "{code}: {reason}",
    "library.facade.movie.cover_failure": "{code}: could not set the cover, {reason}",
    "library.facade.video_decode": (
        "Video is not decoded as an image: the player provides the picture for {name}."
    ),
    # ---- Index -----------------------------------------------------------------
    "library.index.unavailable": (
        "The current library cannot build a search index: the disk holding the photos usually "
        "does not allow cache writes. Grouping and sorting are still available."
    ),
    "library.index.by_date_unavailable": (
        "The current library cannot build an index: the disk holding the photos usually does not "
        "allow cache writes, so photos cannot be selected by date range."
    ),
    "library.index.search_failed": "Search could not be completed; try again.",
    "library.index.match_failed": "Filtering could not be completed; try again.",
    "library.index.by_date_failed": (
        "Selecting photos by date range could not be completed; try again."
    ),
    # ---- File recognition ------------------------------------------------------
    "library.photos.unknown_format": "Unknown",
    # ---- Scanning --------------------------------------------------------------
    "library.scanner.folders_unreadable": (
        "{count} folder could not be read",
        "{count} folders could not be read",
    ),
    "library.scanner.files_unreadable": (
        "{count} file could not be read",
        "{count} files could not be read",
    ),
    "library.scanner.separator": ", ",
    "library.scanner.issue_suffix": (
        "; this is usually caused by a permissions problem or a disconnected external disk."
    ),
    "library.scanner.folder_unavailable": (
        "Could not open this folder: {path}. It may have been moved, renamed, or deleted."
    ),
    "library.watcher.processing_failed": (
        "A file change was detected, but processing it failed; details were written to the log "
        "file: {path}."
    ),
    # ---- Search fields ---------------------------------------------------------
    "library.search.field.event_kind": "Event type",
    "library.search.field.event": "Event",
    "library.search.field.name": "File name",
    "library.search.field.folder": "Album",
    "library.search.field.format": "File format",
    "library.search.field.size": "File size",
    "library.search.field.captured": "Captured",
    "library.search.field.camera": "Camera",
    "library.search.field.lens": "Lens",
    "library.search.field.country": "Country",
    "library.search.field.region": "State/province",
    "library.search.field.city": "City",
    "library.search.field.keyword": "Keywords",
    "library.search.field.aperture": "Aperture",
    "library.search.field.shutter": "Shutter speed",
    "library.search.field.focal": "Focal length",
    "library.search.field.pixels": "Pixel dimensions",
    "library.search.field.modified": "Modified",
    "library.search.hint.event": "Kyoto in five days",
    "library.search.hint.folder": "includes subalbums",
    "library.search.hint.format": "jpg or JPEG",
    "library.search.hint.size": "5MB or >10MB",
    "library.search.hint.date": "2024 / 2024-05",
    "library.search.hint.aperture": "1.8 or < 2",
    "library.search.hint.shutter": "0.002 or > 0.001",
    "library.search.hint.iso": "800 or >1600",
    "library.search.hint.focal": "35 or <= 70",
    "library.search.hint.pixels": "12000000 or >12MP",
    "library.search.event_field_label": "{kind}·{field}",
    "library.search.reason.reversed": (
        " has a reversed range: “{left}” comes after “{right}”; the start must come first."
    ),
    "library.search.reason.date": (
        "A date is required: 2024, 2024-05, or 2024-05-16; "
        "write a range as 2023{range}2024; spaces are allowed around {range}, "
        "and {half} is also accepted."
    ),
    "library.search.reason.value": "This field needs a value, for example {hint}.",
    # ---- Calendar --------------------------------------------------------------
    "library.calendar.level.year": "Y",
    "library.calendar.level.month": "M",
    "library.calendar.level.week": "W",
    "library.calendar.weekday.sunday": "Sun",
    "library.calendar.weekday.monday": "Mon",
    "library.calendar.weekday.tuesday": "Tue",
    "library.calendar.weekday.wednesday": "Wed",
    "library.calendar.weekday.thursday": "Thu",
    "library.calendar.weekday.friday": "Fri",
    "library.calendar.weekday.saturday": "Sat",
    "library.calendar.weekday.header": "{name}",
    "library.calendar.month.1": "January",
    "library.calendar.month_short.1": "Jan",
    "library.calendar.month.2": "February",
    "library.calendar.month_short.2": "Feb",
    "library.calendar.month.3": "March",
    "library.calendar.month_short.3": "Mar",
    "library.calendar.month.4": "April",
    "library.calendar.month_short.4": "Apr",
    "library.calendar.month.5": "May",
    "library.calendar.month_short.5": "May",
    "library.calendar.month.6": "June",
    "library.calendar.month_short.6": "Jun",
    "library.calendar.month.7": "July",
    "library.calendar.month_short.7": "Jul",
    "library.calendar.month.8": "August",
    "library.calendar.month_short.8": "Aug",
    "library.calendar.month.9": "September",
    "library.calendar.month_short.9": "Sep",
    "library.calendar.month.10": "October",
    "library.calendar.month_short.10": "Oct",
    "library.calendar.month.11": "November",
    "library.calendar.month_short.11": "Nov",
    "library.calendar.month.12": "December",
    "library.calendar.month_short.12": "Dec",
    "library.calendar.title.year": "{year}",
    "library.calendar.title.month": "{name} {year}",
    "library.calendar.title.week_span": (
        "{start_name} {start_day}, {start_year} – {end_name} {end_day}"
    ),
    "library.calendar.title.week": "{span} · Week {number}",
    # ---- Backdrop store --------------------------------------------------------
    "library.backdrop.read_image": "Could not read this backdrop image: {name}",
    "library.backdrop.write_cache": "Could not write the backdrop image to the cache: {path}",
    "library.backdrop.read_photo": "Could not read this photo: {path}",
    "library.backdrop.decode_photo": (
        "Could not decode this photo; choose another one and try again: {path}"
    ),
    "library.backdrop.missing": "This backdrop no longer exists; it may have just been deleted.",
    "library.backdrop.rebuilt_recipe": ("The backdrop “{name}” was regenerated from its recipe"),
    "library.backdrop.rebuilt_source": (
        "The backdrop “{name}” was reimported from the original photo"
    ),
    "library.backdrop.registry_unwritable": ("The backdrop list cannot be written: {path}"),
    "library.backdrop.name_required": "Give this backdrop a name.",
    "library.places.loading": "Loading the city database…",
    "library.backdrop.default_name": "Background",
}
