"""窗口各条流程里的文案（横幅 / 错误提示 / 状态文字）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # 外部应用的显示名：统一处理之前先包进 t，值暂时与原字面量一致。
    "window.map.no_location": "这张照片没有拍摄位置，无法打开地图。",
    "window.external.open_failed": "未能打开 {name}：{detail}",
    "window.calendar.apple_open_failed": "未能打开「日历」：{detail}",
    "window.external.app_not_found": "未找到 {name}：它可能已被移动或卸载。",
    "window.folder.no_library": "尚未打开库，没有可打开的文件夹。",
    "window.thumb.label.small": "小",
    "window.thumb.label.medium": "中",
    "window.thumb.label.large": "大",
    "window.thumb.describe": "缩略图：{label}，目标 {edge} 像素",
    "window.thumb.describe_actual": "缩略图：{label} · 实际 {edge} 像素",
    "window.watch.failed": (
        "{detail}。界面暂时不会自动更新；按 ⌘R 可重新扫描当前层级，重新打开库可恢复自动更新。"
    ),
    "window.calendar.reading": (
        "正在读取 {count} 张照片的拍摄时间，用于日历…",
        "正在读取 {count} 张照片的拍摄时间，用于日历…",
    ),
    "window.calendar.read_failed": "日历未能读取拍摄时间：{detail}",
    "window.title.drill": ("{label} · {count} 张", "{label} · {count} 张"),
    "window.fill_window": "铺满窗口",
    "window.restore_window": "还原窗口",
    "window.slideshow.empty": "当前层级中没有照片。",
}
