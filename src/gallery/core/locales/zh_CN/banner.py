"""横幅与确认框上的那句话（ADR 0029 / 0100）。

这些值就是 `ui/banners.py` 与 `ui/desktop.py` 原先的字面量，**标点、空格与「」逐字不动**。
一条横幅仍然是「结果 + 能否撤销」（ADR 0100 决策 4）；`{note}` 只接一个完整的分句。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "banner.quote": "「{text}」",
    "banner.punct.separator": "；",
    "banner.punct.period": "。",
    "banner.detail": "{detail}。",
    "banner.log_failed": "；未能记录操作日志",
    "banner.log_failed.plain": "未能记录操作日志",
    "banner.undoable": "⌘Z 可撤销",
    "banner.unexpected": "出现了一个没料到的问题，详情写进了日志文件：{path}。",
    "banner.rename.journaled": "已重命名为「{name}」。",
    "banner.rename.not_journaled": "已重命名为「{name}」；未能记录操作日志，⌘Z 不可撤销。",
    "banner.event.kind": "事件",
    "banner.event.created": "已记为「{name}」。",
    "banner.event.updated.unchanged": "「{name}」未做改动。",
    "banner.event.updated.journaled": "已更新「{name}」；⌘Z 可撤销。",
    "banner.event.updated.not_journaled": "已更新「{name}」；未能记录操作日志，⌘Z 不可撤销。",
    "banner.event.deleted.base": "已删除「{name}」{photos}",
    "banner.event.deleted.photos": (
        "，并从 {count} 张照片上移除了它的记录",
        "，并从 {count} 张照片上移除了它的记录",
    ),
    "banner.event.deleted.undoable": "。⌘Z 可撤销",
    "banner.event.deleted.not_undoable": "。未能记录操作日志，⌘Z 不可撤销",
    "banner.photos.bound.already": "这些照片已在「{name}」中，未做改动。",
    "banner.photos.bound.by_range": (
        "已按日期范围将 {count} 张照片记录到「{name}」。",
        "已按日期范围将 {count} 张照片记录到「{name}」。",
    ),
    "banner.photos.bound.done": (
        "已将 {count} 张照片记录到「{name}」。",
        "已将 {count} 张照片记录到「{name}」。",
    ),
    "banner.photos.unbound.none": "这些照片原本不在「{name}」中，未做改动。",
    "banner.photos.unbound.done": (
        "已将 {count} 张照片移出「{name}」。",
        "已将 {count} 张照片移出「{name}」。",
    ),
    "banner.event.cover.set": "已将「{photo}」设为「{name}」的封面。⌘Z 可撤销。",
    "banner.event.cover.cleared": "已取消「{name}」的封面，改用第一张照片。⌘Z 可撤销。",
    "banner.shows.imported.none": "表格中未读到可记录的演出。",
    "banner.shows.imported.no_new": "没有新增演出",
    "banner.shows.imported.done": (
        "已从「演迹」导入 {count} 场演出",
        "已从「演迹」导入 {count} 场演出",
    ),
    "banner.shows.imported.duplicates": "，跳过 {count} 场已存在的",
    "banner.shows.imported.skipped": ("，另有 {count} 行没导入", "，另有 {count} 行没导入"),
    "banner.movies.imported.none": "没有导入任何影片。",
    "banner.movies.imported.created": ("已导入 {count} 部影片", "已导入 {count} 部影片"),
    "banner.movies.imported.duplicates": "跳过 {count} 部已存在的",
    "banner.movies.imported.more": ("，共 {count} 部", "，共 {count} 部"),
    "banner.movie_site.opened": "已在浏览器中打开「{code}」的影片网站。",
    "banner.movie_site.failed": "未能打开「{code}」的影片网站：{detail}。",
    "banner.event_undo.restored": "已恢复{what}{note}",
    "banner.event_undo.changed": "已撤销对{what}的改动{note}",
    "banner.event_undo.what": "{kind}「{name}」",
    "banner.event_undo.log_failed": "；未能记录操作日志，下一次 ⌘Z 可能无法继续。",
    "banner.orphan.head.any": "部分事件记录指向已不存在的照片。",
    "banner.orphan.head.named": (
        "{count} 条事件记录指向已不存在的照片，涉及{names}。",
        "{count} 条事件记录指向已不存在的照片，涉及{names}。",
    ),
    "banner.orphan.why": (
        "照片可能在 Gallery 之外被删除或移动。保留这些记录不会造成损坏，\n"
        "但同一路径日后被其他照片占用时，该照片会被计入原事件。"
    ),
    "banner.orphan.ask": ("是否清理这 {count} 条记录？", "是否清理这 {count} 条记录？"),
    "banner.orphan.vanished": (
        "另有 {count} 条指向已不存在的相册{who}。\n"
        "也可能是磁盘未挂载，因此默认保留。是否一并清理？",
        "另有 {count} 条指向已不存在的相册{who}。\n"
        "也可能是磁盘未挂载，因此默认保留。是否一并清理？",
    ),
    "banner.orphan.vanished.who": "，涉及{names}",
    "banner.orphan.list.separator": "、",
    "banner.orphan.list.more": ("等 {count} 个事件", "等 {count} 个事件"),
    "banner.orphan.title": (
        "{count} 条事件记录指向已不存在的照片",
        "{count} 条事件记录指向已不存在的照片",
    ),
    "banner.orphans.forgotten.none": "未清理任何记录。",
    "banner.orphans.forgotten.done": (
        "已清理 {count} 条指向已不存在照片的记录；照片未被改动。",
        "已清理 {count} 条指向已不存在照片的记录；照片未被改动。",
    ),
    "banner.album.done.paste": (
        "已粘贴 {count} 项到「{target}」",
        "已粘贴 {count} 项到「{target}」",
    ),
    "banner.album.done.copy": (
        "已复制 {count} 项到「{target}」",
        "已复制 {count} 项到「{target}」",
    ),
    "banner.album.done.move": (
        "已移动 {count} 项到「{target}」",
        "已移动 {count} 项到「{target}」",
    ),
    "banner.album.renamed.one": "，「{source}」改名为「{created}」",
    "banner.album.renamed.many": (
        "，其中 {count} 项因重名而改名",
        "，其中 {count} 项因重名而改名",
    ),
    "banner.album.failed.paste": "{name} 未能粘贴{more}：{reason}",
    "banner.album.failed.copy": "{name} 未能复制{more}：{reason}",
    "banner.album.failed.move": "{name} 未能移动{more}：{reason}",
    "banner.album.failure.more": ("，共 {count} 项", "，共 {count} 项"),
    "banner.album.stayed.single": (
        "{name} 原本就在「{target}」中，已复制一份，新名称为「{created}」"
    ),
    "banner.album.stayed.many": (
        "另有 {count} 项原本就在「{target}」中，已各复制一份",
        "另有 {count} 项原本就在「{target}」中，已各复制一份",
    ),
    "banner.duplicate.created": ("已复制 {count} 份副本", "已复制 {count} 份副本"),
    "banner.duplicate.named": "，新名称为「{name}」",
    "banner.trash.none.journaled": "未移走任何项目：{reason}。",
    "banner.trash.none.not_journaled": "未移走任何项目：{reason}；未能记录操作日志。",
    "banner.trash.album": "已将相册「{name}」及其内容移到废纸篓",
    "banner.trash.single": "已将「{name}」移到废纸篓",
    "banner.trash.many": ("已将 {count} 项移到废纸篓", "已将 {count} 项移到废纸篓"),
    "banner.trash.failures": (
        "，另有 {count} 项未能移走：{reason}",
        "，另有 {count} 项未能移走：{reason}",
    ),
    "banner.trash.hint.journaled": "⌘Z 可撤销。",
    "banner.trash.hint.not_journaled": "未能记录操作日志，⌘Z 不可撤销。",
    "banner.undo.partial": (
        "仅部分撤销：{count} 项在 Gallery 之外被修改，未做改动{note}。",
        "仅部分撤销：{count} 项在 Gallery 之外被修改，未做改动{note}。",
    ),
    "banner.undo.metadata": "已撤销，「{name}」的位置已恢复{note}。",
    "banner.undo.rename": "已撤销，恢复为「{name}」{note}。",
    "banner.undo.move.failed": "未能移回：{reason}{note}。",
    "banner.undo.move.done": "已撤销，照片已移回原相册{note}。",
    "banner.undo.restore.failed": "未能取回：{reason}{note}。",
    "banner.undo.restore.one": "已从废纸篓取回「{name}」",
    "banner.undo.restore.many": ("已从废纸篓取回 {count} 项", "已从废纸篓取回 {count} 项"),
    "banner.undo.restore.failures": (
        "，另有 {count} 项未能取回：{reason}",
        "，另有 {count} 项未能取回：{reason}",
    ),
    "banner.album_cover.set": "已将「{photo}」设为「{album}」的封面。",
    "banner.album_cover.cleared": "「{album}」已恢复为使用第一张照片作封面。",
    "banner.metadata.none": "「{name}」的元数据未做改动。",
    "banner.metadata.journaled": "已更新「{name}」的{what}；⌘Z 可撤销。",
    "banner.metadata.not_journaled": "已更新「{name}」的{what}；未能记录操作日志，⌘Z 不可撤销。",
    "banner.metadata.what.both": "拍摄时间与位置",
    "banner.metadata.what.capture_time": "拍摄时间",
    "banner.metadata.what.place": "位置",
    "banner.reason.unknown": "原因不明",
    "banner.reason.named": "「{name}」：{reason}",
    "banner.reason.some": "其中一项：{reason}",
    "banner.map.opened.app": "已在「{name}」中打开这张照片的拍摄位置。",
    "banner.map.opened.browser": "已在浏览器中打开 {name}。",
    "banner.external.opened.app": "已在 {name}中打开当前页。",
    "banner.external.opened.browser": "已在浏览器中打开 {name}。",
    "banner.calendar.opened": "已在「{name}」中打开当前页对应的日期。",
    "banner.desktop.clipboard.empty": "没有可拷贝的文件。",
    "banner.desktop.clipboard.copied.single": "已拷贝「{name}」到剪贴板。",
    "banner.desktop.clipboard.copied.many": (
        "已拷贝 {count} 项到剪贴板。",
        "已拷贝 {count} 项到剪贴板。",
    ),
    "banner.desktop.open.default_failed": "未能用默认应用打开「{name}」，请确认文件是否仍在原处。",
    "banner.desktop.open.no_files": "没有可打开的文件。",
    "banner.desktop.open.app_refused": "未能交给 {name}，已改用默认应用打开「{file}」。",
    "banner.desktop.open.app.single": "已在{name}中打开「{file}」。",
    "banner.desktop.open.app.many": (
        "已在{name}中打开 {count} 项。",
        "已在{name}中打开 {count} 项。",
    ),
    "banner.desktop.reveal.no_paths": "没有可显示的路径。",
    "banner.desktop.reveal.fallback": "未能选中这些文件，已改为在访达里打开「{folder}」。",
    "banner.desktop.finder.no_response": "访达未响应，请检查系统权限设置。",
    "banner.desktop.reveal.single": "已在访达中显示「{name}」。",
    "banner.desktop.reveal.many": ("已在访达中显示 {count} 项。", "已在访达中显示 {count} 项。"),
    "banner.desktop.folder.no_folders": "没有可打开的文件夹。",
    "banner.desktop.folder.single": "已在访达中打开「{name}」。",
    "banner.desktop.folder.many": (
        "已在访达中打开 {count} 个文件夹。",
        "已在访达中打开 {count} 个文件夹。",
    ),
    "banner.desktop.qspace.no_response": "QSpace 未响应，访达也未能打开；请检查系统权限设置。",
    "banner.desktop.qspace.folder_fallback.single": "未能交给 QSpace，已改用访达打开「{name}」。",
    "banner.desktop.qspace.folder_fallback.many": (
        "未能交给 QSpace，已改用访达打开 {count} 个文件夹。",
        "未能交给 QSpace，已改用访达打开 {count} 个文件夹。",
    ),
    "banner.desktop.qspace.folder.single": "已在 QSpace 中打开「{name}」。",
    "banner.desktop.qspace.folder.many": (
        "已在 QSpace 中打开 {count} 个文件夹。",
        "已在 QSpace 中打开 {count} 个文件夹。",
    ),
    "banner.desktop.qspace.reveal_fallback": "未能交给 QSpace，{message}",
    "banner.desktop.qspace.reveal.single": "已在 QSpace 中显示「{name}」。",
    "banner.desktop.qspace.reveal.many": (
        "已在 QSpace 中显示 {count} 项。",
        "已在 QSpace 中显示 {count} 项。",
    ),
}
