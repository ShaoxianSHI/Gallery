"""「设置」菜单（ADR 0073 / 0099）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "settings.title": "设置",
    "settings.theme": "主题",
    "settings.animation": "动画速度",
    "settings.slideshow": "放映间隔",
    "settings.movie_site": "影片网站",
    "settings.cache_limit": "缓存上限",
    "settings.background": "背景",
    "settings.external_order": "外部应用顺序…",
    "settings.open_last": "启动时打开上次的库",
    "settings.log_folder": "打开日志文件夹",
    "settings.about": "关于 {name}",
    "settings.about.body": "{name} {version}\n\n库位置：{where}",
    "settings.custom": "自定义…",
    "settings.speed.slow": "慢",
    "settings.speed.normal": "中",
    "settings.speed.fast": "快",
    "settings.speed.off": "关",
    "settings.appearance.system": "跟随系统",
    "settings.appearance.light": "浅色",
    "settings.appearance.dark": "深色",
    "settings.page.detail": "图片",
    "settings.page.video": "视频",
    "settings.page.grid": "网格",
    "settings.page.calendar": "日历",
    "settings.cache.unlimited": "不限制",
    "settings.cache.custom": "自定义大小",
    "settings.cache.set": "缩略图缓存上限已设为 {label}。",
    "settings.seconds": "{seconds} 秒",
    "settings.save_failed": "设置未能保存；本次运行仍按新设置执行。",
    "settings.movie_site.prompt": "番号页的地址前缀（例如 https://jable.tv/videos）：",
    "settings.log_folder.failed": "未能打开日志文件夹：{folder}",
    "settings.no_library": "尚未打开库",
    "settings.photo_filter": "图片 (*.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff *.webp);;所有文件 (*)",
    "settings.background.solid": "设置纯色背景…",
    "settings.background.light": "浅色背景",
    "settings.background.dark": "深色背景",
    "settings.background.generate": "生成水墨背景…",
    "settings.background.photo": "使用照片背景…",
    "settings.background.manage": "管理背景…",
    "settings.background.use_solid": "使用纯色",
    "settings.background.empty": "暂无已保存的背景图",
    "settings.background.empty_banner": "暂无已保存的背景图。",
    "settings.background.saved": "背景「{name}」已保存，可在「背景 › 浅色背景 / 深色背景」中选用。",
    "settings.background.solid_banner.light": "浅色背景已改为纯色。",
    "settings.background.solid_banner.dark": "深色背景已改为纯色。",
    "settings.background.set_banner.light": "浅色背景已设为「{name}」。",
    "settings.background.set_banner.dark": "深色背景已设为「{name}」。",
    "settings.background.pick_title": "选择一张照片作为背景",
}
