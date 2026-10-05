"""「库」：对外门面、目录扫描、条目模型、分组与排序、索引、检索、文件监听、库配置与会话状态。

界面层通常只需要 :class:`Library` 与 :func:`prepare_library`；其余按需从具体子模块导入
（扫描 :mod:`~gallery.core.library.scanner`、排序 :mod:`~gallery.core.library.groups`、
日历 :mod:`~gallery.core.library.calendar`、筛选 :mod:`~gallery.core.library.search`、
监听 :mod:`~gallery.core.library.watcher`），**不要经本文件绕圈互相导入**。
"""

from gallery.core.library.calendar import (
    LEVEL_NAMES,
    LEVELS,
    days_of,
    group_by_day,
    group_by_hour,
    shift,
    title,
)
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    LibraryConfig,
    LibraryUIState,
    load_config,
    prepare_library,
    save_config,
)
from gallery.core.library.facade import Library
from gallery.core.library.groups import SortOrder, build_groups, sort_photos
from gallery.core.library.photos import (
    FolderRef,
    GridEntry,
    GroupRef,
    PhotoRef,
    format_label,
    is_supported_photo,
)

__all__ = [
    "BROWSE_MODE_ALBUMS",
    "BROWSE_MODE_CALENDAR",
    "BROWSE_MODE_FLAT",
    "LEVELS",
    "LEVEL_NAMES",
    "FolderRef",
    "GridEntry",
    "GroupRef",
    "Library",
    "LibraryConfig",
    "LibraryUIState",
    "PhotoRef",
    "SortOrder",
    "build_groups",
    "days_of",
    "format_label",
    "group_by_day",
    "group_by_hour",
    "is_supported_photo",
    "load_config",
    "prepare_library",
    "save_config",
    "shift",
    "sort_photos",
    "title",
]
