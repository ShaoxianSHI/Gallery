"""元数据读取（单张 / 批量），以及写回用户文件所需的三样东西：journal、重命名、移到废纸篓。

读取是只读的：单张走 :mod:`~gallery.core.metadata.exif`，成批（分组与排序要用）走
:mod:`~gallery.core.metadata.batch`，两边共用 :mod:`~gallery.core.metadata.facets` 里
「可分组 / 可排序字段」的那一份取值规则。真正改动用户文件的只有
:mod:`~gallery.core.metadata.rename`（改名）与 :mod:`~gallery.core.metadata.trash`
（移到系统废纸篓），两者都必须经由 :mod:`~gallery.core.metadata.journal` 留下可回滚
记录，并由 :mod:`~gallery.core.metadata.undo` 这一摞共用的栈提供 ⌘Z。
详见 ``docs/adr/0004-rename-on-disk.md`` 与 ``docs/adr/0008-delete-to-trash.md``。
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
    "GROUP_FACETS",
    "SORT_FACETS",
    "BatchMetadataSource",
    "ExifToolBatchSource",
    "ExifToolReader",
    "Facet",
    "PhotoFacets",
    "PillowBatchSource",
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
    "extract",
    "group_facet",
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
    "undo_rename_record",
    "validate_name",
]
