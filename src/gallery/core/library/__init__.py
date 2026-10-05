"""「库」：对外门面、目录扫描、条目模型、分组与排序、索引、检索、文件监听、库配置与会话状态。

界面层通常只需要 :class:`Library` 与 :func:`prepare_library`；只有需要自己遍历
目录（相册封面、逐层列举）时才直接用 :mod:`~gallery.core.library.scanner`，需要自己
排顺序（网格重排、测试）时才用 :mod:`~gallery.core.library.groups`，按时间浏览
（年 / 月 / 周 / 日）时才用 :mod:`~gallery.core.library.calendar`，要写一条筛选条件
时才用 :mod:`~gallery.core.library.search`（搜索框与网格之间只有
:class:`~gallery.core.library.search.SearchCondition` 这一个数据结构）。文件监听经由
:meth:`Library.watch`，其事件合并规则在 :mod:`~gallery.core.library.watcher`。
内部模块一律从具体子模块导入，避免与本文件的再导出互相绕圈。
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
