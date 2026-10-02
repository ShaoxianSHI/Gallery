"""元数据读取（单张 / 批量），以及写用户文件所需的几样东西：journal、改名、移到废纸篓、粘贴、移动。

读取是只读的：单张走 :mod:`~gallery.core.metadata.exif`，成批（分组与排序要用）走
:mod:`~gallery.core.metadata.batch`，两边共用 :mod:`~gallery.core.metadata.facets` 里
「可分组 / 可排序字段」的那一份取值规则。真正改动用户文件的只有四样：
:mod:`~gallery.core.metadata.rename`（改名）、:mod:`~gallery.core.metadata.trash`
（移到系统废纸篓）、:mod:`~gallery.core.metadata.paste`（把文件**复制**进某个目录）与
:mod:`~gallery.core.metadata.transfer`（把文件**移动**进某个相册）。四者都必须经由
:mod:`~gallery.core.metadata.journal` 留下记录，而只有前两样与移动进
:mod:`~gallery.core.metadata.undo` 那一摞共用的栈提供 ⌘Z —— 撤销一次粘贴就是删文件
（红线 1，见 ADR 0018），撤销一次移动仍然只是一次移动，所以移动是能撤的（ADR 0019）。
详见 ``docs/adr/0004-rename-on-disk.md``、``docs/adr/0008-delete-to-trash.md``、
``docs/adr/0018-paste-into-current-folder.md`` 与 ``docs/adr/0019-copy-move-to-album.md``。
"""

from gallery.core.metadata.batch import (
    BatchMetadataSource,
    ExifToolBatchSource,
    PillowBatchSource,
    read_facets,
)
from gallery.core.metadata.exif import (
    ExifToolReader,
    MetadataField,
    MetadataGroup,
    MetadataReport,
    MetadataSource,
    build_report,
    read_metadata,
)
from gallery.core.metadata.facets import (
    GROUP_FACETS,
    SORT_FACETS,
    Facet,
    PhotoFacets,
    extract,
    group_facet,
    sort_facet,
)
from gallery.core.metadata.journal import WriteRecord, append_record, read_records
from gallery.core.metadata.naming import MAX_NAME_BYTES, free_name_in
from gallery.core.metadata.paste import (
    Failure,
    PastedFile,
    PasteResult,
    Skipped,
    copy_into,
)
from gallery.core.metadata.rename import (
    RenameResult,
    rename_entry,
    undo_rename_record,
    validate_name,
)
from gallery.core.metadata.transfer import (
    MovedItem,
    MoveResult,
    UndoMoveResult,
    move_into,
    undo_move_records,
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
    "GROUP_FACETS",
    "MAX_NAME_BYTES",
    "SORT_FACETS",
    "BatchMetadataSource",
    "ExifToolBatchSource",
    "ExifToolReader",
    "Facet",
    "Failure",
    "MoveResult",
    "MovedItem",
    "PhotoFacets",
    "PasteResult",
    "PastedFile",
    "PillowBatchSource",
    "Skipped",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "RenameResult",
    "RestoreResult",
    "TrashResult",
    "UndoMoveResult",
    "UndoResult",
    "WriteRecord",
    "append_record",
    "build_report",
    "copy_into",
    "extract",
    "free_name_in",
    "group_facet",
    "move_into",
    "move_to_trash",
    "read_facets",
    "next_undo_label",
    "read_metadata",
    "read_records",
    "rename_entry",
    "restore_entries",
    "sort_facet",
    "trash_entries",
    "undo_last_write",
    "undo_move_records",
    "undo_rename_record",
    "validate_name",
]
