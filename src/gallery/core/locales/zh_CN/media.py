"""解码与取帧的错误。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "media.decode.cancelled": "已取消读取：{name}。",
    "media.decode.gone": "无法解码 {name}：文件已不在原处。",
    "media.decode.corrupt": "无法解码 {name}：文件可能已损坏或被截断。",
    "media.decode.bomb": "这张图片的像素数大到不像照片，出于安全考虑没有打开：{name}。",
    "media.decode.unreachable": (
        "无法读取 {name}：文件可能尚未下载完成，例如仍在云盘同步，"
        "也可能正被其他程序占用。请等待下载完成后再打开。"
    ),
    "media.decode.heif_missing": (
        "缺少 HEIC/HEIF 解码支持，无法打开 {name}。安装 pillow-heif 后即可。"
    ),
    "media.decode.unidentified": "这不是一张能识别的图片：{name}。文件内容可能与扩展名不符。",
    "media.video.unsupported": "这台机器上取不了视频画面：{name}。",
    "media.video.unreadable": (
        "无法读取 {name} 的第一帧：系统不支持其编码格式，"
        "或文件尚未下载完成。请等待下载完成后再打开。"
    ),
}
