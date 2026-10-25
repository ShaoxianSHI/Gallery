"""大图页与视频页。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "viewer.metadata.event_title": "{kind} · {name}",
    "viewer.metadata.copy_all": "复制全部",
    "viewer.metadata.edit": "编辑元数据",
    "viewer.metadata.show_all": "显示全部字段",
    "viewer.metadata.empty": "尚未打开照片",
    "viewer.metadata.loading": "正在读取元数据…",
    "viewer.metadata.loading.named": "正在读取元数据…\n{name}",
    "viewer.metadata.edit_tooltip": "修改拍摄时间或位置将写回照片（⌘Z 可撤销）",
    "viewer.metadata.all_fields": "全部字段",
    "viewer.metadata.not_editable": "此项不可编辑",
    "viewer.metadata.clipboard.all_fields": "【全部字段】",
    "viewer.metadata.clipboard.event": "【{name}】",
    "viewer.metadata.clipboard.field": "{label}：{value}",
    "viewer.metadata.copied": "已复制",
    "viewer.video.play_tooltip": "播放 / 暂停（空格）",
    "viewer.video.unplayable": "无法播放该视频",
    "viewer.video.unplayable.hint": "{reason}。按 ⇧⏎ 使用系统默认应用打开。",
}
