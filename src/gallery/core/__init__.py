"""Gallery 核心层。

本层为纯 Python 实现：不得导入 PySide6/Qt，不得假设存在事件循环，不得直接与用户交互。
界面层（``gallery.ui``）只能通过本层的公开接口访问文件系统。

按领域分成四个子包：

- ``gallery.core.library``   库：目录扫描、条目模型、库配置、会话状态
- ``gallery.core.media``     像素：解码与缩略图磁盘缓存
- ``gallery.core.metadata``  元数据读取，以及 journal 与重命名这两处写回
- ``gallery.core.places``    离线地名查询

留在本层顶部的只有跨域共享的基础设施：常量、异常、原子读写、库内目录布局与展示格式化。
界面层从这里取名字，具体的子模块路径只在核心层内部使用。
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
