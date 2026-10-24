"""按视频编号从优酷抓影片元数据（ADR 0082）。

一页就够：页面里 ``__INITIAL_DATA__`` 那一段 JSON 带着标题、封面、时长、发布时间与分类。
**不走开放平台** —— 那条要 ``client_id``，等于把别人的应用凭据打进包里。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from typing import Final

from gallery.core.errors import MovieImportError
from gallery.core.events.kinds import SOURCE_YOUKU, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://v.youku.com"

#: 编号：``X`` 开头的一串（``XNjQ3MjkwNTUwNA``）。同一条视频有带尾部 ``=`` 与不带两种写法。
_ID_RE: Final = re.compile(r"X[0-9A-Za-z]{6,40}")

#: 影片页地址里的编号。**地址里那串可能带尾部 ``=``**，归一的时候去掉。
_PATH_RE: Final = re.compile(r"/(?:v_show|video)/id_(X[0-9A-Za-z=]+)\.html")

#: 影片页的域名（``www`` / ``m`` 都认）。
_HOST_RE: Final = re.compile(r"(?:^|\.)youku\.com$", re.I)

#: 服务端渲染的那一段状态。**它的形状是 ``window.__INITIAL_DATA__ ={…}``**（等号后面直接是花括号）。
_DATA_MARK: Final = "__INITIAL_DATA__"

#: 风控页上的那一段路径。它一出现就说明这一页不是影片页。
_CHALLENGE: Final = "_____tmd_____"


def transport() -> HttpTransport:
    """优酷的传输。"""
    return HttpTransport(base=SITE)


def movie_url(code: str) -> str:
    """影片页的地址。**编号不带尾部 ``=``**（带不带都能打开，统一成不带的那种）。"""
    return f"{SITE}/v_show/id_{code}.html"


def normalize_code(text: str) -> str:
    """把用户敲的编号归一：去空白、去尾部 ``=``。**大小写敏感**（那一串本身就是大小写混的）。"""
    return text.strip().rstrip("=")


def id_from_text(text: str) -> str | None:
    """裸的编号；别的形状就是 ``None``。"""
    code = normalize_code(text)
    return code if _ID_RE.fullmatch(code) else None


def id_from_url(url: str) -> str | None:
    """优酷网址里的编号；短链与个人页认不出来。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc.split(":")[0]):
        return None
    match = _PATH_RE.search(parts.path)
    return normalize_code(match.group(1)) if match else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def payload_of(html: str) -> Mapping[str, object] | None:
    """页面里 ``__INITIAL_DATA__`` 后面那一段 JSON；没有就是 ``None``。"""
    at = html.find(_DATA_MARK)
    if at < 0:
        return None
    start = html.find("{", at)
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:])
    except ValueError:
        return None
    return payload if isinstance(payload, Mapping) else None


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``__INITIAL_DATA__`` 的那份 JSON → 一份 :class:`MovieInfo`。

    :raises MovieImportError: 响应里没有影片那一段（改版了，或者这不是影片页）。
    """
    page = payload.get("pageMap")
    extra = page.get("extra") if isinstance(page, Mapping) else None
    if not isinstance(extra, Mapping):
        raise MovieImportError(
            f"优酷这一页没给出影片信息：{code}。站方可能改了页面结构，用浏览器打开看看吧。"
        )
    title = _text(extra.get("videoTitle"))
    if not title:
        raise MovieImportError(f"优酷这一页没给出影片信息：{code}。")
    category = _text(extra.get("videoCategory"))
    show = _text(extra.get("showName"))
    return MovieInfo(
        source=SOURCE_YOUKU,
        code=code,
        title=title,
        cover_urls=_covers(extra.get("videoImg")),
        page_url=movie_url(code),
        release_date=_day(extra.get("videoPublishTime")),
        duration_minutes=minutes_of_seconds(_seconds(extra.get("duration"))),
        series=show,
        tags=(category,) if category else (),
    )


def _covers(picture: object) -> tuple[str, ...]:
    """封面地址：补成绝对的 ``https``。**它只有 448×252**（优酷这一档就是这么大）。"""
    url = _text(picture)
    if not url:
        return ()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return (url,)


def _day(stamp: object) -> str:
    """``2025-04-02 18:01:57`` 取前 10 位；认不出的不猜。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", _text(stamp))
    return match.group(1) if match else ""


def _seconds(value: object) -> int | None:
    """时长写的是秒（``70.56`` 这种浮点）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _text(value: object) -> str:
    """JSON 里的一格文字；不是文字就是空串。"""
    return "" if value is None else str(value).strip()


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def is_challenge(html: str) -> bool:
    """这一页是不是风控给的那个 JS 跳转页。"""
    return _CHALLENGE in html


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按编号抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、被风控挡住、页面结构变了、网络失败。
    """
    if not _ID_RE.fullmatch(code):
        raise MovieImportError(f"这不是一个优酷的编号：{code}。")
    html = client.get_text(movie_url(code))
    if is_challenge(html):
        # 风控页也是 200、正文为空 —— 说成「没有这个视频」会让人去换编号。
        raise MovieImportError(
            f"优酷这次的风控没放行：{code}。过一会儿再试，或者先在浏览器里打开它。"
        )
    payload = payload_of(html)
    if payload is None:
        raise MovieImportError(
            f"优酷这一页没给出影片信息：{code}。站方可能改了页面结构，用浏览器打开看看吧。"
        )
    return view_of(payload, code)


__all__ = [
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "is_challenge",
    "movie_url",
    "normalize_code",
    "payload_of",
    "transport",
    "view_of",
]
