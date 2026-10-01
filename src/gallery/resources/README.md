# 应用图标资源

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