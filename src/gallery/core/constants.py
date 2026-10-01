"""库级别的路径与名称常量。

**这里是配置目录名的唯一来源。** 其他任何模块都不得硬编码 ``.gallery`` 字面量，
而应导入 ``CONFIG_DIR_NAME``；由 ``tests/test_guards.py`` 强制保证。
"""

from __future__ import annotations

from typing import Final

#: 库根目录下用于存放配置、索引与缓存的隐藏目录名。
#: 注意拼写是 gallery，不是 galley。
CONFIG_DIR_NAME: Final = ".gallery"

#: 库级配置文件名。它是 ``.gallery/`` 中唯一建议纳入版本控制的内容。
CONFIG_FILE_NAME: Final = "config.toml"

#: ``.gallery/`` 下存放可丢弃缓存的子目录名。
CACHE_DIR_NAME: Final = "cache"

#: 缩略图缓存目录名（位于 ``.gallery/cache/`` 内）。
THUMBNAIL_DIR_NAME: Final = "thumbs"

#: 元数据写回日志目录名（M5 使用，先占位）。
JOURNAL_DIR_NAME: Final = "journal"

#: SQLite 索引文件名（M2 使用，先占位）。
INDEX_FILE_NAME: Final = "index.db"

#: 应用在系统目录（macOS 为 ``~/Library/Application Support``）下的目录名。
APP_NAME: Final = "Gallery"

#: 缩略图生成算法的版本号。解码方式、缩放滤镜或输出格式变化时递增；
#: 缓存 key 包含它，因此递增会让整批旧缩略图自动失效。
THUMBNAIL_SPEC_VERSION: Final = 1

#: 缩略图长边默认像素数。
DEFAULT_THUMBNAIL_MAX_EDGE: Final = 512

#: 缩略图缓存默认上限（2 GiB）。0 表示不限制。
DEFAULT_MAX_CACHE_BYTES: Final = 2 * 1024**3

#: 网格中缩略图卡片的默认边长（像素，正方形）。
GRID_TILE_EDGE: Final = 200

__all__ = [
    "APP_NAME",
    "CACHE_DIR_NAME",
    "CONFIG_DIR_NAME",
    "CONFIG_FILE_NAME",
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_THUMBNAIL_MAX_EDGE",
    "GRID_TILE_EDGE",
    "INDEX_FILE_NAME",
    "JOURNAL_DIR_NAME",
    "THUMBNAIL_DIR_NAME",
    "THUMBNAIL_SPEC_VERSION",
]
