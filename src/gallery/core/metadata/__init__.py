"""元数据读取，以及写回用户文件所需的三样东西：journal、重命名、移到废纸篓。

读取（:mod:`~gallery.core.metadata.exif`）是只读的；真正改动用户文件的只有
:mod:`~gallery.core.metadata.rename`（改名）与 :mod:`~gallery.core.metadata.trash`
（移到系统废纸篓），两者都必须经由 :mod:`~gallery.core.metadata.journal` 留下可回滚
记录，并由 :mod:`~gallery.core.metadata.undo` 这一摞共用的栈提供 ⌘Z。
详见 ``docs/adr/0004-rename-on-disk.md`` 与 ``docs/adr/0008-delete-to-trash.md``。
"""

from gallery.core.metadata.exif import (
    ExifToolReader,
    MetadataField,
    MetadataGroup,
    MetadataReport,
    MetadataSource,
    build_report,
    read_metadata,
)
from gallery.core.metadata.journal import WriteRecord, append_record, read_records
from gallery.core.metadata.rename import (
    RenameResult,
    rename_entry,
    undo_rename_record,
    validate_name,
)
from gallery.core.metadata.trash import (
    RestoreResult,
    TrashResult,
    move_to_trash,
    restore_entries,
    trash_entries,
)
from gallery.core.metadata.undo import UndoResult, next_undo_label, undo_last_write

__all__ = [
    "ExifToolReader",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "RenameResult",
    "RestoreResult",
    "TrashResult",
    "UndoResult",
    "WriteRecord",
    "append_record",
    "build_report",
    "move_to_trash",
    "next_undo_label",
    "read_metadata",
    "read_records",
    "rename_entry",
    "restore_entries",
    "trash_entries",
    "undo_last_write",
    "undo_rename_record",
    "validate_name",
]
