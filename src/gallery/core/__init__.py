"""Gallery 核心层。

本层为纯 Python 实现：不得导入 PySide6/Qt，不得假设存在事件循环，不得直接与用户交互；
界面层（``gallery.ui``）只能通过本层的公开接口访问文件系统。按领域分成
``library``（库）、``media``（像素）、``metadata``（元数据与写回）、``places``（离线地名）四个子包。
"""

from gallery.core.constants import CONFIG_DIR_NAME, CONFIG_FILE_NAME
from gallery.core.errors import GalleryError
from gallery.core.library import (
    Library,
    LibraryConfig,
    PhotoRef,
    load_config,
    prepare_library,
)
from gallery.core.media import RasterImage

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
