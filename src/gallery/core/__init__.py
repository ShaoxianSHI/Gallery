"""Gallery 核心层。

本层为纯 Python 实现：不得导入 PySide6/Qt，不得假设存在事件循环，不得直接与用户交互。
界面层（``gallery.ui``）只能通过本层的公开接口访问文件系统。
"""

from gallery.core.config import LibraryConfig, load_config, prepare_library
from gallery.core.constants import CONFIG_DIR_NAME, CONFIG_FILE_NAME
from gallery.core.errors import GalleryError
from gallery.core.images import RasterImage
from gallery.core.library import Library
from gallery.core.photos import PhotoRef

__all__ = [
    "CONFIG_DIR_NAME",
    "CONFIG_FILE_NAME",
    "GalleryError",
    "Library",
    "LibraryConfig",
    "PhotoRef",
    "RasterImage",
    "load_config",
    "prepare_library",
]
