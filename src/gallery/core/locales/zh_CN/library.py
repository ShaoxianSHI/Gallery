"""库的配置、检索、索引与背景图仓库。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # ---- 库配置 ----------------------------------------------------------------
    "library.config.edge_range": (
        "配置项 thumbnail_max_edge 必须在 {minimum} 到 {maximum} 之间，当前是 {value}。"
    ),
    "library.config.negative_cache": (
        "配置项 max_cache_bytes 不能是负数，当前是 {value}（0 表示不限制）。"
    ),
    "library.config.parse_failed": (
        "配置文件语法有误，无法解析：{path}。请修正后重试（或直接删除该文件以恢复默认值）。"
    ),
    "library.config.write_failed": (
        "无法写入配置文件：{path}（{reason}）。请检查该文件夹的写入权限。"
    ),
    "library.config.covers_not_table": (
        "配置项 {key} 必须是一张表（形如 [{key}]），当前是 {value!r}。"
    ),
    "library.config.cover.album_label": "{key} 里的相册「{album}」",
    "library.config.cover.photo_label": "{label} 的封面",
    "library.config.cover.not_string": (
        "{label} 的封面必须写成照片的相对路径（字符串），当前是 {value!r}。"
    ),
    "library.config.cover.outside_album": (
        "{label} 的封面「{photo}」不在这个相册里；"
        "封面要写成这个相册里的照片，例如「{key}/照片.jpg」。"
    ),
    "library.config.relative.empty": "{label} 不能是空路径。",
    "library.config.relative.absolute": (
        "{label}「{value}」必须是相对库根目录的路径：不能以 / 开头，也不能带 ..。"
    ),
    "library.config.not_bool": "配置项 {key} 必须是 true 或 false，当前是 {value!r}。",
    "library.config.not_int": "配置项 {key} 必须是整数，当前是 {value!r}。",
    "library.config.movie_site.required": (
        '配置项 movie_site 必须是影片网站的前缀，例如 "{default}"，当前是 {value!r}。'
    ),
    "library.config.movie_site.scheme": (
        '配置项 movie_site 要以 http:// 或 https:// 开头，当前是 {value!r}。例如 "{default}"。'
    ),
    "library.config.movie_site.host": (
        '配置项 movie_site 只有协议头、没有站点，当前是 {value!r}。例如 "{default}"。'
    ),
    # ---- 库门面 ----------------------------------------------------------------
    "library.facade.open.missing": "无法打开该文件夹：{path}。它可能已被移动、重命名或删除。",
    "library.facade.open.not_folder": "该路径不是文件夹：{path}。请选择包含照片的文件夹。",
    "library.facade.open.no_permission": (
        "没有读取该文件夹的权限：{path}。"
        "请在「系统设置 → 隐私与安全性 → 文件与文件夹」中授权后重试。"
    ),
    "library.facade.cache_limit_invalid": ("缩略图缓存上限必须是不小于 0 的整数（0 表示不限制）。"),
    "library.facade.config_not_writable": (
        "当前库的配置目录不可写入，设置无法保存：{path}。"
        "只读卷或网络共享上的库只能浏览，改不了设置。"
    ),
    "library.facade.outside_library": "不能浏览库以外的文件夹：{path}。",
    "library.facade.cover.photo_outside": "「{name}」不在当前库里，无法用作封面。",
    "library.facade.cover.unsupported": "「{name}」不是 Gallery 支持的格式，请更换后重试。",
    "library.facade.cover.not_in_album": (
        "封面得是「{album}」里的照片，「{photo}」不在这个相册里。"
    ),
    "library.facade.cover.not_folder": "「{name}」不是一个文件夹，无法设置封面。",
    "library.facade.cover.root": ("库根目录不是一个相册，它在网格上不是一张卡片，没有封面可设。"),
    "library.facade.cover.album_outside": "「{name}」不在当前库里，无法设置封面。",
    "library.facade.cover.no_config_dir": (
        "当前库无法创建 {name} 目录，封面设置无处保存。请将文件夹移动到可写入的位置后重试。"
    ),
    "library.facade.movie.failure": "{code}：{reason}",
    "library.facade.movie.cover_failure": "{code}：未能设成封面，{reason}",
    "library.facade.video_decode": "视频不走图片解码：{name} 的画面由播放器提供。",
    # ---- 索引 ------------------------------------------------------------------
    "library.index.unavailable": (
        "当前库无法建立检索索引：通常是因为照片所在磁盘不允许写入缓存。分组与排序仍然可用。"
    ),
    "library.index.by_date_unavailable": (
        "当前库无法建立索引：通常是因为照片所在磁盘不允许写入缓存，因此无法按日期范围挑选照片。"
    ),
    "library.index.search_failed": "检索未能完成，请重试。",
    "library.index.match_failed": "筛选未能完成，请重试。",
    "library.index.by_date_failed": "按日期范围挑选照片未能完成，请重试。",
    # ---- 文件识别 --------------------------------------------------------------
    "library.photos.unknown_format": "未知",
    # ---- 扫描 ------------------------------------------------------------------
    "library.scanner.folders_unreadable": (
        "{count} 个文件夹无法读取",
        "{count} 个文件夹无法读取",
    ),
    "library.scanner.files_unreadable": (
        "{count} 个文件无法读取",
        "{count} 个文件无法读取",
    ),
    "library.scanner.separator": "、",
    "library.scanner.issue_suffix": "；通常由权限问题或外接盘断开导致。",
    "library.scanner.folder_unavailable": (
        "无法打开该文件夹：{path}。它可能已被移动、重命名或删除。"
    ),
    "library.watcher.processing_failed": (
        "监听到文件变化，但处理时出了问题，详情写进了日志文件：{path}。"
    ),
    # ---- 检索字段 --------------------------------------------------------------
    "library.search.field.event_kind": "事件类型",
    "library.search.field.event": "事件",
    "library.search.field.name": "文件名",
    "library.search.field.folder": "相册",
    "library.search.field.format": "文件格式",
    "library.search.field.size": "文件大小",
    "library.search.field.captured": "拍摄时间",
    "library.search.field.camera": "相机",
    "library.search.field.lens": "镜头",
    "library.search.field.country": "国家",
    "library.search.field.region": "省/州",
    "library.search.field.city": "城市",
    "library.search.field.keyword": "关键词",
    "library.search.field.aperture": "光圈",
    "library.search.field.shutter": "快门",
    "library.search.field.focal": "焦距",
    "library.search.field.pixels": "像素尺寸",
    "library.search.field.modified": "修改时间",
    "library.search.hint.event": "京都五日",
    "library.search.hint.folder": "含子相册",
    "library.search.hint.format": "jpg 或 JPEG",
    "library.search.hint.size": "5MB 或 >10MB",
    "library.search.hint.date": "2024 / 2024-05",
    "library.search.hint.aperture": "1.8 或 < 2",
    "library.search.hint.shutter": "0.002 或 > 0.001",
    "library.search.hint.iso": "800 或 >1600",
    "library.search.hint.focal": "35 或 <= 70",
    "library.search.hint.pixels": "12000000 或 >12MP",
    "library.search.event_field_label": "{kind}·{field}",
    "library.search.reason.reversed": (
        "的区间写反了：「{left}」在「{right}」后面，起始要写在前面。"
    ),
    "library.search.reason.date": (
        "要一个日期：2024、2024-05、2024-05-16；"
        "要一段区间就写成 2023{range}2024，"
        "{range} 前后可以有空格，{half} 也认。"
    ),
    "library.search.reason.value": "这一栏要一个值，例如 {hint}。",
    # ---- 日历 ------------------------------------------------------------------
    "library.calendar.level.year": "年",
    "library.calendar.level.month": "月",
    "library.calendar.level.week": "周",
    "library.calendar.weekday.sunday": "日",
    "library.calendar.weekday.monday": "一",
    "library.calendar.weekday.tuesday": "二",
    "library.calendar.weekday.wednesday": "三",
    "library.calendar.weekday.thursday": "四",
    "library.calendar.weekday.friday": "五",
    "library.calendar.weekday.saturday": "六",
    "library.calendar.weekday.header": "星期{name}",
    "library.calendar.month.1": "1月",
    "library.calendar.month_short.1": "1月",
    "library.calendar.month.2": "2月",
    "library.calendar.month_short.2": "2月",
    "library.calendar.month.3": "3月",
    "library.calendar.month_short.3": "3月",
    "library.calendar.month.4": "4月",
    "library.calendar.month_short.4": "4月",
    "library.calendar.month.5": "5月",
    "library.calendar.month_short.5": "5月",
    "library.calendar.month.6": "6月",
    "library.calendar.month_short.6": "6月",
    "library.calendar.month.7": "7月",
    "library.calendar.month_short.7": "7月",
    "library.calendar.month.8": "8月",
    "library.calendar.month_short.8": "8月",
    "library.calendar.month.9": "9月",
    "library.calendar.month_short.9": "9月",
    "library.calendar.month.10": "10月",
    "library.calendar.month_short.10": "10月",
    "library.calendar.month.11": "11月",
    "library.calendar.month_short.11": "11月",
    "library.calendar.month.12": "12月",
    "library.calendar.month_short.12": "12月",
    "library.calendar.title.year": "{year}年",
    "library.calendar.title.month": "{year}年{name}",
    "library.calendar.title.week_span": (
        "{start_year}年{start_name}{start_day}日 – {end_name}{end_day}日"
    ),
    "library.calendar.title.week": "{span} · 第 {number} 周",
    # ---- 背景库 ----------------------------------------------------------------
    "library.backdrop.read_image": "无法读取这张背景图：{name}",
    "library.backdrop.write_cache": "背景图未能写进缓存：{path}",
    "library.backdrop.read_photo": "无法读取这张照片：{path}",
    "library.backdrop.decode_photo": "无法解码这张照片，请更换后重试：{path}",
    "library.backdrop.missing": "这张背景已不存在，可能刚被删除。",
    "library.backdrop.rebuilt_recipe": "背景「{name}」已按配方重新生成",
    "library.backdrop.rebuilt_source": "背景「{name}」已从原图重新导入",
    "library.backdrop.registry_unwritable": "背景清单不可写入：{path}",
    "library.backdrop.name_required": "请为这张背景填写名称。",
    "library.places.loading": "正在加载城市库…",
    "library.backdrop.default_name": "背景",
}
