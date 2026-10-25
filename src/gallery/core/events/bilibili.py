"""按 BV 号从 B 站抓影片元数据（ADR 0068）。**只依赖标准库**：`av` 与 BV 的互转是纯算术。

B 站这一侧最省事：``x/web-interface/view`` **一个 JSON 请求**就给出标题、封面原图、发布时间、
时长、UP 主与合集，标签在 ``x/web-interface/view/detail/tag``。两个接口**游客可用**（实测免 cookie、
免 WBI 签名），而 HTTP 状态永远是 200、错在 JSON 的 ``code`` 里 —— 于是「稿件没了」那几种情形都
分得清（见 :data:`_PROBLEMS`）。
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

from gallery.core.errors import MovieImportError
from gallery.core.events.kinds import SOURCE_BILIBILI, minutes_of_seconds
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport
from gallery.core.i18n import t

logger = logging.getLogger(__name__)

#: 抓取用的接口域名。
BASE: Final = "https://api.bilibili.com"

#: 稿件页的域名（``page_url`` 与 ``Referer`` 用它）。
SITE: Final = "https://www.bilibili.com"

#: 接口自己的错误码 → 给用户的一句中文。**HTTP 是 200，错在 JSON 里**，
#: 所以「删了」「私密」「还在审核」分得清楚 —— 混成一句「取不到」会把人支到错的下一步。
_PROBLEMS: Final[dict[int, str]] = {
    -400: "event.bilibili.problem.invalid_code",
    -403: "event.bilibili.problem.forbidden",
    -404: "event.bilibili.problem.not_found",
    -352: "event.bilibili.problem.risk_control",
    62002: "event.bilibili.problem.unavailable",
    62004: "event.bilibili.problem.under_review",
    62012: "event.bilibili.problem.up_only",
}

#: BV 号的 base58 字母表（`av` ↔ BV 互转用）。**它不含 ``0 I O l``**，字母表的顺序是算法的一部分。
_BV_ALPHABET: Final = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"

#: `av` ↔ BV 那两下异或与掩码，来自 B 站自己那份说明。**互转是纯算术，不联网**。
_BV_XOR: Final = 23442827791579
_BV_MASK: Final = (1 << 51) - 1
_BV_HEAD: Final = 1 << 51
_BV_BASE: Final = 58

#: BV 号：``BV`` + 10 位 base58。**大小写敏感** —— ``BV1GJ411x7h7`` 与 ``BV1gj411X7h7``
#: 是两部不同的片子（实测 aid 80433022 与 444729062）。
_BV_RE: Final = re.compile(r"BV[1-9A-HJ-NP-Za-km-z]{10}")

#: 旧的 ``av`` 号。
_AV_RE: Final = re.compile(r"av(\d+)", re.I)

#: 稿件页的域名（含 ``www``）。
_HOST_RE: Final = re.compile(r"(?:^|\.)bilibili\.com$", re.I)

#: 稿件页地址里的编号。
_PATH_RE: Final = re.compile(r"/video/(BV[1-9A-HJ-NP-Za-km-z]{10}|av\d+)")

#: 发布时间戳按北京时间取日期（B 站是国内站，用户看到的就是那一天）。
_CST: Final = datetime.timezone(datetime.timedelta(hours=8))

#: 打开稿件时用来覆盖「上次播放位置」的起点（秒）。**B 站的 ``t`` 只在大于 0 时生效**，
#: 写 0 会被当成没给，播放器照样接上次的位置。
START_AT_BEGINNING: Final = "0.001"


def transport() -> HttpTransport:
    """B 站的传输：接口域名加 B 站的 ``Referer``（**不带它接口会当你是爬虫**）。"""
    return HttpTransport(base=BASE, referer=SITE + "/")


def movie_url(code: str, *, start_at_beginning: bool = False) -> str:
    """稿件页的地址。``start_at_beginning`` 时带上 ``t``，让播放器从头开始。"""
    url = f"{SITE}/video/{code}/"
    return f"{url}?t={START_AT_BEGINNING}" if start_at_beginning else url


# --------------------------------------------------------------------------- #
# 编号：BV 号与旧的 av 号
# --------------------------------------------------------------------------- #


def av_to_bv(aid: int) -> str:
    """旧的 ``av`` 号（数字）→ BV 号。**纯算术**：两者本来就是同一个东西的两种写法。

    用户手上两种写法都有（老链接还在被人转），所以进来的 ``av`` 号一律换成 BV 号再存 ——
    不然同一部片子按两种写法导两次就是两条事件。
    """
    characters = list("BV1000000000")
    index = len(characters) - 1
    remainder = (_BV_HEAD | aid) ^ _BV_XOR
    while remainder:
        characters[index] = _BV_ALPHABET[remainder % _BV_BASE]
        remainder //= _BV_BASE
        index -= 1
    characters[3], characters[9] = characters[9], characters[3]
    characters[4], characters[7] = characters[7], characters[4]
    return "".join(characters)


def bv_to_av(code: str) -> int:
    """BV 号 → 旧的 ``av`` 号（数字）。:func:`av_to_bv` 的逆运算。"""
    characters = list(code)
    characters[3], characters[9] = characters[9], characters[3]
    characters[4], characters[7] = characters[7], characters[4]
    remainder = 0
    for character in characters[3:]:
        remainder = remainder * _BV_BASE + _BV_ALPHABET.index(character)
    return (remainder & _BV_MASK) ^ _BV_XOR


def id_from_text(text: str) -> str | None:
    """裸的 ``BV…`` 或 ``av…`` → BV 号；别的形状就是 ``None``。

    **不许顺手 ``upper()``**：BV 号大小写敏感（ADR 0068 决策 3）。
    """
    stripped = text.strip()
    if _BV_RE.fullmatch(stripped):
        return stripped
    match = _AV_RE.fullmatch(stripped)
    if match and int(match.group(1)) > 0:
        return av_to_bv(int(match.group(1)))
    return None


def id_from_url(url: str) -> str | None:
    """B 站网址里的 BV 号；不是 B 站或者认不出就是 ``None``。

    **``b23.tv`` 那种短链不认**：展开它要发一次网络请求，而识别这一步不联网（ADR 0068 决策 3）。
    """
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if not _HOST_RE.search(parts.netloc):
        return None
    match = _PATH_RE.search(parts.path)
    if not match:
        return None
    token = match.group(1)
    if token[:2].lower() == "av":
        return av_to_bv(int(token[2:]))
    return token


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def problem_of(payload: Mapping[str, object], code: str) -> MovieImportError:
    """接口报错时给用户的那一句。**认不出的码原样带上**（用户报问题时能照着搜）。"""
    status = payload.get("code")
    known = t(_PROBLEMS[status]) if isinstance(status, int) and status in _PROBLEMS else None
    if known is not None:
        return MovieImportError(t("event.bilibili.problem_known", reason=known, code=code))
    message = _text(payload.get("message"))
    if message:
        return MovieImportError(
            t("event.bilibili.problem_unknown_message", status=status, message=message)
        )
    return MovieImportError(t("event.bilibili.problem_unknown", status=status))


def view_of(payload: Mapping[str, object], code: str) -> MovieInfo:
    """``x/web-interface/view`` 的那份 JSON → 一份 :class:`MovieInfo`。**标签另说**。

    :raises MovieImportError: 接口自己报的错。
    """
    status = payload.get("code")
    if status != 0:
        raise problem_of(payload, code)
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise MovieImportError(t("event.bilibili.no_info", code=code))
    bvid = _text(data.get("bvid")) or code
    owner = data.get("owner")
    season = data.get("ugc_season")
    return MovieInfo(
        source=SOURCE_BILIBILI,
        code=bvid,
        title=_text(data.get("title")),
        cover_urls=_covers(data.get("pic")),
        page_url=movie_url(bvid),
        release_date=_day(data.get("pubdate")),
        duration_minutes=minutes_of_seconds(_int(data.get("duration"))),
        creator=_text(owner.get("name")) if isinstance(owner, Mapping) else "",
        series=_text(season.get("title")) if isinstance(season, Mapping) else "",
    )


def tags_of(payload: Mapping[str, object]) -> tuple[str, ...]:
    """``x/web-interface/view/detail/tag`` 的那份 JSON → 标签。

    **认不出就是空的**：这一条是「补一刀」，拿不到标签不该让整个导入失败（ADR 0068 决策 4）。
    """
    if payload.get("code") != 0:
        return ()
    data = payload.get("data")
    if not isinstance(data, list):
        return ()
    names = (_text(entry.get("tag_name")) for entry in data if isinstance(entry, Mapping))
    return tuple(name for name in names if name)


def _covers(picture: object) -> tuple[str, ...]:
    """封面原图。``pic`` 给的是 ``http://``，**升成 ``https``**（库里的封面地址不许是明文）。"""
    url = _text(picture)
    if not url:
        return ()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return (url,)


def _day(pubdate: object) -> str:
    """发布时间戳 → ``YYYY-MM-DD``（**按北京时间**，B 站上显示的就是那一天）。"""
    seconds = _int(pubdate)
    if not seconds:
        return ""
    return datetime.datetime.fromtimestamp(seconds, tz=_CST).date().isoformat()


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
        raise MovieImportError(t("event.bilibili.unparsable", code=code)) from exc
    if not isinstance(payload, Mapping):
        raise MovieImportError(t("event.bilibili.invalid_payload", code=code))
    return payload


def fetch(code: str, *, client: Transport) -> MovieInfo:
    """按 BV 号抓一部影片。**不下载封面**（那要写文件，由调用方决定）。

    先要 ``view``（标题、封面、日期、时长、UP 主都在里面），再去要一遍标签。**标签那一趟失败
    只记一条日志** —— 它是补的，拿不到不该让整部影片导不进来。

    :raises MovieImportError: 稿件不存在 / 不可见 / 编号不合法 / 网络失败。
    """
    if not _BV_RE.fullmatch(code):
        raise MovieImportError(t("event.bilibili.invalid_code", code=code))
    movie = view_of(_json(client.get_text(_view_url(code)), code), code)
    tags = _tags(code, client)
    return replace(movie, tags=tags) if tags else movie


def _view_url(code: str) -> str:
    return f"{BASE}/x/web-interface/view?bvid={urllib.parse.quote(code)}"


def _tags(code: str, client: Transport) -> tuple[str, ...]:
    url = f"{BASE}/x/web-interface/view/detail/tag?bvid={urllib.parse.quote(code)}"
    try:
        return tags_of(_json(client.get_text(url, referer=movie_url(code)), code))
    except MovieImportError as exc:
        logger.info("B 站这次没给出标签（%s）：%s", code, exc.message)
        return ()


__all__ = [
    "BASE",
    "SITE",
    "START_AT_BEGINNING",
    "av_to_bv",
    "bv_to_av",
    "fetch",
    "id_from_text",
    "id_from_url",
    "movie_url",
    "problem_of",
    "tags_of",
    "transport",
    "view_of",
]
