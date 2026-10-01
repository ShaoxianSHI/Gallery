"""「库」：对外门面、目录扫描、条目模型、库配置与会话状态。

界面层通常只需要 :class:`Library` 与 :func:`prepare_library`；只有需要自己遍历
目录（相册封面、逐层列举）时才直接用 :mod:`~gallery.core.library.scanner`。
内部模块一律从具体子模块导入，避免与本文件的再导出互相绕圈。
"""

from gallery.core.library.config import (
    LibraryConfig,
    load_config,
    prepare_library,
    save_config,
)
from gallery.core.library.facade import Library
from gallery.core.library.photos import (
    FolderRef,
    GridEntry,
    PhotoRef,
    format_label,
    is_supported_photo,
)

__all__ = [
    "FolderRef",
    "GridEntry",
    "Library",
    "LibraryConfig",
    "PhotoRef",
    "format_label",
    "is_supported_photo",
    "load_config",
    "prepare_library",
    "save_config",
]
