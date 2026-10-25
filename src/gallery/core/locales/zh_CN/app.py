"""应用与系统的显示名（访达 / 地图 / 日历……）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "app.finder": "访达",
    "app.google_maps": "Google 地图",
    "app.google_calendar": "Google 日历",
    "app.qspace": "QSpace",
    "app.maps": "地图",
    "app.calendar": "日历",
    "app.preview": "预览",
    "app.imageglass": "ImageGlass",
    "app.picview": "Picview",
    "app.quicktime": "QuickTime Player",
    "app.iina": "IINA",
    "app.cli.description": "Gallery —— 本地优先的相册软件：照片留在你自己的文件夹里。",
    "app.cli.library_help": "启动时打开的库目录（省略则显示欢迎页并回到上次打开的库）",
    "app.cli.version_help": "打印版本信息后退出",
    "app.macos.no_source": (
        "未找到 Gallery 源码目录。请在项目目录中运行，或使用 uv run python -m gallery.macos_app。"
    ),
    "app.macos.icon_failed": "生成图标失败：{tool} 退出码 {code}",
    "app.macos.no_icon_source": "未找到应用图标源文件。",
    "app.macos.missing_sips": "未找到 macOS 自带的 sips，无法生成 .icns。",
    "app.macos.missing_iconutil": "未找到 macOS 自带的 iconutil，无法生成 .icns。",
    "app.macos.destination_taken": (
        "目标位置已经有东西，且不是 Gallery.app：{destination}。请更换位置，或先删除它。"
    ),
    "app.macos.no_interpreter": (
        "未找到虚拟环境中的解释器：{interpreter}。请先在项目目录执行 uv sync。"
    ),
    "app.macos.no_icns": "未找到现成的 .icns 文件。",
    "app.macos.description": (
        "把 Gallery 打包成 macOS 的 Gallery.app，让 Dock、访达、⌘Tab 用上应用图标。"
    ),
    "app.macos.destination_help": "生成位置（默认 <项目目录>/{build_dir}/{app}.app）",
    "app.macos.generated": "已生成 {bundle}",
    "app.macos.launch_hint": "双击即可启动，Dock、访达与 ⌘Tab 里都是 Gallery 的图标。",
    "app.macos.venv_hint": (
        "这个包借用仓库的 .venv，不含依赖；仓库移动或 uv sync 重建后请重新生成。"
    ),
}
