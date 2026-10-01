"""元数据读取，以及写回用户文件所需的两样东西：journal 与重命名。

读取（:mod:`~gallery.core.metadata.exif`）是只读的；真正改动用户文件的只有
:mod:`~gallery.core.metadata.rename`，且必须经由 :mod:`~gallery.core.metadata.journal`
留下可回滚记录。详见 ``docs/adr/0004-rename-on-disk.md``。
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
    undo_last_rename,
    validate_name,
)

__all__ = [
    "ExifToolReader",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "RenameResult",
    "WriteRecord",
    "append_record",
    "build_report",
    "read_metadata",
    "read_records",
    "rename_entry",
    "undo_last_rename",
    "validate_name",
]
