"""窗口用的控件：标题栏、面包屑、欢迎页、搜索框、播放按钮。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "widget.breadcrumb.filtered": "筛选后",
    "widget.breadcrumb.search_results": "搜索结果",
    "widget.breadcrumb.count": "{title}（{count}）",
    "widget.breadcrumb.filtered_tooltip": "当前层级中符合筛选条件的照片；按 Esc 逐条撤销条件",
    "widget.breadcrumb.search_tooltip": "整个库的检索结果（不限于某个文件夹）；按 Esc 逐条撤销条件",
    "widget.breadcrumb.group_tooltip": "当前分组（按 Esc 返回上一层级）",
    "widget.external_button.tooltip": "用外部应用打开当前这一页",
    "widget.play_button.tooltip": "放映当前层级的所有照片",
    "widget.play_button.tooltip_pause": "暂停放映",
    "widget.play_button.tooltip_resume": "继续放映",
    "widget.play_button.accessible_name": "放映",
    "widget.search_field.placeholder_search": "搜索照片…",
    "widget.search_field.placeholder_filter": "筛选照片…",
    "widget.search_field.tooltip_filter": (
        "用这些条件筛选当前层级（「全部照片」下筛选整库，「日历」下筛选日历）"
    ),
    "widget.search_field.tooltip_search": "在整个库中检索（切换到筛选可只筛当前层级）",
    "widget.welcome.subtitle": "照片保存在你自己的文件夹中，Gallery 只提供浏览。",
    "widget.welcome.open_button": "打开文件夹…",
    "widget.welcome.hint": (
        "Gallery 不会自行移动、修改或删除照片；重命名与移入废纸篓只在你亲自操作时执行。"
        "首次打开会在库内创建 {config_dir} 目录存放运行时数据。"
    ),
    "widget.welcome.recent_title": "最近打开",
}
