"""按 qipu 编号从爱奇艺抓影片元数据（ADR 0082）。

两步：移动页里那一段 ``__INITIAL_STATE__`` 带着数字 tvid（地址里那串 ``v_19rqxr146g`` 是
qipu 编号，接口不认它，两者也不能互推）；``playervideoinfo`` 免签名、免 cookie，字段一次给全。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from typing import Final

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events.kinds import SOURCE_IQIYI, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import MOBILE_UA, HttpTransport, Transport
from gallery.core.i18n import t

#: 影片页的域名（``page_url`` 用它）。
SITE: Final = "https://www.iqiyi.com"

#: 移动站的域名。**接口不认 qipu 编号，只能从这一页把 tvid 读出来**。
MOBILE: Final = "https://m.iqiyi.com"

#: 取元数据的接口域名。
API: Final = "https://pcw-api.iqiyi.com"

#: qipu 编号：地址里 ``v_`` 后面那一串。**至少带一个字母** —— 纯数字的是 tvid，两回事。
_QIPU_RE: Final = re.compile(r"[0-9a-z]{8,16}")

#: 影片页地址里的 qipu 编号。
_PATH_RE: Final = re.compile(r"/v_([0-9a-z]{8,16})\.html")

#: 服务端渲染那一版页面里的 tvid。**它是数字**，与 qipu 编号不是一回事。
_TVID_RE: Final = re.compile(r'"tvid":\s*(\d{6,20})')

#: 影片页的域名（``www`` / ``m`` 都认）。
_HOST_RE: Final = re.compile(r"(?:^|\.)iqiyi\.com$", re.I)

#: 封面按这个尺寸段取大图：``…_m_601_m9.jpg`` → ``…_m_601_m9_1280_720.jpg``。
_COVER_SIZE: Final = "_1280_720"


def transport() -> HttpTransport:
    """爱奇艺的传输。**UA 用移动版**：``m.`` 站点按桌面 UA 只给一个空壳页。"""
    return HttpTransport(base=SITE, referer=SITE + "/", ua=MOBILE_UA)


def movie_url(code: str) -> str:
    """影片页的地址。"""
    return f"{SITE}/v_{code}.html"


def is_qipu(text: str) -> bool:
    """这一串是不是 qipu 编号的形状。**纯数字不算** —— 那是 tvid，拼不出网址。"""
    stripped = text.strip()
    return _QIPU_RE.fullmatch(stripped) is not None and any(
        character.isalpha() for character in stripped
    )


def id_from_text(text: str) -> str | None:
    """裸的 qipu 编号；别的形状就是 ``None``。"""
    stripped = text.strip()
    return stripped if is_qipu(stripped) else None


def id_from_url(url: str) -> str | None:
    """爱奇艺网址里的 qipu 编号；专辑页（``a_…html``）认不出来。"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc.split(":")[0]):
        return None
    match = _PATH_RE.search(parts.path)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def tvid_of(html: str) -> str | None:
    """移动页里那个数字 tvid。**认不出就是 ``None``**（下架、改版、或者这不是影片页）。"""
    match = _TVID_RE.search(html)
    return match.group(1) if match else None


def problem_of(payload: Mapping[str, object], code: str) -> MovieImportError:
    """接口报错时给用户的那一句。``A00001`` 是「参数错误」，而不存在的 tvid 也是这一句。"""
    status = _text(payload.get("code"))
    message = _text(payload.get("errMsg")) or _text(payload.get("data"))
    if message and message != status:
        return MovieImportError(
            t("event.iqiyi.problem_message", status=status, message=message, code=code)
        )
    return MovieImportError(t("event.iqiyi.problem", status=status, code=code))


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``playervideoinfo`` 的那份 JSON → 一份 :class:`MovieInfo`。

    :raises MovieImportError: 接口自己报的错，或者响应里没有影片那一段。
    """
    if _text(payload.get("code")) != "A00000":
        raise problem_of(payload, code)
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise MovieImportError(t("event.iqiyi.no_info", code=code))
    title = _text(data.get("vn"))
    album = _text(data.get("an"))
    return MovieInfo(
        source=SOURCE_IQIYI,
        code=code,
        title=title,
        cover_urls=_covers(data.get("vpic")),
        page_url=movie_url(code),
        release_date=_day(data.get("issueTime")),
        duration_minutes=minutes_of_seconds(_int(data.get("plg"))),
        series=album if album and album != title else "",
        tags=_tags(data.get("tg")),
    )


def _covers(picture: object) -> tuple[str, ...]:
    """封面地址：升成 ``https``，再接上尺寸段取大图。"""
    url = _text(picture)
    if not url:
        return ()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    if url.endswith(".jpg"):
        url = url[: -len(".jpg")] + _COVER_SIZE + ".jpg"
    return (url,)


def _day(stamp: object) -> str:
    """``issueTime`` 是 ``20211022`` 这种整数 → ``2021-10-22``。认不出的不猜。"""
    text = _text(stamp)
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _tags(raw: object) -> tuple[str, ...]:
    """``tg`` 是空格分隔的一串（``战争 科幻 奇幻 英语 美国 院线``）。"""
    return tuple(part for part in _text(raw).split() if part)


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
    """按 qipu 编号抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、页面里没有 tvid、视频不存在、网络失败。
    """
    if not is_qipu(code):
        raise MovieImportError(t("event.iqiyi.invalid_code", code=code))
    try:
        html = client.get_text(f"{MOBILE}/v_{urllib.parse.quote(code)}.html")
    except SourcePageError as exc:
        if exc.status == 404:
            raise MovieImportError(t("event.iqiyi.not_found", code=code)) from exc
        raise
    tvid = tvid_of(html)
    if tvid is None:
        raise MovieImportError(t("event.iqiyi.no_tvid", code=code))
    return view_of(_json(client.get_text(_video_url(tvid), referer=movie_url(code))), code)


def _video_url(tvid: str) -> str:
    return f"{API}/video/video/playervideoinfo?tvid={urllib.parse.quote(tvid)}"


def _json(raw: str) -> Mapping[str, object]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MovieImportError(t("event.iqiyi.unparsable")) from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError(t("event.iqiyi.invalid_payload"))
    return payload


__all__ = [
    "API",
    "MOBILE",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "is_qipu",
    "movie_url",
    "problem_of",
    "transport",
    "tvid_of",
    "view_of",
]
