"""各类对话框（编辑元数据 / 事件 / 背景 / 导入影片……）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "dialog.close": "关闭",
    "dialog.cancel": "取消",
    "dialog.save": "保存",
    "dialog.done": "完成",
    "dialog.delete": "删除",
    "dialog.rename": "重命名…",
    "dialog.remove": "移除",
    "dialog.name": "名称",
    "dialog.light": "设为浅色",
    "dialog.dark": "设为深色",
    "dialog.event.edit_title": "编辑事件",
    "dialog.event.new_title": "新建{kind}",
    "dialog.event.edit_named_title": "编辑「{name}」",
    "dialog.event.clear_cover": "清除封面",
    "dialog.event.no_cover": "未设置（使用第一张照片）",
    "dialog.event.cover_pending": "（待定）",
    "dialog.event.include_range": "把开始到结束之间的照片都记入",
    "dialog.event.multi_values": "多个用逗号分隔",
    "dialog.event.delete": "删除事件…",
    "dialog.order.title": "外部应用顺序",
    "dialog.order.hint": "拖动调整先后；排在第一项的应用显示在按钮左半。",
    "dialog.order.reset": "恢复这一页的默认顺序",
    "dialog.appearance.wrong_side": (
        "浅色背景请选浅色，深色背景请选深色：正文颜色不随背景改变，选反会导致看不清。"
    ),
    "dialog.appearance.light_slot": "浅色模式",
    "dialog.appearance.dark_slot": "深色模式",
    "dialog.appearance.title": "背景色",
    "dialog.appearance.hint": "两档各选一个颜色；「默认」表示使用系统浅色 / 深色模式。",
    "dialog.appearance.reset": "恢复默认",
    "dialog.appearance.slot_title": "{slot}的背景色",
    "dialog.appearance.default": "默认",
    "dialog.appearance.follow_system": "跟随系统的浅色 / 深色",
    "dialog.appearance.change": "{value}（点击更换）",
    "dialog.metadata.title": "编辑元数据",
    "dialog.metadata.title_named": "编辑「{name}」的元数据",
    "dialog.metadata.capture_time": "拍摄时间",
    "dialog.metadata.timezone": "时区",
    "dialog.metadata.country": "国家",
    "dialog.metadata.region": "省 / 州",
    "dialog.metadata.city": "城市",
    "dialog.metadata.latitude": "纬度",
    "dialog.metadata.longitude": "经度",
    "dialog.metadata.names_to_coordinates": "地点 → 经纬度",
    "dialog.metadata.names_to_coordinates_hint": (
        "按城市框中的名称，从城市库查出该市的省、国家与坐标并填入其余字段"
        "（城市库未收录的层级将被清空）。"
    ),
    "dialog.metadata.coordinates_to_names": "经纬度 → 地点",
    "dialog.metadata.coordinates_to_names_hint": (
        "按下方两个坐标框中的位置，从城市库查出最近的城市并填入上方三个地名框"
        "（城市库未收录的层级将被清空，例如直辖市没有省级行政区）。"
    ),
    "dialog.metadata.copy": "复制",
    "dialog.metadata.copy_time": "复制时间",
    "dialog.metadata.copy_place": "复制位置",
    "dialog.metadata.paste": "粘贴",
    "dialog.metadata.paste_time": "粘贴时间",
    "dialog.metadata.paste_place": "粘贴位置",
    "dialog.metadata.clear_place": "清空位置",
    "dialog.metadata.copied": "已复制",
    "dialog.metadata.error.city_required": "请先填写城市：城市库按市名查询。",
    "dialog.metadata.error.coordinates_required": "请先填写经纬度，两项均必填。",
    "dialog.metadata.error.no_nearby_city": "附近没有城市记录，地名未做改动。",
    "dialog.metadata.error.nothing_to_copy": "这张照片没有可复制的内容。",
    "dialog.metadata.error.clipboard_empty": "剪贴板中没有可用的内容。",
    "dialog.metadata.error.clipboard_no_time": "剪贴板中没有可用的拍摄时间。",
    "dialog.metadata.error.clipboard_no_place": "剪贴板中没有可用的位置。",
    "dialog.metadata.error.bad_time": (
        "无法识别拍摄时间「{text}」。请使用 2024-05-16 20:52:02 格式，秒可省略。"
    ),
    "dialog.metadata.error.bad_timezone": (
        "无法识别时区「{text}」。请使用 +08:00 或 -05:00 格式；留空表示不记录时区。"
    ),
    "dialog.metadata.error.coordinates_pair": "经度与纬度需同时填写：只填一项无法确定位置。",
    "dialog.metadata.error.not_a_number": (
        "{label}不是有效数字「{text}」。请填十进制度，例如 30.27415。"
    ),
    "dialog.metadata.error.out_of_range": "{label}超出范围（{limit}）：填入 {value}。",
    "dialog.metadata.error.city_not_found": "城市库里没有「{city}」，省与国家、坐标都保持原样。",
    "dialog.backdrop.generate_title": "生成水墨背景",
    "dialog.backdrop.palette_hint": (
        "选择配色；双击色块可以编辑它的颜色，也可以删掉这一套。"
        "「+」新增的配色与改过的预设都会保存下来。"
    ),
    "dialog.backdrop.add_palette": "新增一套配色",
    "dialog.backdrop.reset_palettes": "恢复预设配色",
    "dialog.backdrop.reset_palettes_tip": "把八套预设恢复成原样；「+」新增的配色不受影响",
    "dialog.backdrop.reroll": "换一张",
    "dialog.backdrop.chip_tip": "{title}（双击可编辑颜色）",
    "dialog.backdrop.new_palette": "新增配色",
    "dialog.backdrop.name_required": "请为这张背景填写名称。",
    "dialog.backdrop.palette_colors_hint": (
        "从上到下就是从浅到深的次序；至少两个颜色，点色块可以换色。"
    ),
    "dialog.backdrop.add_color": "添加颜色…",
    "dialog.backdrop.colour_dialog_title": "配色里的颜色",
    "dialog.backdrop.add_colour_title": "加一个颜色",
    "dialog.backdrop.delete_palette": "删除这套配色",
    "dialog.backdrop.delete_palette_tip": "删掉这一套配色；已经保存的背景图不受影响",
    "dialog.backdrop.colour_swatch_tip": "{colour}（点击换色）",
    "dialog.backdrop.photo_title": "使用照片背景",
    "dialog.backdrop.photo_hint": "照片将复制一份到 Gallery 缓存，原文件不变。",
    "dialog.backdrop.save_only": "仅保存",
    "dialog.backdrop.manage_title": "管理背景",
    "dialog.backdrop.manage_hint": (
        "改名与删除只作用于 Gallery 缓存中的副本，原照片不变；"
        "点「完成」后生效，取消则不保存任何改动。"
    ),
    "dialog.backdrop.empty": "暂无已保存的背景图",
    "dialog.backdrop.edit_title": "编辑颜色 · {title}",
    "dialog.backdrop.custom": "自定义",
    "dialog.backdrop.palette_limit": "最多新增 {count} 套配色（删掉一套自己的可以再添）",
    "dialog.movie.start": "导入",
    "dialog.movie.folder": "写入文件夹：{path}",
    "dialog.movie.example_required": "请至少填写一个编号或链接，例如 {example}。",
    "dialog.movie.bilibili.title": "导入 Bilibili 影片",
    "dialog.movie.bilibili.heading": "按 BV 号或链接导入 Bilibili 影片",
    "dialog.movie.bilibili.hint": (
        "每行一个 BV 号或 B 站视频地址（b23.tv 短链请先在浏览器中打开，再粘贴完整地址）。"
        "封面保存到下方文件夹。"
    ),
    "dialog.movie.youtube.title": "导入 YouTube 影片",
    "dialog.movie.youtube.heading": "按链接或 videoId 导入 YouTube 影片",
    "dialog.movie.youtube.hint": (
        "每行一个 YouTube 地址或 11 位 videoId（youtu.be 与 watch 链接都行）。"
        "封面保存到下方文件夹。"
    ),
    "dialog.movie.douyin.title": "导入抖音影片",
    "dialog.movie.douyin.heading": "按链接或编号导入抖音影片",
    "dialog.movie.douyin.hint": (
        "每行一个抖音视频地址或分享短链（v.douyin.com 开头），也可以直接填视频编号。"
        "短链需联网展开一次，才能确定对应的视频。封面保存到下方文件夹。"
    ),
    "dialog.movie.tiktok.title": "导入 TikTok 影片",
    "dialog.movie.tiktok.heading": "按链接或编号导入 TikTok 影片",
    "dialog.movie.tiktok.hint": (
        "每行一个 TikTok 视频地址或分享链接（vm.tiktok.com、tiktok.com/t 开头都行），"
        "也可以直接填视频 id。封面保存到下方文件夹。"
    ),
    "dialog.movie.javdb.title": "导入 JavDB 影片",
    "dialog.movie.javdb.heading": "按番号导入 JavDB 影片",
    "dialog.movie.javdb.hint": (
        "每行一个番号，例如 IPX-660；大小写与连字符都可以按习惯写。封面保存到下方文件夹。"
    ),
    "dialog.movie.iqiyi.title": "导入爱奇艺影片",
    "dialog.movie.iqiyi.heading": "按链接导入爱奇艺影片",
    "dialog.movie.iqiyi.hint": (
        "每行一个爱奇艺视频地址，地址里 v_ 后面那一串就是编号；"
        "整部剧的专辑地址（a_ 开头）不受支持，请打开某一集后再复制。封面保存到下方文件夹。"
    ),
    "dialog.movie.tencent.title": "导入腾讯视频影片",
    "dialog.movie.tencent.heading": "按链接或 vid 导入腾讯视频影片",
    "dialog.movie.tencent.hint": (
        "每行一个腾讯视频地址或 11 位 vid；整部剧的合集地址不受支持，请打开某一集后再复制。"
        "封面保存到下方文件夹。"
    ),
    "dialog.movie.youku.title": "导入优酷影片",
    "dialog.movie.youku.heading": "按链接或编号导入优酷影片",
    "dialog.movie.youku.hint": "每行一个优酷视频地址或编号（X 开头那一串）。封面保存到下方文件夹。",
    "dialog.movie.mango.title": "导入芒果 TV 影片",
    "dialog.movie.mango.heading": "按链接或编号导入芒果 TV 影片",
    "dialog.movie.mango.hint": (
        "每行一个芒果 TV 视频地址（/b/… 那一页）或单集编号。整部剧的页面（/h/ 开头）不行，"
        "请点开某一集再复制。有的影片芒果未提供上映日期，该字段将留空。封面保存到下方文件夹。"
    ),
    "dialog.movie.pornhub.title": "导入 Pornhub 影片",
    "dialog.movie.pornhub.heading": "按链接或编号导入 Pornhub 影片",
    "dialog.movie.pornhub.hint": (
        "每行一个 Pornhub 视频地址，或地址里 viewkey= 后面那一串编号"
        "（view_video.php 与 /embed/ 两种地址都行）。作者页与合集页请先打开某一部再复制。"
        "封面保存到下方文件夹。"
    ),
}
