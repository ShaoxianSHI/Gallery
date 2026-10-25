"""交给外部应用时的消息与显示名。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "external.launch.browser_failed": "未能打开浏览器",
    "external.launch.maps_failed": "未能打开地图，请确认系统「地图」仍已安装",
    "external.launch.app_failed": "未能启动该应用",
    "external.launch.browser_not_started": "浏览器未能启动",
    "external.launch.window_unrecognized": "未识别该应用窗口",
    "external.launch.window_rewrite_failed": "未能改写该窗口的地址",
    "external.launch.fallback_both_failed": "{reason}，浏览器也未能打开",
    "external.launch.fallback_browser": "{reason}，已改用浏览器打开",
    "external.apple_calendar.not_authorized": (
        "未能控制「日历」：请在「系统设置 › 隐私与安全性 › 自动化」里允许 Gallery 控制「日历」"
    ),
    "external.apple_calendar.not_responding": "「日历」未响应",
    "external.apple_calendar.not_found": "未找到系统「日历」",
    "external.apple_calendar.open_failed": "未能打开系统「日历」",
    "external.errors.source_page": "源站没有这个页面：{url}",
}
