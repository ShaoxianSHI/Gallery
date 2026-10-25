"""按视频 id 从 TikTok 抓影片元数据（ADR 0085）。

oEmbed 免 key、免配额，一次给出标题、作者与封面，也替短链把编号解出来 —— 分享链接
（``vm.tiktok.com/…``、``tiktok.com/t/…``）里只有 token，没有别的离线办法换到编号。
时长、发布日期与话题再去读一眼网页补上；那一刀失败只记日志，不拖垮整次导入。
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
from gallery.core.events.kinds import SOURCE_TIKTOK, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport
from gallery.core.i18n import t

logger = logging.getLogger(__name__)

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://www.tiktok.com"

#: 官方的预览端点：免 key、免配额。
OEMBED: Final = "https://www.tiktok.com/oembed"

#: 视频 id：一串数字（现在都是 19 位）。
_ID_RE: Final = re.compile(r"\d{15,25}")

#: 分享链接里的那一截 token。
_SHARE_RE: Final = re.compile(r"[A-Za-z0-9_-]{5,32}")

#: 网址里带编号的几种写法：``/@user/video/<id>``、``/embed/v2/<id>``、``/player/v1/<id>``。
_PATH_RES: Final = (
    re.compile(r"/@[^/]+/(?:video|photo)/(\d{15,25})"),
    re.compile(r"/(?:embed(?:/v2)?|player/v1|v)/(\d{15,25})(?:[/.?]|$)"),
)

#: 认这个域名（``www`` / ``m`` 与 ``vm`` / ``vt`` 子域都算）。
_HOST_RE: Final = re.compile(r"(?:^|\.)tiktok\.com$", re.I)

#: 网页版那段服务端渲染的数据。
_UNIVERSAL_RE: Final = re.compile(
    r"""<script[^>]+id=["']__UNIVERSAL_DATA_FOR_REHYDRATION__["'][^>]*>(.*?)</script>""",
    re.S | re.I,
)

#: 拿不到作者主页时用的占位 handle：TikTok 会把它跳到真正的那一个。
_PLACEHOLDER_HANDLE: Final = "@i"


def transport() -> HttpTransport:
    """TikTok 的传输。"""
    return HttpTransport(base=SITE)


def movie_url(code: str) -> str:
    """视频页的地址。**``@i`` 是占位 handle**，打开时会跳到作者真正的那一页。"""
    return f"{SITE}/{_PLACEHOLDER_HANDLE}/video/{code}"


def normalize_code(text: str) -> str:
    """编号归一：去空白与末尾的斜杠。"""
    return (text or "").strip().rstrip("/")


def is_video_id(text: str) -> bool:
    """这一串是不是视频 id 的形状。"""
    return _ID_RE.fullmatch(text.strip()) is not None


def id_from_text(text: str) -> str | None:
    """裸的视频 id；别的形状就是 ``None``。"""
    code = normalize_code(text)
    return code if is_video_id(code) else None


def id_from_url(url: str) -> str | None:
    """TikTok 网址里的视频 id；短链返回那一截 token，展开交给 :func:`fetch`。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = parts.netloc.lower().split(":")[0]
    if not _HOST_RE.search(host):
        return None
    for pattern in _PATH_RES:
        match = pattern.search(parts.path)
        if match:
            return match.group(1)
    if host.startswith(("vm.", "vt.")) or parts.path.startswith("/t/"):
        segments = [part for part in parts.path.split("/") if part]
        token = segments[-1] if segments else ""
        return token if _SHARE_RE.fullmatch(token) else None
    return None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def oembed_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """oEmbed 的那份 JSON → 一份 :class:`MovieInfo`。**没有时长与发布日期**。"""
    return MovieInfo(
        source=SOURCE_TIKTOK,
        code=code,
        title=_text(payload.get("title")),
        cover_urls=_covers(payload.get("thumbnail_url")),
        page_url=movie_url(code),
        creator=_text(payload.get("author_name")),
    )


def item_of(html: str) -> Mapping[str, object]:
    """网页里那一条视频；认不出就是空的那一份。"""
    match = _UNIVERSAL_RE.search(html)
    if match is None:
        return {}
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    scope = payload.get("__DEFAULT_SCOPE__")
    if not isinstance(scope, Mapping):
        return {}
    detail = scope.get("webapp.video-detail")
    if not isinstance(detail, Mapping):
        return {}
    info = detail.get("itemInfo")
    if not isinstance(info, Mapping):
        return {}
    item = info.get("itemStruct")
    return item if isinstance(item, Mapping) else {}


def with_item(movie: MovieInfo, item: Mapping[str, object]) -> MovieInfo:
    """网页里那一条视频补进 oEmbed 那份资料。**页面上少一样就少一样**。

    **标题与封面也要从这里补**：oEmbed 有时只回一个 ``embed_product_id``（短链与裸编号都
    见过），标题、作者与封面只在网页那份 ``itemStruct`` 里。
    """
    video = item.get("video")
    video = video if isinstance(video, Mapping) else {}
    author = item.get("author")
    author = author if isinstance(author, Mapping) else {}
    return replace(
        movie,
        title=movie.title or _text(item.get("desc")),
        cover_urls=movie.cover_urls or _covers(_cover_of(video)),
        release_date=_day(item.get("createTime")),
        duration_minutes=minutes_of_seconds(_int(video.get("duration"))),
        creator=movie.creator or _text(author.get("nickname")),
        tags=_challenges(item.get("challenges")),
    )


def _cover_of(video: Mapping[str, object]) -> str:
    """封面按 ``cover`` → ``originCover`` → ``dynamicCover`` 取第一个。"""
    for key in ("cover", "originCover", "dynamicCover"):
        url = _text(video.get(key))
        if url:
            return url
    return ""


def _challenges(value: object) -> tuple[str, ...]:
    """``challenges`` 里那几个话题名。"""
    if not isinstance(value, list):
        return ()
    names = (_text(entry.get("title")) for entry in value if isinstance(entry, Mapping))
    return tuple(dict.fromkeys(name for name in names if name))


def _covers(value: object) -> tuple[str, ...]:
    """封面地址。**它是带时效签名的 CDN 地址**，只有 oEmbed 这一处会给。"""
    url = _text(value)
    return (url,) if url else ()


def _day(stamp: object) -> str:
    """``createTime`` 是秒级时间戳 → ``YYYY-MM-DD``（**按 UTC**，与 YouTube 那一支一致）。"""
    seconds = _int(stamp)
    if not seconds:
        return ""
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC).date().isoformat()


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


def _short_urls(token: str) -> tuple[str, ...]:
    """短链的几种写法。**token 属于哪一家只能逐个试**：它不随 token 一起被复制进来。"""
    quoted = urllib.parse.quote(token)
    return (
        f"https://vm.tiktok.com/{quoted}/",
        f"https://vt.tiktok.com/{quoted}/",
        f"{SITE}/t/{quoted}",
    )


def _oembed_url(video: str) -> str:
    return f"{OEMBED}?url={urllib.parse.quote(video, safe='')}"


def _oembed_problem(exc: SourcePageError, code: str) -> MovieImportError:
    """oEmbed 那几种状态各说各的。"""
    if exc.status in (400, 403):
        return MovieImportError(t("event.tiktok.no_info_region", code=code))
    if exc.status == 404:
        return MovieImportError(t("event.tiktok.not_found", code=code))
    return MovieImportError(t("event.tiktok.http", status=exc.status, code=code))


def _oembed(code: str, client: Transport) -> tuple[Mapping[str, object], str]:
    """问 oEmbed；短链逐个试，返回那一份资料与真正问到的地址。"""
    candidates = (movie_url(code),) if is_video_id(code) else _short_urls(code)
    problem: MovieImportError | None = None
    for candidate in candidates:
        try:
            raw = client.get_text(_oembed_url(candidate))
        except SourcePageError as exc:
            problem = _oembed_problem(exc, code)
            continue
        except MovieImportError as exc:
            problem = exc
            continue
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise MovieImportError(t("event.tiktok.unparsable", code=code)) from exc
        if isinstance(payload, Mapping):
            return payload, candidate
    raise problem or MovieImportError(t("event.tiktok.no_info", code=code))


def _page_url(payload: Mapping[str, object], code: str) -> str:
    """作者主页 + ``/video/<id>``；oEmbed 没给主页就用占位 handle 那一页。"""
    author = _text(payload.get("author_url")).rstrip("/")
    if author.startswith(f"{SITE}/@"):
        return f"{author}/video/{code}"
    return movie_url(code)


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按视频 id（或分享短链）抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、视频不可见、网络失败。
    """
    normalized = normalize_code(code)
    if not is_video_id(normalized) and not _SHARE_RE.fullmatch(normalized):
        raise MovieImportError(t("event.tiktok.invalid_code", code=code))
    payload, requested = _oembed(normalized, client)
    resolved = _resolved_code(payload, requested, normalized)
    if not is_video_id(resolved):
        raise MovieImportError(t("event.tiktok.no_id", code=code))
    movie = replace(oembed_of(payload, resolved), page_url=_page_url(payload, resolved))
    return _with_page(movie, client)


def _resolved_code(payload: Mapping[str, object], requested: str, code: str) -> str:
    """短链换来的编号：``embed_product_id`` → 回报地址里那一截 → oEmbed 那段 HTML。"""
    candidate = _text(payload.get("embed_product_id"))
    if is_video_id(candidate):
        return candidate
    found = id_from_url(requested)
    if found is not None and is_video_id(found):
        return found
    match = re.search(r"data-video-id=[\"'](\d{15,25})", _text(payload.get("html")))
    return match.group(1) if match else code


def _with_page(movie: MovieInfo, client: Transport) -> MovieInfo:
    """再去读一眼网页，把时长 / 发布日期 / 话题补上。**这一刀失败只记日志**。"""
    try:
        html = client.get_text(movie.page_url, referer=SITE + "/")
    except MovieImportError as exc:
        logger.info("TikTok 这次没给出时长与发布日期（%s）：%s", movie.code, exc.message)
        return movie
    item = item_of(html)
    return with_item(movie, item) if item else movie


__all__ = [
    "OEMBED",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "is_video_id",
    "item_of",
    "movie_url",
    "normalize_code",
    "oembed_of",
    "transport",
    "with_item",
]
