"""English.

以中文为准翻译（ADR 0106 决策 2）：结果词用 ``could not`` / ``cannot`` / ``not yet``，
一条横幅仍然是「结果 + 能否撤销」。引号用 ``“”``，句末用 ``.``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "banner.quote": "“{text}”",
    "banner.punct.separator": "; ",
    "banner.punct.period": ".",
    "banner.detail": "{detail}.",
    "banner.log_failed": "; could not write the operation log",
    "banner.log_failed.plain": "could not write the operation log",
    "banner.undoable": "⌘Z can undo this",
    "banner.unexpected": (
        "Something unexpected happened. Details were written to the log file: {path}."
    ),
    "banner.rename.journaled": "Renamed to “{name}”.",
    "banner.rename.not_journaled": (
        "Renamed to “{name}”; could not write the operation log, so ⌘Z cannot undo this."
    ),
    "banner.event.kind": "event",
    "banner.event.created": "Saved as “{name}”.",
    "banner.event.updated.unchanged": "“{name}” was left unchanged.",
    "banner.event.updated.journaled": "Updated “{name}”; ⌘Z can undo this.",
    "banner.event.updated.not_journaled": (
        "Updated “{name}”; could not write the operation log, so ⌘Z cannot undo this."
    ),
    "banner.event.deleted.base": "Deleted “{name}”{photos}",
    "banner.event.deleted.photos": (
        ", and removed its records from {count} photo",
        ", and removed its records from {count} photos",
    ),
    "banner.event.deleted.undoable": ". ⌘Z can undo this",
    "banner.event.deleted.not_undoable": (
        ". Could not write the operation log, so ⌘Z cannot undo this"
    ),
    "banner.photos.bound.already": "These photos are already in “{name}”; nothing changed.",
    "banner.photos.bound.by_range": (
        "Recorded {count} photo in “{name}” by date range.",
        "Recorded {count} photos in “{name}” by date range.",
    ),
    "banner.photos.bound.done": (
        "Recorded {count} photo in “{name}”.",
        "Recorded {count} photos in “{name}”.",
    ),
    "banner.photos.unbound.none": "These photos were not in “{name}”; nothing changed.",
    "banner.photos.unbound.done": (
        "Removed {count} photo from “{name}”.",
        "Removed {count} photos from “{name}”.",
    ),
    "banner.event.cover.set": "Set “{photo}” as the cover of “{name}”. ⌘Z can undo this.",
    "banner.event.cover.cleared": (
        "Cleared the cover of “{name}” and went back to the first photo. ⌘Z can undo this."
    ),
    "banner.shows.imported.none": "No importable shows were found in the spreadsheet.",
    "banner.shows.imported.no_new": "No new shows",
    "banner.shows.imported.done": (
        "Imported {count} show from “演迹”",
        "Imported {count} shows from “演迹”",
    ),
    "banner.shows.imported.duplicates": ", skipped {count} that already existed",
    "banner.shows.imported.skipped": (
        ", and {count} row was not imported",
        ", and {count} rows were not imported",
    ),
    "banner.movies.imported.none": "No movies were imported.",
    "banner.movies.imported.created": ("Imported {count} movie", "Imported {count} movies"),
    "banner.movies.imported.duplicates": "skipped {count} that already existed",
    "banner.movies.imported.more": (", {count} in total", ", {count} in total"),
    "banner.movie_site.opened": "Opened the website for “{code}” in the browser.",
    "banner.movie_site.failed": "Could not open the website for “{code}”: {detail}.",
    "banner.event_undo.restored": "Restored {what}{note}",
    "banner.event_undo.changed": "Undid changes to {what}{note}",
    "banner.event_undo.what": "{kind} “{name}”",
    "banner.event_undo.log_failed": (
        "; could not write the operation log, so the next ⌘Z may not work."
    ),
    "banner.orphan.head.any": "Some event records point to photos that no longer exist.",
    "banner.orphan.head.named": (
        "{count} event record points to a photo that no longer exists, involving {names}.",
        "{count} event records point to photos that no longer exist, involving {names}.",
    ),
    "banner.orphan.why": (
        "The photos may have been deleted or moved outside Gallery. Keeping these records does "
        "no harm,\nbut if another photo later takes the same path, it will be counted in the "
        "original event."
    ),
    "banner.orphan.ask": (
        "Clean up this {count} record?",
        "Clean up these {count} records?",
    ),
    "banner.orphan.vanished": (
        "Another {count} record points to an album that no longer exists{who}.\n"
        "The disk may also be unmounted, so they are kept by default. Clean them up as well?",
        "Another {count} records point to albums that no longer exist{who}.\n"
        "The disk may also be unmounted, so they are kept by default. Clean them up as well?",
    ),
    "banner.orphan.vanished.who": ", involving {names}",
    "banner.orphan.list.separator": ", ",
    "banner.orphan.list.more": (" and {count} event", " and {count} events"),
    "banner.orphan.title": (
        "{count} event record points to a photo that no longer exists",
        "{count} event records point to photos that no longer exist",
    ),
    "banner.orphans.forgotten.none": "No records were cleaned up.",
    "banner.orphans.forgotten.done": (
        "Cleaned up {count} record that pointed to a missing photo; the photo itself was "
        "not touched.",
        "Cleaned up {count} records that pointed to missing photos; the photos themselves were "
        "not touched.",
    ),
    "banner.album.done.paste": (
        "Pasted {count} item into “{target}”",
        "Pasted {count} items into “{target}”",
    ),
    "banner.album.done.copy": (
        "Copied {count} item into “{target}”",
        "Copied {count} items into “{target}”",
    ),
    "banner.album.done.move": (
        "Moved {count} item into “{target}”",
        "Moved {count} items into “{target}”",
    ),
    "banner.album.renamed.one": ", “{source}” was renamed to “{created}”",
    "banner.album.renamed.many": (
        ", {count} item was renamed because the name was taken",
        ", {count} items were renamed because the names were taken",
    ),
    "banner.album.failed.paste": "Could not paste {name}{more}: {reason}",
    "banner.album.failed.copy": "Could not copy {name}{more}: {reason}",
    "banner.album.failed.move": "Could not move {name}{more}: {reason}",
    "banner.album.failure.more": (", {count} in total", ", {count} in total"),
    "banner.album.stayed.single": (
        "{name} was already in “{target}”, so a copy was made as “{created}”"
    ),
    "banner.album.stayed.many": (
        "Another {count} item was already in “{target}”, so a copy of each was made",
        "Another {count} items were already in “{target}”, so a copy of each was made",
    ),
    "banner.duplicate.created": ("Created {count} copy", "Created {count} copies"),
    "banner.duplicate.named": ", named “{name}”",
    "banner.trash.none.journaled": "Nothing was moved: {reason}.",
    "banner.trash.none.not_journaled": (
        "Nothing was moved: {reason}; could not write the operation log."
    ),
    "banner.trash.album": "Moved the album “{name}” and its contents to the Trash",
    "banner.trash.single": "Moved “{name}” to the Trash",
    "banner.trash.many": (
        "Moved {count} item to the Trash",
        "Moved {count} items to the Trash",
    ),
    "banner.trash.failures": (
        ", and {count} item could not be moved: {reason}",
        ", and {count} items could not be moved: {reason}",
    ),
    "banner.trash.hint.journaled": "⌘Z can undo this.",
    "banner.trash.hint.not_journaled": (
        "Could not write the operation log, so ⌘Z cannot undo this."
    ),
    "banner.undo.partial": (
        "Partially undone: {count} item was changed outside Gallery and left alone{note}.",
        "Partially undone: {count} items were changed outside Gallery and left alone{note}.",
    ),
    "banner.undo.metadata": "Undone; the location of “{name}” was restored{note}.",
    "banner.undo.rename": "Undone; restored to “{name}”{note}.",
    "banner.undo.move.failed": "Could not move them back: {reason}{note}.",
    "banner.undo.move.done": "Undone; the photos were moved back to their original album{note}.",
    "banner.undo.restore.failed": "Could not restore: {reason}{note}.",
    "banner.undo.restore.one": "Restored “{name}” from the Trash",
    "banner.undo.restore.many": (
        "Restored {count} item from the Trash",
        "Restored {count} items from the Trash",
    ),
    "banner.undo.restore.failures": (
        ", and {count} item could not be restored: {reason}",
        ", and {count} items could not be restored: {reason}",
    ),
    "banner.album_cover.set": "Set “{photo}” as the cover of “{album}”.",
    "banner.album_cover.cleared": "“{album}” uses its first photo as the cover again.",
    "banner.metadata.none": "The metadata of “{name}” was left unchanged.",
    "banner.metadata.journaled": "Updated the {what} of “{name}”; ⌘Z can undo this.",
    "banner.metadata.not_journaled": (
        "Updated the {what} of “{name}”; could not write the operation log, so ⌘Z cannot undo this."
    ),
    "banner.metadata.what.both": "capture time and location",
    "banner.metadata.what.capture_time": "capture time",
    "banner.metadata.what.place": "location",
    "banner.reason.unknown": "reason unknown",
    "banner.reason.named": "“{name}”: {reason}",
    "banner.reason.some": "one of them: {reason}",
    "banner.map.opened.app": "Opened the shooting location of this photo in “{name}”.",
    "banner.map.opened.browser": "Opened {name} in the browser.",
    "banner.external.opened.app": "Opened the current page in {name}.",
    "banner.external.opened.browser": "Opened {name} in the browser.",
    "banner.calendar.opened": "Opened the date for the current page in “{name}”.",
    "banner.desktop.clipboard.empty": "There are no files to copy.",
    "banner.desktop.clipboard.copied.single": "Copied “{name}” to the clipboard.",
    "banner.desktop.clipboard.copied.many": (
        "Copied {count} item to the clipboard.",
        "Copied {count} items to the clipboard.",
    ),
    "banner.desktop.open.default_failed": (
        "Could not open “{name}” with the default app. Check that the file is still there."
    ),
    "banner.desktop.open.no_files": "There are no files to open.",
    "banner.desktop.open.app_refused": (
        "Could not hand off to {name}; opened “{file}” with the default app instead."
    ),
    "banner.desktop.open.app.single": "Opened “{file}” in {name}.",
    "banner.desktop.open.app.many": (
        "Opened {count} item in {name}.",
        "Opened {count} items in {name}.",
    ),
    "banner.desktop.reveal.no_paths": "There are no paths to show.",
    "banner.desktop.reveal.fallback": (
        "Could not select the files; opened “{folder}” in Finder instead."
    ),
    "banner.desktop.finder.no_response": (
        "Finder did not respond. Check the system privacy settings."
    ),
    "banner.desktop.reveal.single": "Revealed “{name}” in Finder.",
    "banner.desktop.reveal.many": (
        "Revealed {count} item in Finder.",
        "Revealed {count} items in Finder.",
    ),
    "banner.desktop.folder.no_folders": "There are no folders to open.",
    "banner.desktop.folder.single": "Opened “{name}” in Finder.",
    "banner.desktop.folder.many": (
        "Opened {count} folder in Finder.",
        "Opened {count} folders in Finder.",
    ),
    "banner.desktop.qspace.no_response": (
        "QSpace did not respond, and Finder could not open them either. Check the system privacy "
        "settings."
    ),
    "banner.desktop.qspace.folder_fallback.single": (
        "Could not hand off to QSpace; opened “{name}” in Finder instead."
    ),
    "banner.desktop.qspace.folder_fallback.many": (
        "Could not hand off to QSpace; opened {count} folder in Finder instead.",
        "Could not hand off to QSpace; opened {count} folders in Finder instead.",
    ),
    "banner.desktop.qspace.folder.single": "Opened “{name}” in QSpace.",
    "banner.desktop.qspace.folder.many": (
        "Opened {count} folder in QSpace.",
        "Opened {count} folders in QSpace.",
    ),
    "banner.desktop.qspace.reveal_fallback": "Could not hand off to QSpace. {message}",
    "banner.desktop.qspace.reveal.single": "Revealed “{name}” in QSpace.",
    "banner.desktop.qspace.reveal.many": (
        "Revealed {count} item in QSpace.",
        "Revealed {count} items in QSpace.",
    ),
}
