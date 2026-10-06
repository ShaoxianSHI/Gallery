"""库级别的路径与名称常量。

``CONFIG_DIR_NAME`` 是配置目录名的唯一来源：别处一律导入它，不得硬编码字面量（守护测试按值扫全仓）。
"""

from __future__ import annotations

from typing import Final

#: 库根目录下用于存放配置、索引与缓存的隐藏目录名。注意拼写是 gallery，不是 galley。
CONFIG_DIR_NAME: Final = ".gallery"

#: 库级配置文件名。它是 ``.gallery/`` 中唯一建议纳入版本控制的内容。
CONFIG_FILE_NAME: Final = "config.toml"

#: ``.gallery/`` 下存放可丢弃缓存的子目录名。缩略图已不住在这里（ADR 0038），但读老库时
#: 还要靠它做一次性清理（见 ``core.library.config.discard_legacy_thumb_cache``）。
CACHE_DIR_NAME: Final = "cache"

#: 缩略图缓存目录名。**位于系统目录内**，按库路径摘要再分一层。
THUMBNAIL_DIR_NAME: Final = "thumbs"

#: 写操作日志目录名（``.gallery/journal/``）：每一次会动用户文件的操作都往里记一笔。
JOURNAL_DIR_NAME: Final = "journal"

#: SQLite 索引文件名。分组 / 排序读的元数据快照存在这里（见 ADR 0009、ADR 0038）。
INDEX_FILE_NAME: Final = "index.db"

#: 事件库文件名（``.gallery/events.db``）：事件定义与照片↔事件的绑定（ADR 0043）。
#:
#: **它是 ``.gallery/`` 里第一份不是缓存的东西**：事件是用户自己填的，重扫照片重建不出来，
#: 所以索引重建与清缓存都不许碰它 —— 那是红线 5 的第一个例外。
EVENTS_FILE_NAME: Final = "events.db"

#: 旧版库外索引子目录名（``<app cache>/index/``）。索引固定回库里后它只剩一个用途：认出这个目录
#: 好删掉（``config.discard_legacy_external_index``），留着常量好让「旧目录叫什么」仍是单一来源。
LEGACY_INDEX_CACHE_DIR_NAME: Final = "index"

#: 应用在系统目录（macOS 为 ``~/Library/Application Support``）下的目录名。
APP_NAME: Final = "Gallery"

#: 随包分发的只读资源目录名，位于 ``src/gallery/`` 内（应用图标、自带的城市库）。
RESOURCES_DIR_NAME: Final = "resources"

#: 自带的城市库文件名（gzip 压缩的 TSV），按 GPS 坐标反查地点用的全部数据。
#: 由 ``scripts/build_places_db.py`` 生成，详见 ``resources/README.md``。
PLACES_DB_FILENAME: Final = "places.tsv.gz"

#: 应用图标各档 PNG 的文件名主干，实际文件名是 ``<主干>-<边长>.png``。
APP_ICON_BASENAME: Final = "gallery-icon"

#: 应用图标提供的边长档位（像素）。最大的一档同时是生成 ``.icns`` 的来源。
APP_ICON_SIZES: Final = (16, 32, 64, 256, 1024)

#: 缩略图生成算法的版本号。解码方式、缩放滤镜或输出格式变化时递增；缓存 key 包含它，
#: 递增会让整批旧缩略图自动失效。当前 2 = 缓存 JPEG 从 q85+optimize 改成 q82+关掉 optimize
#: （ADR 0015 决策 2）；采样因子本来就是 4:2:0，写死只是让这一档成为代码里的明文承诺。
THUMBNAIL_SPEC_VERSION: Final = 2

#: 缩略图长边默认像素数。
DEFAULT_THUMBNAIL_MAX_EDGE: Final = 512

#: 缩略图缓存默认上限（2 GiB）。0 表示不限制。
DEFAULT_MAX_CACHE_BYTES: Final = 2 * 1024**3

__all__ = [
    "APP_ICON_BASENAME",
    "APP_ICON_SIZES",
    "APP_NAME",
    "CACHE_DIR_NAME",
    "CONFIG_DIR_NAME",
    "CONFIG_FILE_NAME",
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_THUMBNAIL_MAX_EDGE",
    "EVENTS_FILE_NAME",
    "INDEX_FILE_NAME",
    "JOURNAL_DIR_NAME",
    "LEGACY_INDEX_CACHE_DIR_NAME",
    "PLACES_DB_FILENAME",
    "RESOURCES_DIR_NAME",
    "THUMBNAIL_DIR_NAME",
    "THUMBNAIL_SPEC_VERSION",
]
