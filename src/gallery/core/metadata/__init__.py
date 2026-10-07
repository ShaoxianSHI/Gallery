"""元数据读取（单张 / 批量），以及五样会动用户文件的操作：改名、移到废纸篓、粘贴、移动到相册、
新建相册。读取只读（单张走 ``exif``、成批走 ``batch``，共用 ``facets`` 的取值规则）；那五样写操作
一律先经 :mod:`~gallery.core.metadata.journal` 记账，而 ⌘Z 只认其中三样 —— 撤销粘贴与撤销新建相册
都是删东西（红线 1：删除只允许用户亲手触发）。

「复制」⌘D（:func:`~gallery.core.metadata.paste.duplicate`，ADR 0044）不算第六样写操作：
它就是 :func:`~gallery.core.metadata.paste.copy_into` 那一段，目标目录改成每项自己的父目录。
"""

from gallery.core.metadata.album import (
    DEFAULT_ALBUM_NAME,
    CreatedAlbum,
    create_album,
)
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
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import WriteRecord, append_record, read_records
from gallery.core.metadata.naming import MAX_NAME_BYTES, clean_name, free_name_in
from gallery.core.metadata.paste import PastedFile, PasteResult, copy_into, duplicate
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
    "DEFAULT_ALBUM_NAME",
    "GROUP_FACETS",
    "MAX_NAME_BYTES",
    "SORT_FACETS",
    "BatchMetadataSource",
    "CreatedAlbum",
    "ExifToolBatchSource",
    "ExifToolReader",
    "Facet",
    "Failure",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "MoveResult",
    "MovedItem",
    "PasteResult",
    "PastedFile",
    "PhotoFacets",
    "PillowBatchSource",
    "RenameResult",
    "RestoreResult",
    "TrashResult",
    "UndoMoveResult",
    "UndoResult",
    "WriteRecord",
    "append_record",
    "build_report",
    "clean_name",
    "copy_into",
    "create_album",
    "duplicate",
    "extract",
    "free_name_in",
    "group_facet",
    "move_into",
    "move_to_trash",
    "next_undo_label",
    "read_facets",
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
