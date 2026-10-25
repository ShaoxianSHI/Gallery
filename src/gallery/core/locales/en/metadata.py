"""Metadata field names, reports, and write-back messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # ---- Formatting ------------------------------------------------------------
    "metadata.format.unknown_size": "Unknown size",
    # ---- Field labels (reports and grouping) -----------------------------------
    "metadata.label.camera": "Camera",
    "metadata.label.lens": "Lens",
    "metadata.label.captured": "Captured",
    "metadata.label.year": "Capture year",
    "metadata.label.month": "Capture month",
    "metadata.label.day": "Capture date",
    "metadata.label.event": "Event",
    "metadata.label.country": "Country",
    "metadata.label.region": "State / province",
    "metadata.label.city": "City",
    "metadata.label.keyword": "Keywords",
    "metadata.label.exposure_time": "Exposure time",
    "metadata.label.aperture": "Aperture",
    "metadata.label.exposure_bias": "Exposure compensation",
    "metadata.label.exposure_program": "Exposure program",
    "metadata.label.metering": "Metering mode",
    "metadata.label.flash": "Flash",
    "metadata.label.focal_length": "Focal length",
    "metadata.label.focal_35mm": "35mm equivalent",
    "metadata.label.white_balance": "White balance",
    "metadata.label.orientation": "Orientation",
    "metadata.label.place": "Place",
    "metadata.label.sub_location": "Sublocation",
    "metadata.label.coordinates": "Coordinates",
    "metadata.label.altitude": "Altitude",
    "metadata.label.gps_time": "GPS time",
    "metadata.label.pixel_size": "Pixel dimensions",
    "metadata.label.color_space": "Color space",
    "metadata.label.software": "Software",
    "metadata.label.duration": "Duration",
    "metadata.label.frame_rate": "Frame rate",
    "metadata.label.codec": "Codec",
    "metadata.label.bitrate": "Bitrate",
    "metadata.label.rating": "Rating",
    "metadata.label.title": "Title",
    "metadata.label.description": "Description",
    "metadata.label.creator": "Creator",
    "metadata.label.copyright": "Copyright",
    "metadata.label.filename": "File name",
    "metadata.label.folder": "Folder",
    "metadata.label.format": "Format",
    "metadata.label.size": "Size",
    "metadata.label.modified": "Modified",
    "metadata.label.shutter": "Shutter speed",
    "metadata.label.pixels": "Pixel dimensions",
    "metadata.label.unknown": "Unknown",
    # ---- Report groups, values, and composition --------------------------------
    "metadata.group.shooting": "Shooting",
    "metadata.group.location": "Location",
    "metadata.group.image": "Image",
    "metadata.group.video": "Video",
    "metadata.group.content": "Content",
    "metadata.group.file": "File",
    "metadata.value.seconds": "{value} s",
    "metadata.value.uncalibrated": "Uncalibrated",
    "metadata.text.notice": "({notice})",
    "metadata.text.group": "[{title}]",
    "metadata.text.field": "{label}: {value}",
    "metadata.text.coordinates_separator": ", ",
    "metadata.text.list_separator": ", ",
    "metadata.text.clause_separator": "; ",
    "metadata.text.inferred_place": "Place (inferred)",
    # ---- Group names and empty values ------------------------------------------
    "metadata.facets.group.year": "{year}",
    "metadata.facets.group.month": "{month}/{year}",
    "metadata.facets.group.day": "{month}/{day}/{year}",
    "metadata.facets.empty.event_field": "No {label}",
    "metadata.facets.event_field_label": "{kind} · {field}",
    "metadata.empty.time": "Time unknown",
    "metadata.empty.camera": "Camera unknown",
    "metadata.empty.lens": "Lens unknown",
    "metadata.empty.event": "Not in an event",
    "metadata.empty.country": "Country unknown",
    "metadata.empty.region": "Region unknown",
    "metadata.empty.city": "City unknown",
    "metadata.empty.keyword": "No keywords",
    "metadata.empty.folder": "(Library root)",
    "metadata.empty.unknown": "Unknown",
    # ---- Enumerations on the Pillow fallback path ------------------------------
    "metadata.exif.flash.off": "No flash",
    "metadata.exif.flash.on": "Flash",
    "metadata.exif.flash.no_return": "Flash (no return light detected)",
    "metadata.exif.flash.return": "Flash (return light detected)",
    "metadata.exif.flash.forced": "Flash (forced)",
    "metadata.exif.flash.off_forced": "No flash (forced)",
    "metadata.exif.flash.off_auto": "No flash (auto)",
    "metadata.exif.flash.auto": "Flash (auto)",
    "metadata.exif.white_balance.auto": "Auto",
    "metadata.exif.white_balance.manual": "Manual",
    "metadata.exif.orientation.normal": "Normal",
    "metadata.exif.orientation.rotated_180": "Rotated 180°",
    "metadata.exif.orientation.clockwise_90": "90° clockwise",
    "metadata.exif.orientation.counterclockwise_90": "90° counterclockwise",
    "metadata.exif.color_space.uncalibrated": "Uncalibrated",
    "metadata.exif.exposure_program.undefined": "Undefined",
    "metadata.exif.exposure_program.manual": "Manual",
    "metadata.exif.exposure_program.program": "Program",
    "metadata.exif.exposure_program.aperture_priority": "Aperture priority",
    "metadata.exif.exposure_program.shutter_priority": "Shutter priority",
    "metadata.exif.exposure_program.creative": "Creative",
    "metadata.exif.exposure_program.action": "Action",
    "metadata.exif.exposure_program.portrait": "Portrait",
    "metadata.exif.exposure_program.landscape": "Landscape",
    "metadata.exif.metering.unknown": "Unknown",
    "metadata.exif.metering.average": "Average",
    "metadata.exif.metering.center_weighted": "Center-weighted",
    "metadata.exif.metering.spot": "Spot",
    "metadata.exif.metering.multi": "Multi-segment",
    "metadata.exif.metering.evaluative": "Evaluative",
    "metadata.exif.metering.partial": "Partial",
    # ---- exiftool reads --------------------------------------------------------
    "metadata.exif.timeout": "Reading metadata timed out (over {seconds:g} seconds): {name}.",
    "metadata.exif.cannot_run": "Could not run exiftool: {reason}.",
    "metadata.exif.unparsable": "Could not parse exiftool's output: {name}.",
    "metadata.exif.detail_output": "{error}; first 200 characters of output: {output}",
    "metadata.exif.no_payload": "exiftool returned no usable information: {name}.",
    "metadata.exif.unreadable": "Could not read this file: {name}, {reason}.",
    "metadata.exif.not_installed": (
        "exiftool was not found, so the full metadata cannot be read. "
        "Install it and try again: brew install exiftool"
    ),
    "metadata.exif.fallback_notice": (
        "exiftool failed ({reason}); the basic information is shown below."
    ),
    "metadata.exif.pillow_notice": (
        "exiftool is not installed, so only basic EXIF is shown (IPTC / XMP / maker notes are "
        "unavailable). Install it to see the full information: brew install exiftool"
    ),
    "metadata.exif.pillow_unreadable": (
        "Could not read the basic information of this file: {name}, {error}."
    ),
    # ---- One set of failure wordings ------------------------------------------
    "metadata.failure.source_gone": (
        "The file is no longer in its original location; another program may have just moved it."
    ),
    "metadata.failure.same_file": "The source and destination are the same item.",
    "metadata.failure.disk_full": "The disk is full. Free up space and try again.",
    "metadata.failure.permission": "Permission denied; the system returned {reason}. {hint}",
    "metadata.failure.permission_folder_hint": (
        "Check whether access is granted in “System Settings → Privacy & Security → "
        "Files and Folders”."
    ),
    "metadata.failure.permission_file_hint": (
        "Check whether it is read-only or in use by another program."
    ),
    "metadata.failure.name_too_long": "The file name is too long for this disk.",
    "metadata.failure.action.copy": "Copy",
    "metadata.failure.action.move": "Move",
    "metadata.failure.action_failed": (
        "{action} failed: {reason}. It may be in use by another program."
    ),
    "metadata.failure.unknown": "{reason}. It may be in use by another program.",
    # ---- Naming ----------------------------------------------------------------
    "metadata.naming.empty": "The new name cannot be empty.",
    "metadata.naming.reserved": "“{name}” is a reserved name and cannot be used.",
    "metadata.naming.leading_dot": (
        "A name cannot start with “.”: files that start with a dot do not appear in Gallery."
    ),
    "metadata.naming.slash": (
        "A name cannot contain “/”: renaming only changes the name, not the file's location."
    ),
    "metadata.naming.control": ("A name cannot contain return, tab, or other control characters."),
    "metadata.naming.too_long": (
        "The name is too long: {size} bytes, at most {limit} bytes are allowed."
    ),
    # ---- New album -------------------------------------------------------------
    "metadata.album.name_too_long": ("The name “{name}” is too long even after adding a number."),
    "metadata.album.exists": "“{name}” already exists.",
    "metadata.album.create_failed": "Could not create the album: {reason}",
    "metadata.album.not_folder": "“{name}” is not a folder.",
    "metadata.album.guard.outside_root": ("Albums can only be created inside the library: {path}."),
    "metadata.album.guard.is_root": (
        "Cannot create an album in the library root itself; choose a folder."
    ),
    "metadata.album.guard.is_config_dir": (
        "This is Gallery's own configuration folder; an album cannot be created here."
    ),
    # ---- Downloaded bytes written as a new file --------------------------------
    "metadata.download.guard": "Cannot save the cover here: {reason}.",
    "metadata.download.not_folder": "“{name}” is not a folder.",
    "metadata.download.no_name": "No usable name is available in “{name}”.",
    "metadata.download.failed": "Could not save the cover into “{name}”: {reason}.",
    # ---- Capture time ----------------------------------------------------------
    "metadata.capture_time.unrecognized": (
        "Could not read the capture time “{text}”. Use a format such as 2024-05-16 20:52:02; "
        "the seconds are optional."
    ),
    "metadata.capture_time.bad_offset": (
        "Could not read the time zone “{text}”. Use a format such as +08:00 or -05:00."
    ),
    # ---- Paste and duplicate ---------------------------------------------------
    "metadata.paste.not_folder": "“{name}” is not a folder.",
    "metadata.paste.source_gone": "The source file no longer exists.",
    "metadata.paste.into_self": ("A folder cannot be pasted into itself or one of its subfolders."),
    "metadata.paste.name_too_long": "The name is too long even after adding a number.",
    "metadata.paste.exists": (
        "An item with the same name already exists here; it was not overwritten."
    ),
    "metadata.paste.guard.outside_root": (
        "Items can only be pasted into a folder inside the library: {path}."
    ),
    "metadata.paste.guard.is_root": ("Cannot paste into the library root itself; choose a folder."),
    "metadata.paste.guard.is_config_dir": (
        "This is Gallery's configuration folder; photos cannot be pasted here."
    ),
    # ---- Place editing ---------------------------------------------------------
    "metadata.place_edit.pair_required": (
        "Longitude and latitude must be filled in together: one alone cannot determine a location."
    ),
    "metadata.place_edit.latitude_range": (
        "Latitude must be between -90 and 90; the value is {value}."
    ),
    "metadata.place_edit.longitude_range": (
        "Longitude must be between -180 and 180; the value is {value}."
    ),
    "metadata.place_edit.null_island": (
        "Latitude and longitude are both 0: this coordinate is in the Gulf of Guinea and usually "
        "means no location information."
    ),
    # ---- Rename ----------------------------------------------------------------
    "metadata.rename.section_title": (
        "“{name}” is a section header, not a file on disk, so it cannot be renamed. "
        "Select the photos under it and rename them instead."
    ),
    "metadata.rename.group": (
        "“{name}” is a group, not a file on disk, so it cannot be renamed. Double-click to enter "
        "the group, select the photos inside, and rename them."
    ),
    "metadata.rename.target_exists": (
        "An item named “{name}” already exists here; choose another name."
    ),
    "metadata.rename.failed": "Could not rename: {reason}",
    "metadata.rename.undo.missing": (
        "Cannot undo: “{name}” was not found; it may have been moved or deleted."
    ),
    "metadata.rename.undo.occupied": ("Cannot undo: “{name}” is now occupied by another file."),
    "metadata.rename.undo.failed": "Could not undo: {reason}",
    "metadata.rename.suffix_changed": (
        "Cannot change the extension: changing {old} to {new} would make the file an "
        "unsupported format for Gallery."
    ),
    "metadata.rename.guard.outside_root": ("Only items inside the library can be renamed: {path}."),
    "metadata.rename.guard.is_root": (
        "This is the library itself; renaming it would make Gallery lose track of the photos."
    ),
    "metadata.rename.guard.is_config_dir": (
        "This is Gallery's configuration folder and cannot be renamed."
    ),
    # ---- Move into an album ----------------------------------------------------
    "metadata.transfer.not_album": "“{name}” is not an album.",
    "metadata.transfer.album_into_self": (
        "An album cannot be moved into itself or one of its subalbums."
    ),
    "metadata.transfer.name_too_long": "The name is too long even after adding a number.",
    "metadata.transfer.undo.gone_from": (
        "“{current}” is no longer in “{folder}”; it may have been moved."
    ),
    "metadata.transfer.undo.occupied": (
        "Another file now occupies the original location of “{name}”."
    ),
    "metadata.transfer.guard.outside_root": (
        "Photos can only be moved into an album inside the library: {path}."
    ),
    "metadata.transfer.guard.is_root": (
        "Cannot move photos into the library root itself; choose an album."
    ),
    "metadata.transfer.guard.is_config_dir": (
        "This is Gallery's own configuration folder; nothing can be moved into it."
    ),
    # ---- Trash -----------------------------------------------------------------
    "metadata.trash.unavailable": "Could not use the Trash: {reason}",
    "metadata.trash.move_failed": "Could not move to the Trash: {reason}",
    "metadata.trash.section": (
        "A section header is not a file on disk and cannot be removed; select the photos under "
        "it and move them to the Trash."
    ),
    "metadata.trash.group": (
        "A group is not a file on disk and cannot be removed; double-click to enter the group, "
        "select the photos inside, and move them to the Trash."
    ),
    "metadata.trash.restore.missing": (
        "“{name}” is no longer in the Trash; the Trash may have been emptied, or you may have "
        "restored it in Finder."
    ),
    "metadata.trash.restore.occupied": (
        "Another file now occupies the original location of “{name}”."
    ),
    "metadata.trash.name_too_long": (
        "The name “{name}” is too long even after adding a number in the Trash."
    ),
    "metadata.trash.guard.outside_root": ("Only items inside the library can be removed: {path}."),
    "metadata.trash.guard.is_root": (
        "This is the library itself; deleting it would make Gallery lose all photos."
    ),
    "metadata.trash.guard.is_config_dir": (
        "This is Gallery's configuration folder and cannot be deleted."
    ),
    # ---- Undo stack ------------------------------------------------------------
    "metadata.undo.label.rename": "Rename",
    "metadata.undo.label.trash": "Move to Trash",
    "metadata.undo.label.move": "Move to Album",
    "metadata.undo.label.metadata": "Edit metadata",
    "metadata.undo.label.event": "Edit event",
    "metadata.undo.label.event_delete": "Delete event",
    "metadata.undo.label.fallback": "Previous step",
    "metadata.undo.failure.event": "Event",
    "metadata.undo.failure.event_unavailable": (
        "The event store could not be opened this time, so this cannot be undone."
    ),
    # ---- Write-back ------------------------------------------------------------
    "metadata.writeback.install_hint": ("Install it to use this feature: brew install exiftool"),
    "metadata.writeback.not_installed": (
        "exiftool was not found, so photo metadata cannot be edited. {hint}"
    ),
    "metadata.writeback.read_failed": (
        "Could not read the current metadata of “{name}”, so the write cannot be verified."
    ),
    "metadata.writeback.detail_output": "{error}; first 200 characters of output: {output}",
    "metadata.writeback.timeout.read": (
        "Reading metadata timed out after {seconds:g} seconds: {name}. Try again later."
    ),
    "metadata.writeback.timeout.write": (
        "Writing metadata timed out after {seconds:g} seconds: {name}. Try again later."
    ),
    "metadata.writeback.cannot_run": "Could not run exiftool: {reason}.",
    "metadata.writeback.no_fields": "This record has no fields that can be undone.",
    "metadata.writeback.undo.missing": (
        "Cannot undo: “{name}” was not found; it may have been moved or deleted."
    ),
    "metadata.writeback.conflict": (
        "This item was changed outside Gallery; writing the original value back would overwrite "
        "it, so it was left unchanged."
    ),
    "metadata.writeback.nothing": "There are no changes to write.",
    "metadata.writeback.unstorable": (
        "“{name}” cannot store the fields to write: {fields}. This format cannot hold them, so "
        "nothing was written."
    ),
    "metadata.writeback.not_writable": (
        "“{tag}” is not an editable field. Gallery only edits capture time and location."
    ),
    "metadata.writeback.not_photo": (
        "“{name}” is not a photo format supported by Gallery, so its metadata cannot be edited."
    ),
    "metadata.writeback.missing": "File not found: {name}.",
    "metadata.writeback.guard.outside_root": (
        "Only photos inside the library can be edited: {path}."
    ),
    "metadata.writeback.guard.is_root": "This is the library itself, not a photo file.",
    "metadata.writeback.guard.is_config_dir": ("This is Gallery's own configuration folder."),
    "metadata.writeback.permission.read": (
        "Permission denied reading “{name}”: it or its disk may be read-only."
    ),
    "metadata.writeback.permission.write": (
        "Permission denied writing “{name}”: it or its disk may be read-only."
    ),
    "metadata.writeback.disk_full": (
        "The disk is full and “{name}” cannot be written: free up space and try again."
    ),
    "metadata.writeback.unsupported": (
        "This format does not support editing the metadata of “{name}”: {detail}."
    ),
    "metadata.writeback.no_room": (
        "Could not write “{name}”: there is not enough room in the file structure for this field."
    ),
    "metadata.writeback.rollback_failed": (
        "The write did not take effect and the original values could not be restored: {fields}. "
        "Check this photo: {reason}."
    ),
    "metadata.writeback.rollback_failed_detail": ("{detail}; the rollback also failed: {error}"),
    "metadata.writeback.restore_failed": (
        "The write did not take effect and the original values could not be restored. Open this "
        "photo and check these fields: {fields}. This write did not succeed."
    ),
    "metadata.writeback.restore_failed_detail": (
        "{detail}; after the rollback these fields still do not hold their original values: "
        "{fields}"
    ),
    "metadata.writeback.restored": (
        "The write did not take effect; the photo was restored to its original state: {fields}."
    ),
    "metadata.writeback.failed.read": "Could not read the metadata of “{name}”: {detail}",
    "metadata.writeback.failed.write": "Could not write the metadata of “{name}”: {detail}",
    "metadata.writeback.failed_no_detail.read": "Could not read the metadata of “{name}”.",
    "metadata.writeback.failed_no_detail.write": "Could not write the metadata of “{name}”.",
    "metadata.writeback.no_reason": "exiftool gave no reason",
    # ---- Batch reads -----------------------------------------------------------
    "metadata.batch.timeout": (
        "Reading metadata timed out after {seconds:g} seconds for this batch of photos."
    ),
    "metadata.batch.command": "Command: {command} …",
    "metadata.batch.no_payload": "exiftool returned no usable information.",
    "metadata.batch.exit_code": "exit code {code}",
    "metadata.default_album_name": "New Album",
    "metadata.duplicate_suffix": " copy",
}
