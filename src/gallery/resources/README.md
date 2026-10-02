# 应用资源

## `places.tsv.gz` —— 自带的城市库

按 GPS 坐标反查地点用的**全部**数据，随应用一起分发：运行时不联网、不下载。

| 项 | 值 |
|---|---|
| 记录数 | 21,236 条 |
| 压缩后 | 约 378 KB |
| 列 | `region`（省/州）、`city`（城市）、`country`（国家）、`latitude`、`longitude`、`population` |
| 格式 | UTF-8 TSV，开头三行是 `#` 注释 |
| 生成 | `uv run python scripts/build_places_db.py` |
| 来源 | [dr5hn/countries-states-cities-database](https://github.com/dr5hn/countries-states-cities-database)（ODbL-1.0，需署名） |

**列里就是显示用的名字** —— 名字在生成期就定好了（国内中文、境外当地写法），
运行时只做最近邻查询，不再翻译、不再判断层级归属。`region` 为空表示这个地方没有
省级那一层（直辖市、香港、澳门）。

**不要手改这个文件**：它是生成产物，下次跑脚本会被覆盖。要改请改
`scripts/build_places_db.py`，那里的 docstring 记着这份源数据的几处坑
（直辖市不是城市记录、港澳登记了两次、`parent_id` 只有中国有，等等）。

## `gallery-icon-*.png` —— 应用图标

`gallery-icon-<边长>.png` 是应用图标，界面标题栏、`Gallery.app` 的 Dock 图标都用它。

| 边长 | 用在哪 |
|------|--------|
| 16 / 32 / 64 | 窗口标题栏左侧、访达小图标 |
| 256 | 大一些的展示位（关于面板之类） |
| 1024 | 主图，macOS 的 `Gallery.icns` 由它缩放生成 |

档位由 `core.constants.APP_ICON_SIZES` 钉住，`core.paths.app_icon_files()` 是它们的唯一
来源：增删档位要同时改那里与本目录下的文件名，`tests/` 会核对两边一致。

**换图标：** 替换 1024 那一档（源图），其余按比例缩放，然后重新生成应用包
（`uv run python -m gallery.macos_app`）。

**来源与授权：** 当前这份图标取自 macOS 自带的「照片」应用（Apple 商标资源），仅供本地自用。
对外发布前请换成自己的图标。