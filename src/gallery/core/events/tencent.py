"""按 vid 从腾讯视频抓影片元数据（ADR 0082）。

两条路各管一半：``getinfo`` 是 JSON，给标题与时长；移动分享页的 ``itemprop`` 给日期与标签。
封面由 vid 直接拼出来（``vpic_cover``）—— ``qqvideo_ori`` 那一套会给 120×120 的占位图。

``getinfo`` 是播放器接口，拿它取元数据属于用它的另一面：站方收紧时最先动的就是它。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from gallery.core.errors import MovieImportError
from gallery.core.events.kinds import SOURCE_TENCENT, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport
from gallery.core.i18n import t

logger = logging.getLogger(__name__)

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://v.qq.com"

#: 播放器接口的域名。
API: Final = "https://vv.video.qq.com"

#: 移动分享页。**它才是服务端渲染的那一版**，桌面页里一个字段都没有。
MOBILE: Final = "https://m.v.qq.com"

#: 封面。``/496`` 换成 ``/1280`` 就是 1280×720。
_COVER: Final = "https://puui.qpic.cn/vpic_cover/{code}/{code}_hz.jpg/1280"

#: ``getinfo`` 的响应裹着一层 JS 赋值，不是干净的 JSON。
_WRAPPER: Final = "QZOutputJson="

#: vid：``s00242sxrne`` 这种形状 —— 11 位小写字母数字，**首位是字母**。
_VID_RE: Final = re.compile(r"[a-z][a-z0-9]{10}")

#: 影片页的域名（``v`` / ``m`` / ``film`` 三个子域都认）。
_HOST_RE: Final = re.compile(r"(?:^|\.)(?:v|m|film)\.qq\.com$", re.I)

#: 页面地址里的 vid：``/x/cover/<合集>/<vid>.html`` 与 ``/x/page/<vid>.html``。
_PATH_RES: Final = (
    re.compile(r"/x/cover/[A-Za-z0-9]+/([a-z][a-z0-9]{10})(?:\.html)?$"),
    re.compile(r"/x/page/([a-z][a-z0-9]{10})(?:\.html)?$"),
)

#: 接口自己的错误码 → 一句中文。**HTTP 永远 200，错在 JSON 里**。
_PROBLEMS: Final[dict[int, str]] = {
    61: "event.tencent.problem.not_found",
    62: "event.tencent.problem.under_review",
}

#: 移动页里那些 ``itemprop`` 标签。
_META_RE: Final = re.compile(r'<meta\s+itemprop="(\w+)"[^>]*?content="([^"]*)"', re.I)


def transport() -> HttpTransport:
    """腾讯视频的传输。"""
    return HttpTransport(base=SITE)


def movie_url(code: str) -> str:
    """影片页的地址。**知道合集 id 时也能用 ``/x/cover/<cid>/<vid>.html``**，但只凭 vid 就够。"""
    return f"{SITE}/x/page/{code}.html"


def is_vid(text: str) -> bool:
    """这一串是不是 vid 的形状。**大小写敏感**：vid 只有小写。"""
    return _VID_RE.fullmatch(text.strip()) is not None


def id_from_text(text: str) -> str | None:
    """裸的 vid；别的形状就是 ``None``。"""
    stripped = text.strip()
    return stripped if is_vid(stripped) else None


def id_from_url(url: str) -> str | None:
    """腾讯视频网址里的 vid；合集页（没有具体某一集）认不出来。"""
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
    """接口报错时给用户的那一句。**认不出的码原样带上**（用户报问题时能照着搜）。

    ``em`` 是 0 之外的任何一个数都算错，``msg`` 是站方给的那句话（多半已经说清了原因）。
    """
    status = _int(payload.get("em"))
    known = t(_PROBLEMS[status]) if status is not None and status in _PROBLEMS else None
    if known is not None:
        return MovieImportError(t("event.tencent.problem_known", reason=known, code=code))
    message = _text(payload.get("msg"))
    if message:
        return MovieImportError(
            t("event.tencent.problem_unknown_message", status=status, message=message)
        )
    return MovieImportError(t("event.tencent.problem_unknown", status=status))


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``getinfo`` 的那份 JSON → 一份 :class:`MovieInfo`。**日期与标签不在这里**。

    :raises MovieImportError: 接口自己报的错，或者响应里没有影片那一段。
    """
    if _int(payload.get("em")) != 0:
        raise problem_of(payload, code)
    first = _first_video(payload)
    if first is None:
        raise MovieImportError(t("event.tencent.no_info", code=code))
    vid = _text(first.get("vid")) or code
    return MovieInfo(
        source=SOURCE_TENCENT,
        code=vid,
        title=_text(first.get("ti")),
        cover_urls=(_COVER.format(code=vid),),
        page_url=movie_url(vid),
        duration_minutes=minutes_of_seconds(_seconds(first.get("td"))),
    )


def page_of(html: str, movie: MovieInfo) -> MovieInfo:
    """移动分享页的 ``itemprop`` 标签补上日期与标签。**页面上少一样就少一样**。"""
    meta = _metas(html)
    return replace(
        movie,
        release_date=_day(meta.get("datePublished") or meta.get("uploadDate") or ""),
        tags=_tags(meta.get("keywords") or ""),
    )


def _first_video(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    block = payload.get("vl")
    if not isinstance(block, Mapping):
        return None
    items = block.get("vi")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    return first if isinstance(first, Mapping) else None


def _metas(html: str) -> dict[str, str]:
    """``itemprop`` → 内容。同名只留第一个。"""
    found: dict[str, str] = {}
    for key, value in _META_RE.findall(html):
        found.setdefault(key, value)
    return found


def _day(stamp: str) -> str:
    """``2026-10-10`` 取前 10 位；认不出的不猜。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", stamp.strip())
    return match.group(1) if match else ""


def _tags(raw: str) -> tuple[str, ...]:
    """移动页的 ``keywords`` 是逗号分隔的一串。"""
    return tuple(part.strip() for part in re.split(r"[,，、]", raw) if part.strip())


def _seconds(value: object) -> int | None:
    """``td`` 给的是秒，但写成字符串（``"82.8817"``）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


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


def _json(raw: str) -> Mapping[str, object]:
    """剥掉 ``QZOutputJson=`` 那层壳再解析。"""
    body = raw.strip()
    if body.startswith(_WRAPPER):
        body = body[len(_WRAPPER) :]
    body = body.rstrip(";").strip()
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise MovieImportError(t("event.tencent.unparsable")) from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError(t("event.tencent.invalid_payload"))
    return payload


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 vid 抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、视频不可见、网络失败。
    """
    if not is_vid(code):
        raise MovieImportError(t("event.tencent.invalid_code", code=code))
    movie = view_of(_json(client.get_text(_info_url(code))), code)
    return _with_page(code, movie, client)


def _info_url(code: str) -> str:
    query = urllib.parse.urlencode(
        {"vids": code, "platform": "101001", "charge": "0", "otype": "json"}
    )
    return f"{API}/getinfo?{query}"


def _with_page(code: str, movie: MovieInfo, client: Transport) -> MovieInfo:
    """再读一眼移动分享页，把日期与标签补上。

    **这一刀是补的**：它抓的是 HTML，站方改版就断；那时标题与封面已经拿到了，所以这里吞掉失败，
    只记一条日志（与 YouTube 那一支同一个取向）。
    """
    try:
        html = client.get_text(f"{MOBILE}/play.html?vid={urllib.parse.quote(code)}")
    except MovieImportError as exc:
        logger.info("腾讯视频这次没给出日期与标签（%s）：%s", code, exc.message)
        return movie
    return page_of(html, movie)


__all__ = [
    "API",
    "MOBILE",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "is_vid",
    "movie_url",
    "page_of",
    "problem_of",
    "transport",
    "view_of",
]
