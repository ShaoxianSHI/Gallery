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
#:
#: **缩略图已经不住在这里了**（ADR 0038）：它们按库路径摘要落在系统目录的
#: ``<app cache>/thumbs/<摘要>/``，免得几万个小文件跟着照片被云同步来回传。
#: 这个常量保留下来是因为**读老库的旧目录**时还要用到它（一次性迁移，见
#: ``core.library.config.discard_legacy_thumb_cache``）。
CACHE_DIR_NAME: Final = "cache"

#: 缩略图缓存目录名。**位于系统目录内**，按库路径摘要再分一层。
THUMBNAIL_DIR_NAME: Final = "thumbs"

#: 写操作日志目录名（``.gallery/journal/``）：每一次会动用户文件的操作都往里记一笔。
JOURNAL_DIR_NAME: Final = "journal"

#: SQLite 索引文件名。分组 / 排序读的元数据快照存在这里（见 ADR 0009、ADR 0038）。
INDEX_FILE_NAME: Final = "index.db"

#: **旧版**的库外索引子目录名（``<app cache>/index/``）。
#:
#: ADR 0038 把索引固定回库里之后就只剩一个用途：**认出这个目录好把它删掉**
#: （``config.discard_legacy_external_index``）。留着常量而不是就地写个字面量，
#: 是为了让「旧目录叫什么」这件事仍然只有一个来源。
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

#: 缩略图生成算法的版本号。解码方式、缩放滤镜或输出格式变化时递增；
#: 缓存 key 包含它，因此递增会让整批旧缩略图自动失效。
#: 2 = 缓存 JPEG 从「q85 + optimize」改成「q82 + 关掉 optimize」（见 ADR 0015 决策 2）。
#:
#: 顺带更正一句旧注释：它写过「4:4:4 改成 4:2:0」，而 ADR 记录了那条弯路 ——
#: 把写出来的 JPEG 的 SOF0 拆开看，**原来的采样因子本来就是 4:2:0**，libjpeg 在那个
#: 质量下自己就选了色度减半；显式写出来是「让这一档成为代码里的一行承诺」，不是变更。
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
    "INDEX_FILE_NAME",
    "JOURNAL_DIR_NAME",
    "LEGACY_INDEX_CACHE_DIR_NAME",
    "PLACES_DB_FILENAME",
    "RESOURCES_DIR_NAME",
    "THUMBNAIL_DIR_NAME",
    "THUMBNAIL_SPEC_VERSION",
]
