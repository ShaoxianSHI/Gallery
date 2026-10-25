"""窗口里那些动作流程（粘贴 / 事件 / 排序 / 检索 / 缩略图……）。

值就是这些流程原先的字面量，**标点、空格与「」逐字不动**。带计数的键一律写成两元组，
中文两档相同；英文那一侧按需要分单复数。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # 两处重复导入的确认框共用的按钮。
    "flow.common.cancel": "取消",
    "flow.common.skip_duplicates": "跳过重复",
    "flow.common.import_all": "全部导入",
    # ---- 会动用户文件的入口（write_ops）------------------------------------
    "flow.write_ops.group_not_file": (
        "分组不是磁盘上的文件，无法重命名或删除。请双击进入分组，选中其中的照片后再操作。"
    ),
    "flow.write_ops.nothing_selected": "没有可处理的东西。",
    "flow.write_ops.no_paste_target": "没有可粘贴到的文件夹。",
    "flow.write_ops.clipboard_empty": "剪贴板中没有可粘贴的文件。",
    "flow.write_ops.busy": "上一次操作尚未结束。",
    "flow.write_ops.pasting": (
        "正在把 {count} 项粘贴到「{target}」…",
        "正在把 {count} 项粘贴到「{target}」…",
    ),
    "flow.write_ops.paste_failed": "未能粘贴：{reason}",
    "flow.write_ops.duplicating": ("正在复制 {count} 份副本…", "正在复制 {count} 份副本…"),
    "flow.write_ops.duplicate_failed": "未能复制：{reason}",
    "flow.write_ops.no_album_target": "没有可新建相册的文件夹。",
    "flow.write_ops.album_created": "已新建相册「{name}」。",
    "flow.write_ops.log_failed": "未能记录操作日志。",
    "flow.write_ops.no_cover_photo": "没有可设为相册封面的照片。",
    "flow.write_ops.no_editable_photo": "没有可编辑元数据的照片。",
    "flow.write_ops.reading_metadata": "正在读取 {name} 的元数据…",
    "flow.write_ops.metadata_target_changed": "当前照片已更换，请重新执行「编辑元数据」。",
    "flow.write_ops.metadata_unreadable": "无法读取「{name}」的元数据，不能编辑。",
    "flow.write_ops.transferring.copy": (
        "正在把 {count} 项复制到「{target}」…",
        "正在把 {count} 项复制到「{target}」…",
    ),
    "flow.write_ops.transferring.move": (
        "正在把 {count} 项移动到「{target}」…",
        "正在把 {count} 项移动到「{target}」…",
    ),
    "flow.write_ops.transfer_failed.copy": "未能复制：{reason}",
    "flow.write_ops.transfer_failed.move": "未能移动：{reason}",
    "flow.write_ops.nothing_to_undo": "没有可撤销的操作。",
    "flow.write_ops.events_unavailable.movies": "当前库无法使用事件功能，不能导入影片。",
    "flow.write_ops.import_busy": "上一次导入尚未结束。",
    "flow.write_ops.no_library": "请先打开一个库。",
    "flow.write_ops.planning_movies": (
        "正在抓取 {count} 项影片的资料…",
        "正在抓取 {count} 项影片的资料…",
    ),
    "flow.write_ops.writing_movies": (
        "正在下载并写入 {count} 部影片的封面…",
        "正在下载并写入 {count} 部影片的封面…",
    ),
    "flow.write_ops.movie_import_failed": "导入影片失败：{reason}",
    "flow.write_ops.movie_import_title": "导入影片",
    "flow.write_ops.movie_import_duplicates": (
        "获取到 {count} 部影片，其中 {duplicates} 部已存在于库中"
        "（来源与编号相同）。\n\n要跳过这些，还是全部重新导入？",
        "获取到 {count} 部影片，其中 {duplicates} 部已存在于库中"
        "（来源与编号相同）。\n\n要跳过这些，还是全部重新导入？",
    ),
    # ---- 事件（event_flow）------------------------------------------------
    "flow.event_flow.nothing_to_bind": "请先选中照片，再记录到事件。",
    "flow.event_flow.nothing_to_unbind": "请先选中要从该事件移出的照片。",
    "flow.event_flow.import_filter": "Excel 表格 (*.xlsx *.xlsm)",
    "flow.event_flow.import_movie.latin": "导入 {source}…",
    "flow.event_flow.import_movie.cjk": "导入{source}…",
    "flow.event_flow.action.edit_event": "编辑事件…",
    "flow.event_flow.action.leave_event": "从该事件移出",
    "flow.event_flow.action.clear_event_cover": "清除事件封面",
    "flow.event_flow.action.import_shows": "从「演迹」导入…",
    "flow.event_flow.event_missing": "该事件已不存在。",
    "flow.event_flow.movie_no_page": (
        "这部影片没有可打开的网页：编号可能为空，或该站仅在 App 内提供页面。"
    ),
    "flow.event_flow.menu.record_as": "记录为",
    "flow.event_flow.menu.edit_event": "编辑事件",
    "flow.event_flow.menu.new_event": "新增事件",
    "flow.event_flow.menu.import_movies": "导入影片",
    "flow.event_flow.menu.import_shows": "导入演出",
    "flow.event_flow.menu.set_event_cover": "设为事件封面",
    "flow.event_flow.choose_shows_file": "选择「演迹」导出的表格",
    "flow.event_flow.events_unavailable.shows": "当前库无法使用事件功能，不能导入演出。",
    "flow.event_flow.duplicate_shows": (
        "表格中有 {count} 场演出，其中 {duplicates} 场的名称、日期与场馆"
        "与库中已有记录相同。\n\n要跳过这些，还是全部重新导入？",
        "表格中有 {count} 场演出，其中 {duplicates} 场的名称、日期与场馆"
        "与库中已有记录相同。\n\n要跳过这些，还是全部重新导入？",
    ),
    "flow.event_flow.new_event_item": "新建{kind}…",
    "flow.event_flow.delete_event.plain": "删除「{name}」？\n\n⌘Z 可将这些记录一并恢复。",
    "flow.event_flow.delete_event.with_photos": (
        "删除「{name}」，连同 {count} 张照片上的记录？\n\n⌘Z 可将这些记录一并恢复。",
        "删除「{name}」，连同 {count} 张照片上的记录？\n\n⌘Z 可将这些记录一并恢复。",
    ),
    "flow.event_flow.orphan.keep": "暂不清理",
    "flow.event_flow.orphan.clear_missing": "清理这些",
    "flow.event_flow.orphan.clear_vanished_too": "一并清理相册已不存在的",
    "flow.event_flow.orphan.clear_all": "一并清理这些",
    # ---- 分组与排序（ordering）--------------------------------------------
    "flow.ordering.purpose.filter": "筛选",
    "flow.ordering.purpose.group": "分组",
    "flow.ordering.purpose.sort": "排序",
    "flow.ordering.facet_progress": (
        "正在读取 {count} 张照片的元数据，用于{purpose}…",
        "正在读取 {count} 张照片的元数据，用于{purpose}…",
    ),
    "flow.ordering.exiftool_required": (
        "读取该字段需要使用 exiftool。安装命令：brew install exiftool"
    ),
    "flow.ordering.group.none": "不分组",
    "flow.ordering.group": "分组",
    "flow.ordering.sort": "排序",
    "flow.ordering.ascending": "升序",
    "flow.ordering.descending": "降序",
    "flow.ordering.layout.cards": "卡片式",
    "flow.ordering.layout.sections": "分区式",
    "flow.ordering.empty.filtered": "当前层级中没有符合筛选条件的照片。",
    "flow.ordering.empty.search": "当前筛选条件下没有照片。",
    "flow.ordering.empty.group": "该分组中没有照片。",
    "flow.ordering.empty.album": "该文件夹中没有子文件夹，也没有可显示的照片。",
    "flow.ordering.empty.level": (
        "未找到可显示的照片或视频。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP 与 "
        "MP4、MOV、M4V、AVI、3GP。"
    ),
    "flow.ordering.progress": "正在读取元数据…{done} / {total}",
    "flow.ordering.facet_failed": "读取元数据失败，已按默认顺序显示：{reason}",
    # ---- 打开库与地点解析（library_flow）----------------------------------
    "flow.library_flow.choose_folder": "选择照片文件夹",
    "flow.library_flow.config_dir_failed": (
        "无法写入「{name}」：Gallery 不能在其中创建配置目录。"
        "浏览、分组与搜索仍可用，但索引本次不会落盘，下次打开需要重新读取。"
    ),
    "flow.library_flow.scan_issues": "部分内容未能读取：{summary}",
    "flow.library_flow.scan_failed": "扫描失败：{reason}",
    "flow.library_flow.place_progress": (
        "正在解析 {count} 张照片的地点…",
        "正在解析 {count} 张照片的地点…",
    ),
    "flow.library_flow.place_progress_processed": "正在解析照片的地点…已处理 {count} 张",
    "flow.library_flow.recent_empty": "（暂无最近打开的库）",
    "flow.library_flow.clear_recent": "清除最近打开",
    # ---- 检索（search_flow）----------------------------------------------
    "flow.search_flow.filter_failed": "筛选未能完成：{reason}",
    "flow.search_flow.search_failed": "检索未能完成：{reason}",
    "flow.search_flow.unusable": "「{field}」{reason}无法识别的部分不会加入筛选条件。",
    "flow.search_flow.exiftool_required": (
        "未安装 exiftool，无法读取「{field}」字段。"
        "文件名、相册、格式与大小不受影响。安装命令：brew install exiftool"
    ),
    "flow.search_flow.no_library.filter": "请先打开一个库，再筛选。",
    "flow.search_flow.no_library.search": "请先打开一个库，再搜索。",
    "flow.search_flow.index_progress": (
        "正在建立检索索引…{count} 张",
        "正在建立检索索引…{count} 张",
    ),
    "flow.search_flow.index_progress_processed": (
        "正在建立检索索引…已处理 {count} 张",
        "正在建立检索索引…已处理 {count} 张",
    ),
    "flow.search_flow.index_unavailable.filter": "当前库无法建立检索索引，筛选暂不可用。",
    "flow.search_flow.index_unavailable.search": "当前库无法建立检索索引，搜索暂不可用。",
    # ---- 右键菜单里的只读命令（context_menu_flow）------------------------
    "flow.context_menu.copy_paths": ("已拷贝 {count} 个路径。", "已拷贝 {count} 个路径。"),
    "flow.context_menu.metadata_read_failed": "无法读取元数据：{reason}",
    "flow.context_menu.metadata_copied": "已复制「{name}」的元数据。",
    "flow.context_menu.copy_to_album": "复制到相册",
    "flow.context_menu.move_to_album": "移动到相册",
    "flow.context_menu.group_condition": (
        "按「{field}」分组：{label} · {count} 张",
        "按「{field}」分组：{label} · {count} 张",
    ),
    "flow.context_menu.group_copied": "已复制分组条件：按「{field}」分组。",
    # ---- 缩略图（thumbnail_flow）------------------------------------------
    "flow.thumbnail_flow.failed": (
        "有 {count} 个文件无法显示缩略图：{reason}",
        "有 {count} 个文件无法显示缩略图：{reason}",
    ),
    "flow.thumbnail_flow.clear_title": "清空缩略图缓存",
    "flow.thumbnail_flow.clear_body": (
        "将删除「{name}」的缩略图缓存。\n照片本身不受影响，缓存会在下次浏览时自动重建。"
    ),
    "flow.thumbnail_flow.clear_busy": "上一次清空尚未结束。",
    "flow.thumbnail_flow.clearing": "正在清空缩略图缓存…",
    "flow.thumbnail_flow.cleared": (
        "已清除 {count} 个缩略图缓存文件。",
        "已清除 {count} 个缩略图缓存文件。",
    ),
    "flow.thumbnail_flow.clear_failed": "未能清空缩略图缓存：{reason}",
    # ---- 元数据面板（metadata_flow）--------------------------------------
    "flow.metadata_flow.no_city": "坐标附近没有城市记录",
    "flow.metadata_flow.distance": "{label}（最近城市约 {distance:.0f} 公里外）",
    "flow.metadata_flow.exiftool_required": (
        "修改照片位置需要使用 exiftool。安装命令：brew install exiftool"
    ),
    "flow.metadata_flow.looking_up_city": "正在查询最近的城市…",
    "flow.metadata_flow.no_coordinates": "无法推断地点：未读到坐标",
    "flow.metadata_flow.lookup_failed": "未能推断地点：{reason}",
}
