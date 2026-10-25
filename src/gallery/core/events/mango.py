"""按 vid 从芒果 TV 抓影片元数据（ADR 0082）。

一个 JSON 请求给标题、封面、时长与剧集，正式剧集另有一份 ``detail``（日期、类型、导演、主持）。
**影片页要「合集 + 单集」两个编号**（``/b/<合集>/<单集>.html``，只给单集是 404），合集 id 在
``info.clipId`` 里；UGC 片段没有合集 id，它在网页上本来就没有页面。
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

#: 影片页地址里的两个编号。**单集那一段是 vid，前一段是合集 id。**
_PATH_RE: Final = re.compile(r"/b/(\d{4,8})/(\d{6,10})\.html")

#: 影片页的域名（``m`` 也认）。
_HOST_RE: Final = re.compile(r"(?:^|\.)mgtv\.com$", re.I)

#: 接口自己的错误码 → 一句中文。
_PROBLEMS: Final[dict[int, str]] = {
    10008: "芒果 TV 未收录该视频",
}

#: 「编号」里那两段之间的分隔符。**与网址里那一小段同序**：先说合集，再说单集。
SEPARATOR: Final = "/"


def transport() -> HttpTransport:
    """芒果 TV 的传输。"""
    return HttpTransport(base=SITE)


def movie_url(code: str) -> str | None:
    """影片页的地址。**编号里没有合集 id 就打不开** —— UGC 片段只有 App 里有页面。"""
    return f"{SITE}/b/{code}.html" if SEPARATOR in code else None


def page_code(clip: str, video: str) -> str:
    """``编号`` 的标准写法：``合集/单集``。"""
    return SEPARATOR.join((clip, video))


def id_from_text(text: str) -> str | None:
    """裸的 vid（纯数字）；别的形状就是 ``None``。**它打不开网页**，只能拿来抓元数据。"""
    stripped = text.strip()
    return stripped if _VID_RE.fullmatch(stripped) else None


def id_from_url(url: str) -> str | None:
    """芒果 TV 网址里的「合集/单集」。**``/h/`` 那种整部剧的页面认不出来。**"""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc.split(":")[0]):
        return None
    match = _PATH_RE.search(parts.path)
    return page_code(match.group(1), match.group(2)) if match else None


def video_id_of(code: str) -> str:
    """编号里那一截 vid。``294541/3779644`` 与 ``3779644`` 都给 ``3779644``。"""
    return code.rsplit(SEPARATOR, 1)[-1].strip()


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
    return MovieImportError(f"芒果 TV 未提供该视频的信息：错误码 {status}{tail}。")


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``video/info`` 的那份 JSON → 一份 :class:`MovieInfo`。

    :raises MovieImportError: 接口自己报的错，或者响应里没有影片那一段。
    """
    if _int(payload.get("code")) != 200:
        raise problem_of(payload, code)
    data = payload.get("data")
    info = data.get("info") if isinstance(data, Mapping) else None
    if not isinstance(info, Mapping):
        raise MovieImportError(f"芒果 TV 未提供该视频的信息：{code}。")
    title = _text(info.get("videoName")) or _text(info.get("title"))
    if not title:
        raise MovieImportError(f"芒果 TV 未提供该视频的信息：{code}。")
    clip = _text(info.get("clipId"))
    video = video_id_of(code)
    full = page_code(clip, video) if clip.isdigit() and clip != "0" else video
    detail_raw = info.get("detail")
    detail = detail_raw if isinstance(detail_raw, Mapping) else {}
    show = _text(info.get("title"))
    return MovieInfo(
        source=SOURCE_MANGO,
        code=full,
        title=title,
        cover_urls=_covers(info.get("videoImage") or info.get("clipImage")),
        page_url=movie_url(full) or SITE + "/",
        release_date=_day(detail.get("releaseTime")),
        duration_minutes=minutes_of_seconds(_seconds(info.get("time"))),
        series=show if show and show != title else "",
        director=_text(detail.get("director")) if detail.get("director") != "未知" else "",
        actors=_names(detail.get("presenter")),
        tags=_names(detail.get("kind")),
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


def _names(raw: object) -> tuple[str, ...]:
    """``王姬 / 陆立``、``真人秀 / 纪实`` 这种写法：按斜杠拆开。**``未知`` 不算一项。**"""
    return tuple(
        part.strip() for part in _text(raw).split("/") if part.strip() and part.strip() != "未知"
    )


def _day(value: object) -> str:
    """``2006-09-04`` 取那一天；认不出的不猜。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", _text(value))
    return match.group(1) if match else ""


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
    """按编号抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    :raises MovieImportError: 编号不合法、视频不存在、网络失败。
    """
    video = video_id_of(code)
    if not _VID_RE.fullmatch(video):
        raise MovieImportError(f"不是有效的芒果 TV vid：{code}。")
    return view_of(_json(client.get_text(_info_url(video))), code)


def _info_url(code: str) -> str:
    return f"{API}/video/info?vid={urllib.parse.quote(code)}"


def _json(raw: str) -> Mapping[str, object]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MovieImportError("芒果 TV 返回的信息无法解析。") from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError("芒果 TV 返回的信息不是有效的视频信息。")
    return payload


__all__ = [
    "API",
    "SEPARATOR",
    "SITE",
    "fetch",
    "id_from_text",
    "id_from_url",
    "movie_url",
    "page_code",
    "problem_of",
    "transport",
    "video_id_of",
    "view_of",
]
