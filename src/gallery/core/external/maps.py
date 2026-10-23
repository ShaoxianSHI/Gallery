"""地图链接：把一张照片的坐标翻译成「地图」或 Google 地图能打开的地址。

**macOS 上只有系统自带的「地图」认坐标**：高德地图的 Mac 版没有注册任何 URL scheme
（iOS 那个 ``iosamap://`` 在 macOS 上直接报找不到应用），百度 / 腾讯也没有 Mac 端点。
坐标是 EXIF 里的 WGS-84；``ll`` 把地图中心挪过去，``q`` 才落下一个标记点。
"""

from __future__ import annotations

#: Apple 地图注册的 URL scheme（bundle id ``com.apple.Maps``）。
APPLE_SCHEME = "maps://"

#: Google 地图的站点。**桌面 PWA 装在 ``www.google.com`` 上**，不是 ``maps.google.com``。
MAPS_HOST = "www.google.com"

#: Google 地图在站点里的路径前缀：PWA 的启动地址与应用窗口都从 ``/maps`` 开始。
#: **认包与认窗口都要带上它** —— 只按 host 会把同一域名下别的 Google 应用认成地图。
MAPS_PATH = "/maps"

#: Google 官方那份跨平台链接格式（``query`` 里直接给坐标）。
GOOGLE_BASE = "https://www.google.com/maps/search/?api=1&query="

#: 坐标写进 URL 时保留几位小数。六位约 0.1 米，比任何相机的 GPS 都细。
_DECIMALS = 6


def _coordinate_text(latitude: float, longitude: float) -> tuple[str, str]:
    """校验并格式化坐标；出了地球就抛 :class:`ValueError`（调用方先挡过一遍）。"""
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"纬度要在 -90 到 90 之间，收到 {latitude!r}")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"经度要在 -180 到 180 之间，收到 {longitude!r}")
    return f"{latitude:.{_DECIMALS}f}", f"{longitude:.{_DECIMALS}f}"


def apple_deep_link(latitude: float, longitude: float) -> str:
    """系统「地图」的坐标链接。

    **``q`` 不能省**：只给 ``ll`` 是把地图中心挪过去，用户看到的是「那附近」；
    ``q`` 给同一个坐标才在那里落一个标记点，地图上能直接读出「就是这里」。
    """
    lat, lon = _coordinate_text(latitude, longitude)
    return f"{APPLE_SCHEME}?ll={lat},{lon}&q={lat},{lon}"


def google_web_link(latitude: float, longitude: float) -> str:
    """Google 地图的网页地址。**Mac 上没有它的应用**，这一条永远开在默认浏览器里。"""
    lat, lon = _coordinate_text(latitude, longitude)
    return f"{GOOGLE_BASE}{lat},{lon}"


__all__ = [
    "APPLE_SCHEME",
    "GOOGLE_BASE",
    "MAPS_HOST",
    "MAPS_PATH",
    "apple_deep_link",
    "google_web_link",
]
