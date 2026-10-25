"""事件的字段、默认名与各来源的失败原因。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "event.kind.trip.empty": "未命名旅行",
    "event.kind.show.empty": "未命名演出",
    "event.kind.movie.empty": "未命名影片",
    "event.kind.trip": "旅行",
    "event.kind.show": "演出",
    "event.kind.movie": "影片",
    "event.field.cover": "封面",
    "event.field.title": "名称",
    "event.field.title.hint": "留空时按日期与关键字段自动生成",
    "event.field.started_on": "开始日期",
    "event.field.started_on.hint": "2026-05-16，只知道年月也可以",
    "event.field.ended_on": "结束日期",
    "event.field.ended_on.hint": "当天往返时与开始日期相同",
    "event.field.note": "备注",
    "event.field.destination": "目的地",
    "event.field.destination.hint": "多个地点用逗号分隔",
    "event.field.companions": "同行者",
    "event.field.companions.hint": "用逗号分开",
    "event.field.transport": "交通方式",
    "event.field.lodging": "住宿",
    "event.field.cost": "花费",
    "event.field.cost.hint": "只填数字；不记录时留空",
    "event.field.rating": "评分",
    "event.field.artist": "艺人",
    "event.field.artist.hint": "多个用逗号分隔",
    "event.field.guests": "嘉宾",
    "event.field.guests.hint": "多个用逗号分隔",
    "event.field.program": "剧目",
    "event.field.program.hint": "话剧、音乐剧的剧目名",
    "event.field.show_type": "演出类型",
    "event.field.show_type.hint": "演唱会、音乐剧、话剧……",
    "event.field.start_time": "开场时间",
    "event.field.city": "城市",
    "event.field.venue": "场馆",
    "event.field.venue.hint": "如 国家大剧院",
    "event.field.ticket_price": "票价",
    "event.field.ticket_price.hint": "票面价格，只填数字",
    "event.field.paid": "实付",
    "event.field.paid.hint": "实际支付，只填数字",
    "event.field.seat": "座位",
    "event.field.seat.hint": "如 A区 12排 5号",
    "event.field.channel": "购买渠道",
    "event.field.channel.hint": "如 大麦、秀动",
    "event.field.thoughts": "感想",
    "event.field.source": "来源",
    "event.field.code": "编号",
    "event.field.code.hint": (
        "番号、YouTube 的 videoId、B 站 BV 号，或腾讯 / 爱奇艺 / 优酷 / 芒果 / "
        "抖音 / TikTok / Pornhub 的编号"
    ),
    "event.field.duration": "时长",
    "event.field.duration.hint": "分钟，只填数字",
    "event.field.creator": "作者",
    "event.field.creator.hint": "片商、频道、UP 主",
    "event.field.label": "发行商",
    "event.field.series": "系列",
    "event.field.series.hint": "系列、合集",
    "event.field.director": "导演",
    "event.field.actors": "演员",
    "event.field.actors.hint": "多个用逗号分隔",
    "event.field.tags": "标签",
    "event.field.tags.hint": "多个用逗号分隔",
    "event.suggested_title.new": "新的{kind}",
    "event.problem.title": "这一次{kind}得有个名字：以后只能靠它认出是哪一次。",
    "event.problem.started_on": "无法识别开始日期「{date}」。请使用 2026-05-16，或只填 2026-05。",
    "event.problem.ended_on": "无法识别结束日期「{date}」。请使用 2026-05-20，或只填 2026-05。",
    "event.problem.ended_before_started": "结束日期（{end}）早于开始日期（{start}）还早。",
    "event.problem.cost_not_number": (
        "{label}不是有效数字「{text}」。请只填数字，例如 3280 或 3280.50。"
    ),
    "event.problem.cost_negative": "{label}不能为负数。",
    "event.error.unknown_kind": "认不出的事件类型：{kind}",
    "event.error.not_found": "该事件已不存在。",
    "event.error.undo_unreadable": "该事件记录无法读取，无法撤销。",
    "event.error.undo_unknown_kind": "认不出的事件类型：{kind}。",
    "event.error.undo_id_taken": "库里已经有一条 id 为 {event_id} 的事件，不敢覆盖。",
    "event.error.cover_not_bound": "只能从该事件的照片中选择封面。",
    "event.error.event_missing": "没有 id 为 {event_id} 的事件",
    "event.error.events_unavailable": "事件库当前不可用：当前库无法创建配置目录。",
    "event.orphan.unnamed": "未命名",
    "event.import_shows.not_show_export": (
        "该表格中未找到「演出名称」列，可能不是「演迹」导出的演出列表。"
    ),
    "event.import_shows.skipped_activity": "第 {row} 行是活动而非演出，已跳过。",
    "event.import_shows.skipped_no_title": "第 {row} 行缺少演出名称，已跳过。",
    "event.import_shows.file_missing": "找不到这个文件：{name}。",
    "event.import_shows.not_xlsx": "「{name}」不是「演迹」导出的 .xlsx 表格。",
    "event.transport.unknown_reason": "未知原因",
    "event.transport.unreachable": "连不上源站，稍后再试：{reason}。",
    "event.transport.webpage_not_image": "封面下载回来的是一个网页，不是图片。",
    "event.movie.empty_input": "编号或链接不能为空。",
    "event.movie.unrecognized_url": (
        "无法识别该地址：{url}。短链请先在浏览器中打开，再粘贴完整地址；"
        "合集页请先打开某一集再复制。"
    ),
    "event.movie.unknown_source": "认不出这个来源：{source}。",
    "event.movie.no_cover": "{code} 没有封面图。",
    "event.jav.not_found": "源站未收录该编号：{code}。",
    "event.jav.multiple": "编号 {code} 找到多条近似结果，无法确定是哪一部。",
    "event.jav.no_results": "源站未收录该编号：{code}。请核对写法，或更换编号后重试。",
    "event.jav.search_blocked": "源站未提供搜索结果：{code}。通常是限速或访问受限，请稍后重试。",
    "event.jav.empty_code": "编号不能为空。",
    "event.jav.no_cover": "编号 {code} 的页面里没找到封面图。",
    "event.youtube.embed_forbidden": (
        "YouTube 不给该视频的资料，不允许嵌入播放：{code}。请在浏览器中打开确认。"
    ),
    "event.youtube.not_found": "YouTube 未收录该视频：{code}。它可能已被删除或设为不公开。",
    "event.youtube.http": "YouTube 本次未提供信息：HTTP {status}，{code}。",
    "event.youtube.invalid_code": "不是有效的 YouTube videoId：{code}。",
    "event.youtube.unparsable": "YouTube 返回的信息无法解析：{code}。",
    "event.youtube.invalid_payload": "YouTube 返回的信息不是有效的视频信息：{code}。",
    "event.bilibili.problem.invalid_code": "B 站返回：编号不合法",
    "event.bilibili.problem.forbidden": "B 站不让看该稿件，权限不足",
    "event.bilibili.problem.not_found": "B 站未收录该稿件",
    "event.bilibili.problem.risk_control": "B 站风控未放行，请稍后重试，或先在浏览器中打开",
    "event.bilibili.problem.unavailable": "该稿件不可见，可能已删除或设为私密",
    "event.bilibili.problem.under_review": "该稿件仍在审核中，请稍后重试",
    "event.bilibili.problem.up_only": "该稿件仅 UP 主可见",
    "event.bilibili.problem_known": "{reason}：{code}。",
    "event.bilibili.problem_unknown": "B 站未提供该稿件的信息：错误码 {status}。",
    "event.bilibili.problem_unknown_message": (
        "B 站未提供该稿件的信息：错误码 {status}，{message}。"
    ),
    "event.bilibili.no_info": "B 站未提供该稿件的信息：{code}。",
    "event.bilibili.unparsable": "B 站返回的信息无法解析：{code}。",
    "event.bilibili.invalid_payload": "B 站返回的信息不是有效的稿件信息：{code}。",
    "event.bilibili.invalid_code": "不是有效的 BV 号：{code}。",
    "event.douyin.invalid_code": "不是有效的抖音视频编号或分享短链：{code}。",
    "event.douyin.share_expand_failed": (
        "无法展开该抖音分享链接：{code}。请先在浏览器中打开，再粘贴完整地址。"
    ),
    "event.douyin.share_resolve_failed": (
        "无法从该抖音分享链接解析出视频编号：{code}。请先在浏览器中打开，再粘贴完整地址。"
    ),
    "event.douyin.unparsable": "抖音返回的信息无法解析：{code}。",
    "event.douyin.invalid_payload": "抖音返回的信息不是有效的视频信息：{code}。",
    "event.douyin.not_found": "抖音未收录该视频：{code}。",
    "event.douyin.no_info": (
        "抖音未提供该视频的信息：{code}。视频可能已删除或设为私密，"
        "也可能是站点页面结构变更；请在浏览器中打开确认。"
    ),
    "event.douyin.no_title": "抖音未提供该视频的标题：{code}。",
    "event.iqiyi.problem": "爱奇艺未提供该视频的信息：{status}（{code}）。",
    "event.iqiyi.problem_message": "爱奇艺未提供该视频的信息：{status}，{message}（{code}）。",
    "event.iqiyi.no_info": "爱奇艺未提供该视频的信息：{code}。",
    "event.iqiyi.invalid_code": "不是有效的爱奇艺编号：{code}。",
    "event.iqiyi.not_found": "爱奇艺未收录该视频：{code}。",
    "event.iqiyi.no_tvid": (
        "爱奇艺这一页未提供视频编号：{code}。视频可能已下架，或该地址不是单集 / 单片。"
    ),
    "event.iqiyi.unparsable": "爱奇艺返回的信息无法解析。",
    "event.iqiyi.invalid_payload": "爱奇艺返回的信息不是有效的视频信息。",
    "event.tencent.problem.not_found": "腾讯视频未收录该视频，或它已下架",
    "event.tencent.problem.under_review": "腾讯视频说该视频仍在审核中，请稍后重试",
    "event.tencent.problem_known": "{reason}：{code}。",
    "event.tencent.problem_unknown": "腾讯视频未提供该视频的信息：错误码 {status}。",
    "event.tencent.problem_unknown_message": (
        "腾讯视频未提供该视频的信息：错误码 {status}，{message}。"
    ),
    "event.tencent.no_info": "腾讯视频未提供该视频的信息：{code}。",
    "event.tencent.unparsable": "腾讯视频返回的信息无法解析。",
    "event.tencent.invalid_payload": "腾讯视频返回的信息不是有效的视频信息。",
    "event.tencent.invalid_code": "不是有效的腾讯视频 vid：{code}。",
    "event.youku.no_info_reason": (
        "优酷这一页未提供影片信息：{code}。站点可能更改了页面结构，请在浏览器中打开确认。"
    ),
    "event.youku.no_info": "优酷这一页未提供影片信息：{code}。",
    "event.youku.invalid_code": "不是有效的优酷编号：{code}。",
    "event.youku.risk_control": "优酷风控未放行：{code}。请稍后重试，或者先在浏览器里打开它。",
    "event.mango.problem.not_found": "芒果 TV 未收录该视频",
    "event.mango.problem_known": "{reason}：{code}。",
    "event.mango.problem_unknown": "芒果 TV 未提供该视频的信息：错误码 {status}。",
    "event.mango.problem_unknown_message": (
        "芒果 TV 未提供该视频的信息：错误码 {status}，{message}。"
    ),
    "event.mango.no_info": "芒果 TV 未提供该视频的信息：{code}。",
    "event.mango.invalid_code": "不是有效的芒果 TV vid：{code}。",
    "event.mango.unparsable": "芒果 TV 返回的信息无法解析。",
    "event.mango.invalid_payload": "芒果 TV 返回的信息不是有效的视频信息。",
    "event.tiktok.no_info_region": (
        "TikTok 未提供该视频的信息；它可能已被删除、设为私密或受地区限制：{code}。"
    ),
    "event.tiktok.not_found": "TikTok 未收录该视频：{code}。",
    "event.tiktok.http": "TikTok 本次未提供信息：HTTP {status}，{code}。",
    "event.tiktok.unparsable": "TikTok 返回的信息无法解析：{code}。",
    "event.tiktok.no_info": "TikTok 未提供该视频的信息：{code}。",
    "event.tiktok.invalid_code": "不是有效的 TikTok 视频 id 或分享链接：{code}。",
    "event.tiktok.no_id": "TikTok 未提供该视频的 id：{code}。",
    "event.pornhub.no_info_reason": (
        "Pornhub 这一页未提供影片信息：{code}。站点可能更改了页面结构，请在浏览器中打开确认。"
    ),
    "event.pornhub.geo_blocked": "该视频在当前的网络所在地看不到：{code}。",
    "event.pornhub.unavailable": "Pornhub 未收录该视频，或它已被删除 / 设为私密：{code}。",
    "event.pornhub.invalid_code": "不是有效的 Pornhub 编号：{code}。",
    "event.pornhub.not_found": "Pornhub 未收录该视频：{code}。",
    "event.field.start_time.hint": "19:30",
    "event.kind.default_empty": "未命名",
}
