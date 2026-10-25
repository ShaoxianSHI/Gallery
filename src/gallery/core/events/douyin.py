"""按 aweme_id 从抖音抓影片元数据（ADR 0085）。

分享短链（``v.douyin.com/<token>``）里没有编号，只能跟一次跳转把它展开；这一步要联网，
所以识别仍然只认网址形状，展开发生在 :func:`fetch` 里。元数据先问 ``iteminfo`` 接口，
拿不到再读分享页里那段 ``_ROUTER_DATA`` —— 站方的反爬时紧时松，两条路都留着。
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import urllib.parse
from collections.abc import Mapping
from typing import Final

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events.kinds import SOURCE_DOUYIN, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import ANDROID_UA, HttpTransport, Transport
from gallery.core.i18n import t

logger = logging.getLogger(__name__)

#: 影片页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://www.douyin.com"

#: 分享短链的域名。**展开它才知道是哪一个视频**。
SHORT: Final = "https://v.douyin.com"

#: 取元数据的接口。
ITEMINFO: Final = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"

#: 分享页（接口被挡时的退路）。
SHARE: Final = "https://www.iesdouyin.com/share/video"

#: 分享页只认移动端来源，桌面来源给的是同一个空壳。
REFERER: Final = "https://www.douyin.com/?is_from_mobile_home=1&recommend=1"

#: aweme_id：一串数字。
_ID_RE: Final = re.compile(r"\d{15,20}")

#: 分享短链里的那一截 token。**不含连字符**，于是番号不会被当成短链去展开。
_SHARE_RE: Final = re.compile(r"[A-Za-z0-9_]{4,32}")

#: 网址里带编号的几种写法：``/video/<id>``、``/note/<id>``、``/share/video/<id>``。
_PATH_RE: Final = re.compile(r"/(?:share/)?(?:video|note)/(\d{15,20})")

#: 认这几种域名（``www`` 与 ``v`` / ``ies`` 子域都算）。
_HOST_RE: Final = re.compile(r"(?:^|\.)(?:douyin|iesdouyin)\.com$", re.I)

#: 网页版有时把编号放在查询串的 ``modal_id`` 里。
_QUERY_KEY: Final = "modal_id"

#: 分享页里那段服务端渲染的数据。
_ROUTER_RE: Final = re.compile(r"_ROUTER_DATA\s*=\s*")

#: 分享页最多问几次。**空壳是间歇性的**：拿到 ``ttwid`` 之后偶尔仍会给一次空壳。
_PAGE_TRIES: Final = 3

#: 发布时间按北京时间取日期（抖音上显示的就是那一天）。
_CST: Final = datetime.timezone(datetime.timedelta(hours=8))


def transport() -> HttpTransport:
    """抖音的传输。**安卓 UA**：分享页只按它给服务端渲染的那一版。"""
    return HttpTransport(base=SITE, referer=REFERER, ua=ANDROID_UA)


def movie_url(code: str) -> str:
    """视频页的地址。"""
    return f"{SITE}/video/{code}"


def normalize_code(text: str) -> str:
    """编号归一：去空白与末尾的斜杠。**数字编号大小写无关，分享 token 原样留着**。"""
    return (text or "").strip().rstrip("/")


def is_video_id(text: str) -> bool:
    """这一串是不是 aweme_id 的形状（15–20 位数字）。"""
    return _ID_RE.fullmatch(text.strip()) is not None


def id_from_text(text: str) -> str | None:
    """裸的 aweme_id；别的形状就是 ``None``。"""
    code = normalize_code(text)
    return code if is_video_id(code) else None


def id_from_url(url: str) -> str | None:
    """抖音网址里的 aweme_id；短链返回那一截 token，展开交给 :func:`fetch`。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = parts.netloc.lower().split(":")[0]
    if not _HOST_RE.search(host):
        return None
    match = _PATH_RE.search(parts.path)
    if match:
        return match.group(1)
    candidate = (urllib.parse.parse_qs(parts.query).get(_QUERY_KEY) or [""])[0]
    if is_video_id(candidate):
        return candidate
    if host.startswith("v."):
        token = parts.path.lstrip("/").split("/")[0]
        return token if _SHARE_RE.fullmatch(token) else None
    return None


def resolve_share_code(code: str, *, client: Transport) -> str:
    """把分享短链的 token 展开成 aweme_id。**这一步要联网**。

    :raises MovieImportError: 短链打不开，或者跳转之后仍认不出编号。
    """
    if not _SHARE_RE.fullmatch(code):
        raise MovieImportError(t("event.douyin.invalid_code", code=code))
    try:
        final = client.get_final_url(f"{SHORT}/{urllib.parse.quote(code)}/")
    except MovieImportError as exc:
        raise MovieImportError(t("event.douyin.share_expand_failed", code=code)) from exc
    found = id_from_url(final)
    if found is not None and is_video_id(found):
        return found
    raise MovieImportError(t("event.douyin.share_resolve_failed", code=code))


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def item_of(payload: Mapping[str, object]) -> Mapping[str, object]:
    """``iteminfo`` 的那份 JSON 里那一条视频；没有就是空的那一份。"""
    items = payload.get("item_list")
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        return items[0]
    return {}


def item_from_router(html: str) -> Mapping[str, object]:
    """分享页里 ``_ROUTER_DATA`` 那一段里的那一条视频；认不出就是空的那一份。"""
    match = _ROUTER_RE.search(html)
    if match is None:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except ValueError:
        return {}
    return _find_item(payload)


def _find_item(payload: object) -> Mapping[str, object]:
    """``item_list`` 可能裹在任意一层 loaderData 里，按名字找下去。"""
    if isinstance(payload, Mapping):
        items = payload.get("item_list")
        if isinstance(items, list) and items and isinstance(items[0], Mapping):
            return items[0]
        for value in payload.values():
            found = _find_item(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_item(value)
            if found:
                return found
    return {}


def movie_of(item: Mapping[str, object], code: str) -> MovieInfo:
    """那一条视频 → 一份 :class:`MovieInfo`。**页面没给的那几栏留空**。"""
    author = item.get("author")
    video = item.get("video")
    video = video if isinstance(video, Mapping) else {}
    return MovieInfo(
        source=SOURCE_DOUYIN,
        code=code,
        title=_text(item.get("desc")),
        cover_urls=_covers(
            video.get("origin_cover"), video.get("cover"), video.get("dynamic_cover")
        ),
        page_url=movie_url(code),
        release_date=_day(item.get("create_time")),
        duration_minutes=_millis_minutes(video.get("duration")),
        creator=_text(author.get("nickname")) if isinstance(author, Mapping) else "",
        tags=_hashtags(item.get("text_extra")),
    )


def _covers(*candidates: object) -> tuple[str, ...]:
    """封面地址：第一个能用的就够了，**升成绝对的 ``https``**。"""
    for candidate in candidates:
        url = _first_url(candidate)
        if url:
            return (url,)
    return ()


def _first_url(value: object) -> str:
    """``cover`` / ``origin_cover`` 是 ``{"url_list": [...]}``。"""
    if isinstance(value, Mapping):
        urls = value.get("url_list")
        if isinstance(urls, list):
            value = urls[0] if urls else ""
    url = _text(value)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _hashtags(value: object) -> tuple[str, ...]:
    """``text_extra`` 里那几个话题名。"""
    if not isinstance(value, list):
        return ()
    names = (_text(entry.get("hashtag_name")) for entry in value if isinstance(entry, Mapping))
    return tuple(dict.fromkeys(name for name in names if name))


def _day(stamp: object) -> str:
    """发布时间戳 → ``YYYY-MM-DD``（按北京时间）。"""
    seconds = _int(stamp)
    if not seconds:
        return ""
    return datetime.datetime.fromtimestamp(seconds, tz=_CST).date().isoformat()


def _millis_minutes(value: object) -> int | None:
    """``video.duration`` 是毫秒；不足半分钟的那一档交给 :func:`minutes_of_seconds`。"""
    millis = _int(value)
    return minutes_of_seconds(millis // 1000) if millis else None


def _int(value: object) -> int | None:
    """JSON 里的一个数字；不是数字就是 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _text(value: object) -> str:
    """JSON 里的一格文字；不是文字就是空串。"""
    return "" if value is None else str(value).strip()


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def _json(raw: str, code: str) -> Mapping[str, object]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MovieImportError(t("event.douyin.unparsable", code=code)) from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError(t("event.douyin.invalid_payload", code=code))
    return payload


def _item_from_api(code: str, client: Transport) -> Mapping[str, object]:
    """先问接口；**被挡或改版只记一条日志**，还有分享页那条路。"""
    url = f"{ITEMINFO}?item_ids={urllib.parse.quote(code)}"
    try:
        return item_of(_json(client.get_text(url, referer=movie_url(code)), code))
    except MovieImportError as exc:
        logger.info("抖音的接口这次没给出资料（%s）：%s", code, exc.message)
        return {}


def _item_from_page(code: str, client: Transport) -> Mapping[str, object]:
    """分享页那条路；几次都空才报错。

    **第一次请求只用来拿 ``ttwid`` cookie**：不带它时页面是一个空壳，带上它才是服务端渲染
    的那一版。拿到 cookie 之后偶尔仍会给一次空壳，所以多问几次。
    """
    url = f"{SHARE}/{urllib.parse.quote(code)}/"
    item: Mapping[str, object] = {}
    try:
        for _ in range(_PAGE_TRIES):
            item = item_from_router(client.get_text(url))
            if item:
                return item
    except SourcePageError as exc:
        if exc.status == 404:
            raise MovieImportError(t("event.douyin.not_found", code=code)) from exc
        raise
    if not item:
        raise MovieImportError(t("event.douyin.no_info", code=code))
    return item


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 aweme_id（或分享短链）抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、短链展不开、视频不可见、网络失败。
    """
    normalized = normalize_code(code)
    if not is_video_id(normalized):
        if not _SHARE_RE.fullmatch(normalized):
            raise MovieImportError(t("event.douyin.invalid_code", code=code))
        normalized = resolve_share_code(normalized, client=client)
    item = _item_from_api(normalized, client) or _item_from_page(normalized, client)
    movie = movie_of(item, normalized)
    if not movie.title:
        raise MovieImportError(t("event.douyin.no_title", code=normalized))
    return movie


__all__ = [
    "ITEMINFO",
    "REFERER",
    "SHARE",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "is_video_id",
    "item_from_router",
    "item_of",
    "movie_of",
    "movie_url",
    "normalize_code",
    "resolve_share_code",
    "transport",
]
