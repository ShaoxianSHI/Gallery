"""按番号从 JavDB 抓取影片元数据（ADR 0063），抓成三个来源共用的 :class:`MovieInfo`（ADR 0068）。

**解析是纯逻辑**（HTML → 字段），能直接单测；真正发请求的是 :mod:`gallery.core.events.transport` 里
那份共用的传输，测试传假实现即可。站点结构一变，改这里的正则就行 —— 与同级 ``import_shows``
同一分工：这一层不碰事件库、不碰 Qt。
"""

from __future__ import annotations

import html as _html
import re
import unicodedata
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from gallery.core.errors import MovieImportError
from gallery.core.events.kinds import SOURCE_JAV
from gallery.core.events.models import MovieInfo
from gallery.core.events.transport import HttpTransport, Transport

#: 抓取的源站。**抓取与「打开影片网站」是两件事**：后者由 ``config.movie_site`` 说了算。
DEFAULT_SOURCE_BASE: Final = "https://javdb.com"

#: 通过年龄确认用的 cookie（源站要求）。
DEFAULT_COOKIE: Final = "over18=1"

#: 详情页各语系字段标签 → :class:`MovieInfo` 的字段名。**多值字段（演员 / 标签）单列**。
#:
#: 「片商 / 製作商」落在 ``creator``：这一栏在三个来源里都要装（片商、频道、UP 主），
#: 所以它取的是中性名（ADR 0068 决策 2）。
_ALIASES: Final[dict[str, str]] = {
    "番號": "code",
    "番号": "code",
    "ID": "code",
    "日期": "release_date",
    "發行日期": "release_date",
    "发行日期": "release_date",
    "時長": "duration",
    "时长": "duration",
    "長度": "duration",
    "长度": "duration",
    "導演": "director",
    "导演": "director",
    "片商": "creator",
    "製作商": "creator",
    "制作商": "creator",
    "發行商": "label",
    "发行商": "label",
    "系列": "series",
    "評分": "rating",
    "评分": "rating",
    "類別": "tags",
    "类别": "tags",
    "演員": "actors",
    "演员": "actors",
}


def transport(base: str = DEFAULT_SOURCE_BASE) -> HttpTransport:
    """JavDB 的传输：年龄确认的 cookie、繁体中文，以及站点自己的 ``Referer``。"""
    return HttpTransport(
        base=base,
        cookie=DEFAULT_COOKIE,
        language="zh-TW,zh;q=0.9,en;q=0.8",
    )


@dataclass(frozen=True, slots=True)
class SearchHit:
    """搜索页里的一条结果：定位番号只用到 ``id`` 与 ``url``。"""

    id: str
    url: str
    title: str = ""
    cover: str = ""
    date: str = ""


def normalize_code(text: str) -> str:
    """把用户敲的番号归一成 ``IPX-660``（去空白、全角转半角、``IPX660`` → ``IPX-660``）。

    **只对 JavDB 这一个来源成立**：YouTube 的 videoId 与 B 站的 BV 号都**大小写敏感**，
    顺手 ``upper()`` 会静默指向另一部片子（ADR 0068 决策 3）。
    """
    code = unicodedata.normalize("NFKC", (text or "").strip().upper())
    code = re.sub(r"\s+", "", code).replace("_", "-")
    if re.fullmatch(r"[A-Z]+\d+", code):
        code = re.sub(r"^([A-Z]+)(\d+)$", r"\1-\2", code)
    return code


def code_shape(text: str) -> bool:
    """这一串是不是**番号的形状**（``字母 + 数字``，可带一个连字符）。

    识别用户粘进来的那一坨文字时要用它：恰好 11 位、又长成番号的东西（``ABCDE-12345``）
    必须留给 JavDB，不能被当成 YouTube 的 videoId（ADR 0068 决策 3）。
    """
    return re.fullmatch(r"[A-Za-z]+-?\d+", text) is not None


def movie_url(base: str, code: str) -> str:
    """影片网站的地址：``https://jable.tv/videos/`` + ``ipx-660`` + ``/``。

    **番号小写、带连字符**（``normalize_code`` 给的就是这个形状）。
    """
    host = base.strip().rstrip("/")
    return f"{host}/{code.lower()}/"


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #

_TAG_RE: Final = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)

#: 详情页里一个字段的边界：下一个字段 / 评论按钮 / 面板结束。**拼成两段只是为了行宽**。
_FIELD_RE: Final = re.compile(
    r"<strong>([^<]+?)\s*:</strong>(.*?)"
    r'(?=<div class="panel-block|<div class="review-buttons|</nav>)',
    re.S,
)

#: 搜索页的结果区为空时源站给的那一段（``暫無內容``）。**它不在时那条搜索页就不是搜索结果页** ——
#: 限速 / 反爬给的往往是别的页面，两者要用户做的事完全不一样（换番号 vs 等一会儿再试）。
_EMPTY_PAGE_RE: Final = re.compile(r'class="empty-message"', re.I)


def strip_tags(fragment: str) -> str:
    """一段 HTML 里的可见文字（去标签、解实体、压空白）。"""
    if not fragment:
        return ""
    text = _TAG_RE.sub(" ", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[\u00a0\u3000]", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def parse_search_items(html: str, base: str) -> tuple[SearchHit, ...]:
    """搜索页里那些结果卡片，一行一条。"""
    hits: list[SearchHit] = []
    for match in re.finditer(
        r'<a\s+href="(/v/[^"#?]+)"[^>]*class="box"[^>]*>(.*?)</a>', html, re.S | re.I
    ):
        href, body = match.group(1), match.group(2)
        idm = re.search(r'class="video-title"><strong>([^<]+)</strong>(.*?)</div>', body, re.S)
        if not idm:
            continue
        identifier = strip_tags(idm.group(1))
        title = strip_tags(idm.group(2))
        cover = re.search(r'<img[^>]+src="([^"]+)"', body)
        date = re.search(r'class="meta">\s*([0-9]{4}-[0-9]{2}-[0-9]{2})', body)
        hits.append(
            SearchHit(
                id=identifier,
                title=title,
                url=urllib.parse.urljoin(base + "/", href.lstrip("/")),
                cover=cover.group(1) if cover else "",
                date=date.group(1) if date else "",
            )
        )
    return tuple(hits)


def locate(hits: Sequence[SearchHit], code: str) -> SearchHit:
    """从搜索结果里挑出番号精确相等的那一条。**找不到 / 两条分不清都给一句中文。**

    :raises MovieImportError: 没有精确命中，或者命中多条又无法用日期定先后。
    """
    exact = [hit for hit in hits if normalize_code(hit.id) == code]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        exact = sorted(exact, key=lambda hit: hit.date, reverse=True)
        return exact[0]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise MovieImportError(f"源站没有这个编号：{code}。")
    raise MovieImportError(f"编号 {code} 找到多条近似结果，没法确定是哪一部。")


def no_search_results(html: str, code: str) -> MovieImportError:
    """搜索页里一条结果都没有时给用户的那一句。**看源站那一段「暫無內容」在不在**：

    在 = 确实没有这个番号（换一个试试）；不在 = 这一页根本不是搜索结果页（多半被限速或挡住了，
    等一会儿再试）—— 混成一句「查不到番号」会把人支到错的那条路上。
    """
    if _EMPTY_PAGE_RE.search(html):
        return MovieImportError(f"源站没有这个编号：{code}。换一个试试，或者确认它的写法。")
    return MovieImportError(
        f"源站没给出搜索结果（{code}）：多半是被限速或挡住了，等一会儿再试一次。"
    )


def parse_detail(html: str, code: str, *, base: str, hit: SearchHit | None = None) -> MovieInfo:
    """详情页 → 一份 :class:`MovieInfo`。``hit`` 是搜索页那一条，用来回退标题与封面。"""
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(html):
        key = _ALIASES.get(strip_tags(match.group(1)))
        if key and key not in fields:
            fields[key] = match.group(2)

    title = ""
    tm = re.search(r'<strong class="current-title">(.*?)</strong>', html, re.S)
    if tm:
        title = strip_tags(tm.group(1))
    if not title and hit is not None:
        title = hit.title

    cover = ""
    for pattern in (
        r'<img[^>]+class="video-cover"[^>]*src="([^"]+)"',
        r'data-movie-detail-target="cover"[^>]*src="([^"]+)"',
    ):
        cm = re.search(pattern, html, re.S)
        if cm:
            cover = cm.group(1)
            break
    if not cover and hit is not None:
        cover = hit.cover

    raw_code = strip_tags(fields.get("code", ""))
    release = re.search(r"(\d{4}-\d{2}-\d{2})", strip_tags(fields.get("release_date", "")))
    duration = re.search(r"(\d+)", strip_tags(fields.get("duration", "")))
    rating = re.search(r"([0-9]+(?:\.[0-9]+)?)", strip_tags(fields.get("rating", "")))
    return MovieInfo(
        source=SOURCE_JAV,
        code=normalize_code(raw_code) or code,
        title=title,
        cover_urls=(cover,) if cover else (),
        page_url=hit.url if hit is not None else base,
        release_date=release.group(1) if release else (hit.date if hit is not None else ""),
        duration_minutes=int(duration.group(1)) if duration else None,
        creator=strip_tags(fields.get("creator", "")),
        label=strip_tags(fields.get("label", "")),
        series=strip_tags(fields.get("series", "")),
        director=strip_tags(fields.get("director", "")),
        actors=_split_names(fields.get("actors", "")),
        tags=_split_names(fields.get("tags", "")),
        rating=float(rating.group(1)) if rating else None,
    )


def _split_names(fragment: str) -> tuple[str, ...]:
    """演员 / 标签那一格：去标签后按逗号拆，空项丢掉。"""
    text = strip_tags(fragment)
    return tuple(part.strip() for part in re.split(r"[,，、]", text) if part.strip())


def fetch(code: str, *, client: Transport, base: str = DEFAULT_SOURCE_BASE) -> MovieInfo:
    """按番号抓一部影片：搜到详情页 → 解析。**不下载封面**（那要写文件，由调用方决定）。

    :param client: 抓取用的传输（:class:`~gallery.core.events.transport.HttpTransport`
        或测试用的假实现）。
    :raises MovieImportError: 检索不到、解析不出来，或网络失败。
    """
    norm = normalize_code(code)
    if not norm:
        raise MovieImportError("编号不能为空。")
    getter = client.get_text
    search_html = getter(urllib.parse.urljoin(base + "/", f"search?f=all&q={norm}"))
    hits = parse_search_items(search_html, base)
    if not hits:
        raise no_search_results(search_html, norm)
    hit = locate(hits, norm)
    detail_html = getter(hit.url, referer=urllib.parse.urljoin(base + "/", "search"))
    movie = parse_detail(detail_html, norm, base=base, hit=hit)
    if not movie.cover_urls:
        raise MovieImportError(f"编号 {norm} 的页面里没找到封面图。")
    return movie


__all__ = [
    "DEFAULT_COOKIE",
    "DEFAULT_SOURCE_BASE",
    "SearchHit",
    "code_shape",
    "fetch",
    "locate",
    "movie_url",
    "no_search_results",
    "normalize_code",
    "parse_detail",
    "parse_search_items",
    "strip_tags",
    "transport",
]
