"""按 videoId 从 YouTube 抓影片元数据（ADR 0068）。**只依赖标准库**：不引 yt-dlp（它连 YouTube
的 JS 挑战都要另配一个 Deno 运行时）。

**两步走**（ADR 0068 决策 4）：官方 oEmbed（免 key、免配额）给标题、频道与封面，这三样必须拿到；
时长、发布日期与标签再去读一眼 watch 页补上 —— 那一刀抓的是 HTML，站方改版或反爬挡住都会让它
空手而归，所以它失败只记一条日志，不让整次导入失败。
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events.kinds import SOURCE_YOUTUBE, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport
from gallery.core.i18n import t

logger = logging.getLogger(__name__)

BASE: Final = "https://www.youtube.com"

#: 官方的预览端点：**免 key、免配额**，YouTube 自己给嵌入用的那一份资料。
OEMBED: Final = "https://www.youtube.com/oembed"

#: 缩略图的地址形状。
_THUMBNAIL: Final = "https://i.ytimg.com/vi/{code}/{name}.jpg"

#: 认这几个域名（``www.`` 会先去掉）。
_HOSTS: Final = frozenset({"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"})

#: videoId：11 位 base64url 字符。**大小写敏感**。
_ID_RE: Final = re.compile(r"[A-Za-z0-9_-]{11}")

#: 路径里带 id 的那几种写法（``/shorts/<id>``、``/embed/<id>``、``/live/<id>``、``/v/<id>``）。
_PATH_RE: Final = re.compile(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)")

#: 封面按这个顺序试（ADR 0068 决策 6）：前两个是 16:9 的大图，后两个是小图回退。
#: **``hqdefault`` 不在里面** —— 它是 4:3、上下带黑边的，只在 oEmbed 给的那一个地址上作最后一档。
_THUMBNAIL_NAMES: Final = ("maxresdefault", "hq720", "sddefault", "mqdefault")

#: watch 页里那三样。**每一项单独取**：页面上少一样不该让另外两样也没了。
_STRING: Final = r'"((?:[^"\\]|\\.)*)"'
_LENGTH_RE: Final = re.compile(r'"lengthSeconds":"(\d+)"')
#: 页面上那串**自带上传者的时区偏移**（``2009-10-24T23:57:33-07:00``），不是光秃秃的日期。
#: 两个字段名都认：``publishDate`` 与 ``uploadDate`` 是同一份时间，站方两边都写过。
_DATE_RE: Final = re.compile(r'"(?:publishDate|uploadDate)":"([^"]+)"')
_CHANNEL_RE: Final = re.compile(r'"ownerChannelName":' + _STRING)
_KEYWORDS_RE: Final = re.compile(r'"keywords":\[(.*?)\]', re.S)
_QUOTED_RE: Final = re.compile(_STRING)


def transport() -> HttpTransport:
    """YouTube 的传输。"""
    return HttpTransport(base=BASE)


def movie_url(code: str) -> str:
    """视频页的地址。"""
    return f"{BASE}/watch?v={code}"


def is_video_id(text: str) -> bool:
    """这一串是不是 videoId 的形状（11 位）。**认形状不认大小写** —— 它本来就大小写敏感。"""
    return _ID_RE.fullmatch(text.strip()) is not None


def id_from_url(url: str) -> str | None:
    """YouTube 网址里的 videoId；不是 YouTube 或者认不出就是 ``None``。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = parts.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[len("www.") :]
    if host not in _HOSTS:
        return None
    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
    else:
        candidate = (urllib.parse.parse_qs(parts.query).get("v") or [""])[0]
        if not candidate:
            match = _PATH_RE.match(parts.path)
            candidate = match.group(1) if match else ""
    return candidate if is_video_id(candidate) else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def oembed_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """oEmbed 的那份 JSON → 一份 :class:`MovieInfo`。

    **没有时长与发布日期**：那两个不在 oEmbed 里（那是 watch 页那一刀的事）。
    """
    return MovieInfo(
        source=SOURCE_YOUTUBE,
        code=code,
        title=_text(payload.get("title")),
        cover_urls=_covers(code, _text(payload.get("thumbnail_url"))),
        page_url=movie_url(code),
        creator=_text(payload.get("author_name")),
    )


def with_page(html: str, movie: MovieInfo) -> MovieInfo:
    """watch 页里的时长、发布日期与标签补进 oEmbed 那份资料。**页面上少一样就少一样**。"""
    channel = movie.creator or _unquote(_first(_CHANNEL_RE, html))
    return replace(
        movie,
        release_date=_day(_first(_DATE_RE, html)),
        duration_minutes=minutes_of_seconds(_int(_first(_LENGTH_RE, html))),
        creator=channel,
        tags=_keywords(html),
    )


def _day(stamp: str) -> str:
    """``publishDate`` → ``YYYY-MM-DD``，**取 UTC 那一天**。

    页面上那串自带上传者的时区偏移（``2009-10-24T23:57:33-07:00``）—— 照原样取前 10 位会得到
    24 日，而官方 Data API 的 ``publishedAt`` 是 UTC（那支视频在 UTC 是 25 日，也是它到处被
    引用的日期）。**没带时区的或认不出的都不猜**：前者它自己写的就是哪一天，后者退回前 10 位。
    """
    if not stamp:
        return ""
    try:
        moment = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp[:10]
    if moment.tzinfo is None:
        # 没带时区时**不许拿本机时区去换算** —— 换一台机器就换一天。
        return moment.date().isoformat()
    return moment.astimezone(datetime.UTC).date().isoformat()


def _covers(code: str, fallback: str) -> tuple[str, ...]:
    """封面候选，按顺序试。oEmbed 给的那一个放在最后（它是 ``hqdefault``，带黑边但一定有）。"""
    names = tuple(_THUMBNAIL.format(code=code, name=name) for name in _THUMBNAIL_NAMES)
    return names if not fallback or fallback in names else (*names, fallback)


def _keywords(html: str) -> tuple[str, ...]:
    """watch 页里那一串 ``keywords``。**没有就是空的**（那是作者填的，不是每个视频都有）。"""
    block = _KEYWORDS_RE.search(html)
    if not block:
        return ()
    return tuple(_unquote(match.group(1)) for match in _QUOTED_RE.finditer(block.group(1)))


def _first(pattern: re.Pattern[str], html: str) -> str:
    match = pattern.search(html)
    return match.group(1) if match else ""


def _unquote(raw: str) -> str:
    """页面上那一串是 JSON 里的一段，**要解码一次**（频道名里可能有引号或 ``\\u0026``）。"""
    if not raw:
        return ""
    try:
        return str(json.loads(f'"{raw}"')).strip()
    except ValueError:
        return raw.strip()


def _text(value: object) -> str:
    """JSON 里的一格文字；不是文字就是空串。"""
    return "" if value is None else str(value).strip()


def _int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def _oembed_problem(exc: SourcePageError, code: str) -> MovieImportError:
    """oEmbed 那几种状态各说各的：**401 / 403 是不让嵌入，404 是没这个视频**。

    混成一句「取不到」会把用户支到错的下一步（换一个视频 vs 换一个工具）。
    """
    if exc.status in (401, 403):
        return MovieImportError(t("event.youtube.embed_forbidden", code=code))
    if exc.status == 404:
        return MovieImportError(t("event.youtube.not_found", code=code))
    return MovieImportError(t("event.youtube.http", status=exc.status, code=code))


def _oembed_url(code: str) -> str:
    watch = urllib.parse.quote(movie_url(code), safe="")
    return f"{OEMBED}?url={watch}&format=json"


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 videoId 抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: oEmbed 不给资料（视频没了 / 不允许嵌入 / 网络失败）。
    """
    if not is_video_id(code):
        raise MovieImportError(t("event.youtube.invalid_code", code=code))
    try:
        raw = client.get_text(_oembed_url(code))
    except SourcePageError as exc:
        raise _oembed_problem(exc, code) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MovieImportError(t("event.youtube.unparsable", code=code)) from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError(t("event.youtube.invalid_payload", code=code))
    return _with_page(code, oembed_of(payload, code), client)


def _with_page(code: str, movie: MovieInfo, client: Transport) -> MovieInfo:
    """再去读一眼 watch 页，把时长 / 发布日期 / 标签补上。

    **这一刀是补的**（ADR 0068 决策 4）：它抓的是 HTML，站方改版或反爬挡住（``Sign in to
    confirm you're not a bot``）都会让它空手而归，而那时标题与封面已经拿到了 —— 所以这里
    吞掉失败，只记一条日志。
    """
    try:
        html = client.get_text(movie_url(code))
    except MovieImportError as exc:
        logger.info("YouTube 这次没给出时长与发布日期（%s）：%s", code, exc.message)
        return movie
    return with_page(html, movie)


__all__ = [
    "BASE",
    "OEMBED",
    "fetch",
    "id_from_url",
    "is_video_id",
    "movie_url",
    "oembed_of",
    "transport",
    "with_page",
]
