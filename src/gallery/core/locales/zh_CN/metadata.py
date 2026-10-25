"""元数据的字段名、报告与写回消息。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    # ---- 格式化 ----------------------------------------------------------------
    "metadata.format.unknown_size": "未知大小",
    # ---- 字段标签（报告与分组共用）---------------------------------------------
    "metadata.label.camera": "相机",
    "metadata.label.lens": "镜头",
    "metadata.label.captured": "拍摄时间",
    "metadata.label.year": "拍摄年份",
    "metadata.label.month": "拍摄月份",
    "metadata.label.day": "拍摄日期",
    "metadata.label.event": "事件",
    "metadata.label.country": "国家",
    "metadata.label.region": "省 / 州",
    "metadata.label.city": "城市",
    "metadata.label.keyword": "关键词",
    "metadata.label.exposure_time": "曝光时间",
    "metadata.label.aperture": "光圈",
    "metadata.label.exposure_bias": "曝光补偿",
    "metadata.label.exposure_program": "曝光程序",
    "metadata.label.metering": "测光模式",
    "metadata.label.flash": "闪光灯",
    "metadata.label.focal_length": "焦距",
    "metadata.label.focal_35mm": "35mm 等效",
    "metadata.label.white_balance": "白平衡",
    "metadata.label.orientation": "方向",
    "metadata.label.place": "地点",
    "metadata.label.sub_location": "子地点",
    "metadata.label.coordinates": "坐标",
    "metadata.label.altitude": "海拔",
    "metadata.label.gps_time": "定位时间",
    "metadata.label.pixel_size": "像素尺寸",
    "metadata.label.color_space": "色彩空间",
    "metadata.label.software": "处理软件",
    "metadata.label.duration": "时长",
    "metadata.label.frame_rate": "帧率",
    "metadata.label.codec": "编码",
    "metadata.label.bitrate": "码率",
    "metadata.label.rating": "评级",
    "metadata.label.title": "标题",
    "metadata.label.description": "说明",
    "metadata.label.creator": "创建者",
    "metadata.label.copyright": "版权",
    "metadata.label.filename": "文件名",
    "metadata.label.folder": "所在文件夹",
    "metadata.label.format": "格式",
    "metadata.label.size": "大小",
    "metadata.label.modified": "修改时间",
    "metadata.label.shutter": "快门",
    "metadata.label.pixels": "像素尺寸",
    "metadata.label.unknown": "未知",
    # ---- 报告的分组、值与拼法 --------------------------------------------------
    "metadata.group.shooting": "拍摄信息",
    "metadata.group.location": "位置",
    "metadata.group.image": "图像",
    "metadata.group.video": "视频",
    "metadata.group.content": "内容",
    "metadata.group.file": "文件",
    "metadata.value.seconds": "{value} 秒",
    "metadata.value.uncalibrated": "未校准（Uncalibrated）",
    "metadata.text.notice": "（{notice}）",
    "metadata.text.group": "【{title}】",
    "metadata.text.field": "{label}：{value}",
    "metadata.text.coordinates_separator": "，",
    "metadata.text.list_separator": "、",
    "metadata.text.clause_separator": "；",
    "metadata.text.inferred_place": "地点（推断）",
    # ---- 分组字段的组名与空值 --------------------------------------------------
    "metadata.facets.group.year": "{year} 年",
    "metadata.facets.group.month": "{year} 年 {month} 月",
    "metadata.facets.group.day": "{year} 年 {month} 月 {day} 日",
    "metadata.facets.empty.event_field": "未填{label}",
    "metadata.facets.event_field_label": "{kind} · {field}",
    "metadata.empty.time": "时间未知",
    "metadata.empty.camera": "相机未知",
    "metadata.empty.lens": "镜头未知",
    "metadata.empty.event": "未归入事件",
    "metadata.empty.country": "国家未知",
    "metadata.empty.region": "省州未知",
    "metadata.empty.city": "城市未知",
    "metadata.empty.keyword": "无关键词",
    "metadata.empty.folder": "（根目录）",
    "metadata.empty.unknown": "未知",
    # ---- Pillow 降级路径的枚举 ------------------------------------------------
    "metadata.exif.flash.off": "未闪光",
    "metadata.exif.flash.on": "闪光",
    "metadata.exif.flash.no_return": "闪光（未检测到回光）",
    "metadata.exif.flash.return": "闪光（检测到回光）",
    "metadata.exif.flash.forced": "闪光（强制）",
    "metadata.exif.flash.off_forced": "未闪光（强制）",
    "metadata.exif.flash.off_auto": "未闪光（自动）",
    "metadata.exif.flash.auto": "闪光（自动）",
    "metadata.exif.white_balance.auto": "自动",
    "metadata.exif.white_balance.manual": "手动",
    "metadata.exif.orientation.normal": "正常",
    "metadata.exif.orientation.rotated_180": "旋转 180°",
    "metadata.exif.orientation.clockwise_90": "顺时针 90°",
    "metadata.exif.orientation.counterclockwise_90": "逆时针 90°",
    "metadata.exif.color_space.uncalibrated": "未校准（Uncalibrated）",
    "metadata.exif.exposure_program.undefined": "未定义",
    "metadata.exif.exposure_program.manual": "手动",
    "metadata.exif.exposure_program.program": "程序自动",
    "metadata.exif.exposure_program.aperture_priority": "光圈优先",
    "metadata.exif.exposure_program.shutter_priority": "快门优先",
    "metadata.exif.exposure_program.creative": "创意",
    "metadata.exif.exposure_program.action": "运动",
    "metadata.exif.exposure_program.portrait": "人像",
    "metadata.exif.exposure_program.landscape": "风景",
    "metadata.exif.metering.unknown": "未知",
    "metadata.exif.metering.average": "平均",
    "metadata.exif.metering.center_weighted": "中央重点",
    "metadata.exif.metering.spot": "点测光",
    "metadata.exif.metering.multi": "多点",
    "metadata.exif.metering.evaluative": "评价",
    "metadata.exif.metering.partial": "局部",
    # ---- exiftool 读取 --------------------------------------------------------
    "metadata.exif.timeout": "读取元数据超时（超过 {seconds:g} 秒）：{name}。",
    "metadata.exif.cannot_run": "无法运行 exiftool：{reason}。",
    "metadata.exif.unparsable": "exiftool 的输出无法解析：{name}。",
    "metadata.exif.detail_output": "{error}；输出前 200 字：{output}",
    "metadata.exif.no_payload": "exiftool 没有返回可用信息：{name}。",
    "metadata.exif.unreadable": "读不到这个文件：{name}，{reason}。",
    "metadata.exif.not_installed": (
        "未找到 exiftool，无法读取完整元数据。安装后重试：brew install exiftool"
    ),
    "metadata.exif.fallback_notice": "exiftool 读取失败（{reason}），下面显示的是基础信息。",
    "metadata.exif.pillow_notice": (
        "未安装 exiftool，只显示基础 EXIF（读不到 IPTC / XMP / 制造商注释）。"
        "安装后可看到完整信息：brew install exiftool"
    ),
    "metadata.exif.pillow_unreadable": "无法读取这个文件的基本信息：{name}，{error}。",
    # ---- 失败的统一说法 --------------------------------------------------------
    "metadata.failure.source_gone": "该文件已不在原位置，可能刚被其他程序移动。",
    "metadata.failure.same_file": "源和目标是同一个东西。",
    "metadata.failure.disk_full": "磁盘已满，请释放空间后重试。",
    "metadata.failure.permission": "没有权限，系统返回 {reason}。{hint}",
    "metadata.failure.permission_folder_hint": (
        "请检查「系统设置 → 隐私与安全性 → 文件与文件夹」里有没有授权。"
    ),
    "metadata.failure.permission_file_hint": "请检查它是否为只读，或正被其他程序占用。",
    "metadata.failure.name_too_long": "文件名过长，超出当前磁盘的限制。",
    "metadata.failure.action.copy": "复制",
    "metadata.failure.action.move": "移动",
    "metadata.failure.action_failed": "{action}失败：{reason}。它可能正在被其他程序使用。",
    "metadata.failure.unknown": "{reason}。它可能正在被其他程序使用。",
    # ---- 起名字 ----------------------------------------------------------------
    "metadata.naming.empty": "新名字不能为空。",
    "metadata.naming.reserved": "「{name}」是保留名字，不能用。",
    "metadata.naming.leading_dot": "名字不能以「.」开头：以点开头的文件不会出现在 Gallery 里。",
    "metadata.naming.slash": "名字里不能带「/」：改名只改名字，不会移动文件。",
    "metadata.naming.control": "名字里不能有回车、Tab 等控制字符。",
    "metadata.naming.too_long": "名字太长了：{size} 字节，最多只能 {limit} 字节。",
    # ---- 新建相册 --------------------------------------------------------------
    "metadata.album.name_too_long": "「{name}」名称过长，加上编号后仍超出限制。",
    "metadata.album.exists": "「{name}」已存在。",
    "metadata.album.create_failed": "未能新建相册：{reason}",
    "metadata.album.not_folder": "「{name}」不是一个文件夹。",
    "metadata.album.guard.outside_root": "只能在库里的文件夹中新建相册：{path}。",
    "metadata.album.guard.is_root": "不能在库本身里新建相册，请选一个文件夹。",
    "metadata.album.guard.is_config_dir": "这是 Gallery 自己的配置目录，不能往里建相册。",
    # ---- 下载的字节写成新文件 --------------------------------------------------
    "metadata.download.guard": "不能把封面写到这里：{reason}。",
    "metadata.download.not_folder": "「{name}」不是一个文件夹。",
    "metadata.download.no_name": "「{name}」中没有可用的名称。",
    "metadata.download.failed": "未能把封面写进「{name}」：{reason}。",
    # ---- 拍摄时间 --------------------------------------------------------------
    "metadata.capture_time.unrecognized": (
        "无法识别拍摄时间「{text}」。请填成 2024-05-16 20:52:02 这样的写法，秒可以省。"
    ),
    "metadata.capture_time.bad_offset": ("无法识别时区「{text}」。请使用 +08:00 或 -05:00 格式。"),
    # ---- 粘贴与复制 ------------------------------------------------------------
    "metadata.paste.not_folder": "「{name}」不是一个文件夹。",
    "metadata.paste.source_gone": "源文件已不存在。",
    "metadata.paste.into_self": "不能把文件夹粘贴到它自身或其子目录中。",
    "metadata.paste.name_too_long": "名称过长，加上编号后仍超出限制。",
    "metadata.paste.exists": "该位置已有同名文件或文件夹，未覆盖。",
    "metadata.paste.guard.outside_root": "只能粘贴到库中的文件夹：{path}。",
    "metadata.paste.guard.is_root": "不能粘贴到库本身，请选择文件夹。",
    "metadata.paste.guard.is_config_dir": "这是 Gallery 的配置目录，不能粘贴照片。",
    # ---- 位置编辑 --------------------------------------------------------------
    "metadata.place_edit.pair_required": "经度与纬度需同时填写：只填一项无法确定位置。",
    "metadata.place_edit.latitude_range": "纬度要在 -90 到 90 之间，写的是 {value}。",
    "metadata.place_edit.longitude_range": "经度要在 -180 到 180 之间，写的是 {value}。",
    "metadata.place_edit.null_island": ("经纬度均为 0：该坐标位于几内亚湾，通常表示没有位置信息。"),
    # ---- 改名 ------------------------------------------------------------------
    "metadata.rename.section_title": (
        "「{name}」是分区的标题，不是磁盘上的文件，无法重命名。请选中它下面的照片后再重命名。"
    ),
    "metadata.rename.group": (
        "「{name}」是一个分组，不是磁盘上的文件，改不了名。双击进入分组后选中里面的照片再改名。"
    ),
    "metadata.rename.target_exists": "这里已经有一个叫「{name}」已存在，请更换名称。",
    "metadata.rename.failed": "改名失败：{reason}",
    "metadata.rename.undo.missing": "无法撤销：未找到「{name}」，它可能已被移动或删除。",
    "metadata.rename.undo.occupied": "无法撤销：「{name}」已被其他文件占用。",
    "metadata.rename.undo.failed": "撤销失败：{reason}",
    "metadata.rename.suffix_changed": (
        "不能修改扩展名：从 {old} 改为 {new} 后，文件将不再属于 Gallery 支持的格式。"
    ),
    "metadata.rename.guard.outside_root": "只能重命名库里的东西：{path}。",
    "metadata.rename.guard.is_root": "这是库本身，改名会让 Gallery 找不到照片。",
    "metadata.rename.guard.is_config_dir": "这是 Gallery 的配置目录，不能重命名。",
    # ---- 移动到相册 ------------------------------------------------------------
    "metadata.transfer.not_album": "「{name}」不是一个相册。",
    "metadata.transfer.album_into_self": "不能把相册移入它自身或其子相册。",
    "metadata.transfer.name_too_long": "名称过长，加上编号后仍超出限制。",
    "metadata.transfer.undo.gone_from": "「{current}」已不在「{folder}」中，可能已被移动。",
    "metadata.transfer.undo.occupied": "「{name}」所在位置已有其他文件。",
    "metadata.transfer.guard.outside_root": "只能移到库里的相册：{path}。",
    "metadata.transfer.guard.is_root": "不能把照片移到库本身，请选一个相册。",
    "metadata.transfer.guard.is_config_dir": "这是 Gallery 自己的配置目录，不能移东西进去。",
    # ---- 废纸篓 ----------------------------------------------------------------
    "metadata.trash.unavailable": "无法使用废纸篓：{reason}",
    "metadata.trash.move_failed": "无法移到废纸篓：{reason}",
    "metadata.trash.section": (
        "分区标题不是磁盘上的文件，无法移除；请选中它下面的照片后再移到废纸篓。"
    ),
    "metadata.trash.group": (
        "分组不是磁盘上的文件，无法移除；请双击进入分组，选中其中的照片后再移到废纸篓。"
    ),
    "metadata.trash.restore.missing": (
        "废纸篓中已找不到「{name}」；废纸篓可能已被清空，或你已在访达中还原。"
    ),
    "metadata.trash.restore.occupied": "「{name}」所在位置已有其他文件。",
    "metadata.trash.name_too_long": "「{name}」名称过长，在废纸篓中加上编号后仍超出限制。",
    "metadata.trash.guard.outside_root": "只能移除库中的项目：{path}。",
    "metadata.trash.guard.is_root": "这是库本身，删除后 Gallery 将找不到任何照片。",
    "metadata.trash.guard.is_config_dir": "这是 Gallery 的配置目录，不能删除。",
    # ---- 撤销栈 ----------------------------------------------------------------
    "metadata.undo.label.rename": "重命名",
    "metadata.undo.label.trash": "移到废纸篓",
    "metadata.undo.label.move": "移动到相册",
    "metadata.undo.label.metadata": "编辑元数据",
    "metadata.undo.label.event": "修改事件",
    "metadata.undo.label.event_delete": "删除事件",
    "metadata.undo.label.fallback": "上一步",
    "metadata.undo.failure.event": "事件",
    "metadata.undo.failure.event_unavailable": "本次无法打开事件库，无法撤销。",
    # ---- 写回 ------------------------------------------------------------------
    "metadata.writeback.install_hint": "安装后即可使用：brew install exiftool",
    "metadata.writeback.not_installed": "未找到 exiftool，无法修改照片元数据。{hint}",
    "metadata.writeback.read_failed": "无法读取「{name}」现在的元数据，无法确认写入结果。",
    "metadata.writeback.detail_output": "{error}；输出前 200 字：{output}",
    "metadata.writeback.timeout.read": (
        "读取元数据超时，超过 {seconds:g} 秒：{name}。请稍后重试。"
    ),
    "metadata.writeback.timeout.write": (
        "写入元数据超时，超过 {seconds:g} 秒：{name}。请稍后重试。"
    ),
    "metadata.writeback.cannot_run": "无法运行 exiftool：{reason}。",
    "metadata.writeback.no_fields": "这条记录里没有可撤销的字段。",
    "metadata.writeback.undo.missing": "无法撤销：未找到「{name}」，它可能已被移动或删除。",
    "metadata.writeback.conflict": (
        "该项在 Gallery 之外被修改过；写回原值会覆盖它，因此未做改动。"
    ),
    "metadata.writeback.nothing": "没有需要写入的改动。",
    "metadata.writeback.unstorable": (
        "「{name}」存不下要写的字段：{fields}。这种格式放不了它们，一个字都没写进去。"
    ),
    "metadata.writeback.not_writable": (
        "「{tag}」不是可修改的字段。Gallery 只修改拍摄时间与位置。"
    ),
    "metadata.writeback.not_photo": "「{name}」不是 Gallery 支持的照片格式，不能修改其元数据。",
    "metadata.writeback.missing": "未找到该文件：{name}。",
    "metadata.writeback.guard.outside_root": "只能修改库中的照片：{path}。",
    "metadata.writeback.guard.is_root": "这是库本身，不是照片文件。",
    "metadata.writeback.guard.is_config_dir": "这是 Gallery 自己的配置目录。",
    "metadata.writeback.permission.read": (
        "没有权限读取「{name}」：它或它所在的磁盘可能被设成了只读。"
    ),
    "metadata.writeback.permission.write": (
        "没有权限写入「{name}」：它或它所在的磁盘可能被设成了只读。"
    ),
    "metadata.writeback.disk_full": "磁盘满了，写不进「{name}」：请释放空间后重试。",
    "metadata.writeback.unsupported": "此格式不支持修改「{name}」的元数据：{detail}。",
    "metadata.writeback.no_room": ("「{name}」写入失败：文件结构中没有足够空间存放该字段。"),
    "metadata.writeback.rollback_failed": (
        "写入未生效，且未能恢复原值：{fields}。请检查这张照片：{reason}。"
    ),
    "metadata.writeback.rollback_failed_detail": "{detail}；回滚也失败了：{error}",
    "metadata.writeback.restore_failed": (
        "写入未生效，且原值未能恢复。请打开这张照片核对以下字段：{fields}。本次写入未成功。"
    ),
    "metadata.writeback.restore_failed_detail": (
        "{detail}；回滚之后这些字段仍然不是原值：{fields}"
    ),
    "metadata.writeback.restored": "写入未生效，照片已恢复原样：{fields}。",
    "metadata.writeback.failed.read": "读取「{name}」的元数据失败：{detail}",
    "metadata.writeback.failed.write": "写入「{name}」的元数据失败：{detail}",
    "metadata.writeback.failed_no_detail.read": "读取「{name}」的元数据失败。",
    "metadata.writeback.failed_no_detail.write": "写入「{name}」的元数据失败。",
    "metadata.writeback.no_reason": "exiftool 未提供原因",
    # ---- 批量读取 --------------------------------------------------------------
    "metadata.batch.timeout": "读取元数据超时，超过 {seconds:g} 秒：这一批照片。",
    "metadata.batch.command": "命令：{command} …",
    "metadata.batch.no_payload": "exiftool 没有返回可用信息。",
    "metadata.batch.exit_code": "退出码 {code}",
    "metadata.default_album_name": "新建相册",
    "metadata.duplicate_suffix": "的副本",
}
