"""按 vid 从芒果 TV 抓影片元数据（ADR 0082）。

一个 JSON 请求就够：``pcweb.api.mgtv.com/video/info`` 给标题、封面、时长与所属剧集。
**没有发布日期** —— 那个接口不给，所以这一栏留空（ADR 0082 决策 4）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from typing import Final

from gallery.core.errors import MovieImportError
from gallery.core.events.kinds import SOURCE_MANGO, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://www.mgtv.com"

#: 取元数据的接口域名。
API: Final = "https://pcweb.api.mgtv.com"

#: vid：纯数字（``14544857``）。
_VID_RE: Final = re.compile(r"\d{6,10}")

#: 影片页地址里的 vid：``/h/<vid>.html`` 与 ``/b/<合集>/<vid>.html`` 两种。
_PATH_RES: Final = (
    re.compile(r"/h/(\d{6,10})\.html"),
    re.compile(r"/b/\d+/(\d{6,10})\.html"),
)

#: 影片页的域名（``m`` 也认）。
_HOST_RE: Final = re.compile(r"(?:^|\.)mgtv\.com$", re.I)

#: 接口自己的错误码 → 一句中文。
_PROBLEMS: Final[dict[int, str]] = {
    10008: "芒果 TV 上没有这个视频",
}


def transport() -> HttpTransport:
    """芒果 TV 的传输。"""
    return HttpTransport(base=SITE)


def movie_url(code: str) -> str:
    """影片页的地址。``/h/`` 那一档只凭 vid 就能打开。"""
    return f"{SITE}/h/{code}.html"


def id_from_text(text: str) -> str | None:
    """裸的 vid（纯数字）；别的形状就是 ``None``。"""
    stripped = text.strip()
    return stripped if _VID_RE.fullmatch(stripped) else None


def id_from_url(url: str) -> str | None:
    """芒果 TV 网址里的 vid。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc.split(":")[0]):
        return None
    for pattern in _PATH_RES:
        match = pattern.search(parts.path)
        if match:
            return match.group(1)
    return None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def problem_of(payload: Mapping[str, object], code: str) -> MovieImportError:
    """接口报错时给用户的那一句。**HTTP 也是 200，错在 JSON 的 ``code`` 里**。"""
    status = _int(payload.get("code"))
    known = _PROBLEMS.get(status) if status is not None else None
    if known is not None:
        return MovieImportError(f"{known}：{code}。")
    message = _text(payload.get("msg")) or _text(payload.get("err_msg"))
    tail = f"，{message}" if message else ""
    return MovieImportError(f"芒果 TV 没给出这个视频的资料：错误码 {status}{tail}。")


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``video/info`` 的那份 JSON → 一份 :class:`MovieInfo`。

    :raises MovieImportError: 接口自己报的错，或者响应里没有影片那一段。
    """
    if _int(payload.get("code")) != 200:
        raise problem_of(payload, code)
    data = payload.get("data")
    info = data.get("info") if isinstance(data, Mapping) else None
    if not isinstance(info, Mapping):
        raise MovieImportError(f"芒果 TV 没给出这个视频的资料：{code}。")
    title = _text(info.get("title")) or _text(info.get("videoName"))
    if not title:
        raise MovieImportError(f"芒果 TV 没给出这个视频的资料：{code}。")
    return MovieInfo(
        source=SOURCE_MANGO,
        code=code,
        title=title,
        cover_urls=_covers(info.get("videoImage") or info.get("clipImage")),
        page_url=movie_url(code),
        duration_minutes=minutes_of_seconds(_seconds(info.get("time"))),
        series=_text(info.get("plName")),
    )


def _covers(picture: object) -> tuple[str, ...]:
    """封面地址。**芒果的图床地址末尾带一个 ``?``**，原样留着（去掉它反而是另一个地址）。"""
    url = _text(picture)
    if not url:
        return ()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return (url,)


def _seconds(value: object) -> int | None:
    """时长写成 ``mm:ss`` 或 ``hh:mm:ss``，**不是秒数**。"""
    text = _text(value)
    if not text:
        return None
    parts = text.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _int(value: object) -> int | None:
    """JSON 里的一个数字；不是数字就是 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _text(value: object) -> str:
    """JSON 里的一格文字；不是文字就是空串。"""
    return "" if value is None else str(value).strip()


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 vid 抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、视频不存在、网络失败。
    """
    if not _VID_RE.fullmatch(code):
        raise MovieImportError(f"这不是一个芒果 TV 的 vid：{code}。")
    return view_of(_json(client.get_text(_info_url(code))), code)


def _info_url(code: str) -> str:
    return f"{API}/video/info?vid={urllib.parse.quote(code)}"


def _json(raw: str) -> Mapping[str, object]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MovieImportError("芒果 TV 给出的资料读不懂。") from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError("芒果 TV 给出的资料不是一份视频信息。")
    return payload


__all__ = [
    "API",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "movie_url",
    "problem_of",
    "transport",
    "view_of",
]
