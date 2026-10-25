"""按 viewkey 从 Pornhub 抓影片元数据（ADR 0083）。

一页就够，但那几栏各在页面上的不同位置：标题与封面在 ``og:`` 标签里，时长与封面另有一份在
``flashvars`` 那段 JSON 里，标签与出演者挂在 ``data-label`` 上，上传者只在那句 ``From:`` 后面。
所以逐项按可靠性取，**取不到就留空** —— 从别处猜一个只会让列表按错的值排序与分组。
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Final

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events.kinds import SOURCE_PORNHUB, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport
from gallery.core.i18n import t

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://www.pornhub.com"

#: 年龄确认的 cookie。**不带它拿回的是确认页**，正文里一个影片字段都没有。
DEFAULT_COOKIE: Final = (
    "age_verified=1; accessAgeDisclaimerPH=1; accessAgeDisclaimerUK=1; accessPH=1; platform=pc"
)

#: viewkey：小写字母与数字。老片是纯数字，新片多半以 ``ph`` 开头。
_ID_RE: Final = re.compile(r"[0-9a-z]{4,32}")

#: 网址里带编号的另外两种写法：``/embed/<id>`` 与 ``/pornstar/<名字>/video/<id>``。
_PATH_RES: Final = (
    re.compile(r"/(?:gay/)?embed/([0-9a-z]+)"),
    re.compile(r"/(?:gay/)?pornstar/[^/]+/video/([0-9a-z]+)"),
)

#: 影片页的域名：``www``、各国子域、``premium``、``.net`` 与 ``.org`` 都认。
_HOST_RE: Final = re.compile(r"(?:^|\.)pornhub(?:premium)?\.(?:com|net|org)$", re.I)

#: 页面上那些 ``<meta>`` 标签与它们的属性。
_META_TAG_RE: Final = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE: Final = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

#: JSON-LD 那一段（``VideoObject`` 里有 ``uploadDate`` 与 ``duration``）。
_JSONLD_RE: Final = re.compile(
    r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", re.S | re.I
)

#: 标签 / 分类 / 出演者都是 ``<a data-label="…">``。
_LABEL_RE: Final = r"""<a\b[^>]*\bdata-label=["']%s["'][^>]*>(.*?)</a>"""

#: 播放器参数那一段：``var flashvars_123 = {…}``。
_FLASHVARS_RE: Final = re.compile(r"var\s+flashvars_\d+\s*=\s*")

#: 标题的另两种写法（``og:`` 与 ``twitter:`` 都没有时）。
_H1_RE: Final = re.compile(r"""<h1\b[^>]*\bclass=["']title["'][^>]*>(.*?)</h1>""", re.S | re.I)

#: 上传者那一句：``From:&nbsp;<a href="/users/…">名字</a>``（也有 ``<span class="username">``）。
_UPLOADER_RE: Final = re.compile(
    r"(?s)From:&nbsp;.*?<(?:a\b[^>]+\bhref=[\"']/(?:(?:user|channel)s|model|pornstar)/"
    r"|span\b[^>]+\bclass=[\"']username)[^>]+>(.+?)<"
)

#: 整页视频资料的另一种来源：``var MODEL_PROFILE = {…}``。
_MODEL_RE: Final = re.compile(r"var\s+MODEL_PROFILE\s*=\s*")

#: 页面说「这个视频不在」的那几种写法（删除、私密、被下架）。
_MISSING_RE: Final = re.compile(
    r"""class=["'][^"']*\b(?:noVideo|removed|userMessageSection)\b""", re.I
)

#: 地域限制页上的标志。
_GEO_MARKS: Final = ("geoBlocked", "This content is unavailable in your country")

#: ISO 8601 的时长（``PT10M15S``）。
_ISO_RE: Final = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def transport() -> HttpTransport:
    """Pornhub 的传输：年龄确认的 cookie，以及英文页面。"""
    return HttpTransport(base=SITE, cookie=DEFAULT_COOKIE, language="en-US,en;q=0.9")


def movie_url(code: str) -> str:
    """影片页的地址。"""
    return f"{SITE}/view_video.php?viewkey={urllib.parse.quote(code)}"


def normalize_code(text: str) -> str:
    """编号归一：去空白、转小写。**viewkey 只有小写字母与数字**。"""
    return (text or "").strip().lower()


def id_from_text(text: str) -> str | None:
    """裸的 viewkey；别的形状就是 ``None``。"""
    code = normalize_code(text)
    return code if _ID_RE.fullmatch(code) else None


def id_from_url(url: str) -> str | None:
    """Pornhub 网址里的 viewkey；作者页与合集页认不出来。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc.split(":")[0]):
        return None
    for pattern in _PATH_RES:
        match = pattern.search(parts.path)
        if match:
            return _code_or_none(match.group(1))
    candidate = (urllib.parse.parse_qs(parts.query).get("viewkey") or [""])[0]
    return _code_or_none(candidate)


def _code_or_none(text: str) -> str | None:
    """认得出形状的那个编号；认不出就是 ``None``。"""
    code = normalize_code(text)
    return code if _ID_RE.fullmatch(code) else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def metas_of(html: str) -> dict[str, str]:
    """页面上那些 ``<meta>`` 的 ``property`` / ``name`` → ``content``。同名只留第一个。"""
    found: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        attrs = {
            name.lower(): quoted if quoted else single
            for name, quoted, single in _ATTR_RE.findall(tag)
        }
        key = attrs.get("property") or attrs.get("name") or ""
        content = attrs.get("content")
        if key and content is not None:
            found.setdefault(key.lower(), _html.unescape(content))
    return found


def _json_after(html: str, mark: re.Pattern[str]) -> Mapping[str, object]:
    """``mark`` 后面那一段 JSON；没有或者读不懂就是空的那一份。"""
    match = mark.search(html)
    if match is None:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except ValueError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def video_object_of(html: str) -> Mapping[str, object]:
    """JSON-LD 里的 ``VideoObject``；没有就是空的那一份。"""
    for block in _JSONLD_RE.findall(html):
        try:
            payload = json.loads(block)
        except ValueError:
            continue
        for item in _objects(payload):
            if str(item.get("@type", "")).lower() == "videoobject":
                return item
    return {}


def _objects(payload: object) -> list[Mapping[str, object]]:
    """JSON-LD 可能是一份、一串，或者裹在 ``@graph`` 里。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    graph = payload.get("@graph")
    if isinstance(graph, list):
        return [payload, *(item for item in graph if isinstance(item, Mapping))]
    return [payload]


def view_of(html: str, code: str) -> MovieInfo:
    """影片页的 HTML → 一份 :class:`MovieInfo`。**页面没给的那几栏留空**。

    :raises MovieImportError: 视频已被删除 / 私密 / 当前地区看不到，或者页面里没有标题。
    """
    _raise_if_unavailable(html, code)
    meta = metas_of(html)
    flash = _json_after(html, _FLASHVARS_RE)
    video = video_object_of(html)
    title = _title_of(html, meta)
    if not title:
        raise MovieImportError(t("event.pornhub.no_info_reason", code=code))
    duration = (
        _seconds(flash.get("video_duration"))
        or _seconds(meta.get("og:duration"))
        or _iso_seconds(video.get("duration"))
    )
    return MovieInfo(
        source=SOURCE_PORNHUB,
        code=code,
        title=title,
        cover_urls=_covers(flash.get("image_url"), meta.get("og:image"), video.get("thumbnailUrl")),
        page_url=movie_url(code),
        release_date=_day(meta.get("og:video:release_date") or _text_of(video.get("uploadDate"))),
        duration_minutes=minutes_of_seconds(duration),
        creator=_creator_of(html, video),
        actors=_labeled(html, "pornstar"),
        tags=_unique((*_labeled(html, "tag"), *_labeled(html, "category"))),
    )


def _raise_if_unavailable(html: str, code: str) -> None:
    """页面自己说了「不在」的时候给一句中文，别让用户去查编号。"""
    if any(mark in html for mark in _GEO_MARKS):
        raise MovieImportError(t("event.pornhub.geo_blocked", code=code))
    if _MISSING_RE.search(html):
        raise MovieImportError(t("event.pornhub.unavailable", code=code))


def _title_of(html: str, meta: Mapping[str, str]) -> str:
    """标题：``og:`` → ``twitter:`` → 页面那个 ``<h1 class="title">``。"""
    for candidate in (meta.get("og:title"), meta.get("twitter:title")):
        if candidate and candidate.strip():
            return candidate.strip()
    match = _H1_RE.search(html)
    return _text_of(match.group(1)) if match else ""


def _creator_of(html: str, video: Mapping[str, object]) -> str:
    """上传者：那一句 ``From:`` 后面的名字 → ``MODEL_PROFILE`` → JSON-LD 的 ``author``。"""
    match = _UPLOADER_RE.search(html)
    name = _text_of(match.group(1)) if match else ""
    if name:
        return name
    model = _json_after(html, _MODEL_RE)
    if _text_of(model.get("username")):
        return _text_of(model.get("username"))
    author = video.get("author")
    if isinstance(author, Mapping):
        return _text_of(author.get("name"))
    return _text_of(author)


def _covers(*candidates: object) -> tuple[str, ...]:
    """封面地址：第一个能用的就够了，**补成绝对的 ``https``**。"""
    for candidate in candidates:
        url = _first_url(candidate)
        if url:
            return (url,)
    return ()


def _first_url(value: object) -> str:
    """JSON-LD 的 ``thumbnailUrl`` 可能是字符串或一串，取第一个。"""
    if isinstance(value, list):
        value = value[0] if value else ""
    url = _text_of(value)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _labeled(html: str, label: str) -> tuple[str, ...]:
    """``data-label="tag"`` 那几格的名字（标签、分类、出演者都走它）。"""
    pattern = re.compile(_LABEL_RE % re.escape(label), re.S | re.I)
    return _unique(_text_of(item) for item in pattern.findall(html))


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    """去空、去重，保持页面上的顺序。"""
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(item, None)
    return tuple(seen)


def _day(stamp: object) -> str:
    """``2013-06-28`` 或 ``2013-06-28T…`` 取那一天；认不出的不猜。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", _text_of(stamp))
    return match.group(1) if match else ""


def _seconds(value: object) -> int | None:
    """秒数；不是数字就是 ``None``。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _iso_seconds(value: object) -> int | None:
    """ISO 8601 的时长（``PT10M15S``）→ 秒；认不出就是 ``None``。"""
    match = _ISO_RE.fullmatch(_text_of(value))
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _text_of(value: object) -> str:
    """一段 HTML / JSON 里的文字：去掉标签、解开实体、压掉多余空白。"""
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 viewkey 抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、视频不可见、网络失败。
    """
    normalized = normalize_code(code)
    if not _ID_RE.fullmatch(normalized):
        raise MovieImportError(t("event.pornhub.invalid_code", code=code))
    try:
        html = client.get_text(movie_url(normalized))
    except SourcePageError as exc:
        if exc.status == 404:
            raise MovieImportError(t("event.pornhub.not_found", code=normalized)) from exc
        raise
    return view_of(html, normalized)


__all__ = [
    "DEFAULT_COOKIE",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "metas_of",
    "movie_url",
    "normalize_code",
    "transport",
    "video_object_of",
    "view_of",
]
