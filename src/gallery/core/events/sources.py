"""影片是「哪儿的哪一个」：编号怎么认、按来源分派抓取、打开哪一页（ADR 0068 / 0082 / 0083）。

识别按可靠性排序：网址 → 面板选的来源 → ``BV`` / ``av`` → 11 位 videoId → 番号，纯函数不联网。
**裸编号的歧义靠面板消**：腾讯的 vid 与 YouTube 的 videoId 同形，芒果与 Netflix 都是纯数字，
只凭形状分不出来；网址仍然优先（粘错站也照粘的那一站认）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

from gallery.core.errors import MovieImportError
from gallery.core.events import (
    bilibili,
    douyin,
    iqiyi,
    jav,
    mango,
    pornhub,
    tencent,
    tiktok,
    youku,
    youtube,
)
from gallery.core.events.kinds import (
    SOURCE_BILIBILI,
    SOURCE_DOUYIN,
    SOURCE_IQIYI,
    SOURCE_JAV,
    SOURCE_MANGO,
    SOURCE_PORNHUB,
    SOURCE_TENCENT,
    SOURCE_TIKTOK,
    SOURCE_YOUKU,
    SOURCE_YOUTUBE,
)
from gallery.core.events.models import MovieInfo, MovieRef
from gallery.core.events.transport import Transport

#: 像网址的东西。**带不带的 ``scheme`` 都算**（用户从地址栏复制时多半带着，手打时多半不带）。
_URL_RE: Final = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?[\w-]+(?:\.[\w-]+)+(?:[/:?#]|$)", re.I)

#: 按域名认来源。**顺序无关**（每个域名只属于一家），按导入菜单的习惯排。
_URL_PARSERS: Final[tuple[tuple[str, Callable[[str], str | None]], ...]] = (
    (SOURCE_YOUTUBE, youtube.id_from_url),
    (SOURCE_DOUYIN, douyin.id_from_url),
    (SOURCE_TIKTOK, tiktok.id_from_url),
    (SOURCE_BILIBILI, bilibili.id_from_url),
    (SOURCE_TENCENT, tencent.id_from_url),
    (SOURCE_IQIYI, iqiyi.id_from_url),
    (SOURCE_YOUKU, youku.id_from_url),
    (SOURCE_MANGO, mango.id_from_url),
    (SOURCE_PORNHUB, pornhub.id_from_url),
)

#: 按面板选的来源认裸编号。**JavDB 不在里面**：番号是回退，什么形状都能当番号。
_TEXT_PARSERS: Final[dict[str, Callable[[str], str | None]]] = {
    SOURCE_YOUTUBE: lambda text: text if youtube.is_video_id(text) else None,
    SOURCE_DOUYIN: douyin.id_from_text,
    SOURCE_TIKTOK: tiktok.id_from_text,
    SOURCE_BILIBILI: bilibili.id_from_text,
    SOURCE_TENCENT: tencent.id_from_text,
    SOURCE_IQIYI: iqiyi.id_from_text,
    SOURCE_YOUKU: youku.id_from_text,
    SOURCE_MANGO: mango.id_from_text,
    SOURCE_PORNHUB: pornhub.id_from_text,
}


def source_of(text: str) -> str:
    """事件里「来源」那一栏的值。**空的当 JavDB 来的**。

    两种情形会没填：早于 ADR 0068 的影片事件（迁移已经回填过，但代码里手写的事件草稿不在其中，
    例如 ``EventDraft.of(code="IPX-660")``）。那时能打开的只有 JavDB 那一页 —— 这个库本来
    就只有它一个来路。
    """
    return (text or "").strip() or SOURCE_JAV


def parse_ref(text: str, *, source: str | None = None) -> MovieRef:
    """把用户敲的一行读成「哪儿的哪一个」。

    :param source: 面板上选的那个来源。**它只在「这一行不是网址」时起作用** —— 腾讯的 vid 与
        YouTube 的 videoId 同形，只凭形状会认错（ADR 0082 决策 2）。
    :raises MovieImportError: 空行，或者认不出来（例如 ``b23.tv`` 短链）。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise MovieImportError("编号或链接不能为空。")
    if _URL_RE.match(cleaned):
        return _ref_from_url(cleaned)
    if source is not None:
        parser = _TEXT_PARSERS.get(source)
        if parser is not None:
            code = parser(cleaned)
            if code:
                return MovieRef(source=source, code=code)
    # 不是网址：先把「与番号不可能撞」的形状认掉。
    code = bilibili.id_from_text(cleaned)
    if code:
        return MovieRef(source=SOURCE_BILIBILI, code=code)
    # YouTube 的 videoId 与番号撞得最厉害（恰好 11 位、字母数字混着），所以要把番号那个
    # 形状排除在外 —— ``ABCDE-12345`` 也是 11 位，但用户写的多半是番号。
    if youtube.is_video_id(cleaned) and not jav.code_shape(cleaned):
        return MovieRef(source=SOURCE_YOUTUBE, code=cleaned)
    return MovieRef(source=SOURCE_JAV, code=jav.normalize_code(cleaned))


def _ref_from_url(url: str) -> MovieRef:
    """网址里的编号。**认不出就给一句中文**，不猜是哪个站。"""
    for source, parser in _URL_PARSERS:
        code = parser(url)
        if code:
            return MovieRef(source=source, code=code)
    raise MovieImportError(
        f"无法识别该地址：{url}。短链请先在浏览器中打开，再粘贴完整地址；"
        "合集页请先打开某一集再复制。"
    )


def movie_url(ref: MovieRef, *, jav_site: str) -> str | None:
    """这个影片该打开的网址；**来源认不出就是 ``None``**。

    ``jav_site`` 只管 JavDB 那一栏（用户可以把它换成 jable.tv 之类的镜像）；其余来源的地址形状
    是固定的，没有可配置的余地。**B 站那一页带上 ``t``**，于是打开时从开头播放，
    而不是接上 B 站自己的「上次播放位置」。
    """
    if ref.source == SOURCE_JAV:
        return jav.movie_url(jav_site, ref.code)
    if ref.source == SOURCE_YOUTUBE:
        return youtube.movie_url(ref.code)
    if ref.source == SOURCE_DOUYIN:
        return douyin.movie_url(ref.code)
    if ref.source == SOURCE_TIKTOK:
        return tiktok.movie_url(ref.code)
    if ref.source == SOURCE_BILIBILI:
        return bilibili.movie_url(ref.code, start_at_beginning=True)
    if ref.source == SOURCE_TENCENT:
        return tencent.movie_url(ref.code)
    if ref.source == SOURCE_IQIYI:
        return iqiyi.movie_url(ref.code)
    if ref.source == SOURCE_YOUKU:
        return youku.movie_url(ref.code)
    if ref.source == SOURCE_MANGO:
        return mango.movie_url(ref.code)
    if ref.source == SOURCE_PORNHUB:
        return pornhub.movie_url(ref.code)
    return None


class MovieSource(Protocol):
    """抓取影片的入口。:class:`HttpSources` 是真实实现，测试传假实现（**不必联网**）。

    ``cover_bytes`` 与 ``fetch`` 分开：抓元数据要联网解析，下封面只是取字节 —— 而**封面写盘是
    调用方的事**（那要写进用户的文件夹，见 ``Library.import_movies``）。
    """

    def fetch(self, ref: MovieRef) -> MovieInfo: ...

    def cover_bytes(self, movie: MovieInfo) -> bytes: ...


#: 来源 → 抓取那一份元数据的函数。各模块的函数签名是同一个形状（``code`` + ``client=``），
#: 所以分派是一张表而不是一串 ``if``。
_FETCHERS: Final[dict[str, Callable[..., MovieInfo]]] = {
    SOURCE_JAV: jav.fetch,
    SOURCE_YOUTUBE: youtube.fetch,
    SOURCE_DOUYIN: douyin.fetch,
    SOURCE_TIKTOK: tiktok.fetch,
    SOURCE_BILIBILI: bilibili.fetch,
    SOURCE_TENCENT: tencent.fetch,
    SOURCE_IQIYI: iqiyi.fetch,
    SOURCE_YOUKU: youku.fetch,
    SOURCE_MANGO: mango.fetch,
    SOURCE_PORNHUB: pornhub.fetch,
}


def _default_transports() -> dict[str, Transport]:
    """每个来源各一条传输。**每个来源只在自己的 ``base`` / ``Referer`` / cookie / UA 上不同。**"""
    return {
        SOURCE_JAV: jav.transport(),
        SOURCE_YOUTUBE: youtube.transport(),
        SOURCE_DOUYIN: douyin.transport(),
        SOURCE_TIKTOK: tiktok.transport(),
        SOURCE_BILIBILI: bilibili.transport(),
        SOURCE_TENCENT: tencent.transport(),
        SOURCE_IQIYI: iqiyi.transport(),
        SOURCE_YOUKU: youku.transport(),
        SOURCE_MANGO: mango.transport(),
        SOURCE_PORNHUB: pornhub.transport(),
    }


@dataclass
class HttpSources:
    """每个来源一条传输，按 ``ref.source`` 分派。

    **所有传输活在同一份对象里**：限速的「上一次请求是什么时候」记在每条传输上，所以一次导入里的
    二十部影片共用同一份节奏，而不是每部新建一个客户端从头开始。
    """

    transports: dict[str, Transport] = field(default_factory=_default_transports)

    def transport_of(self, source: str) -> Transport:
        """这个来源的那条传输。**认不出的来源就是一句中文**（面板的下拉框理论上给不出别的）。"""
        transport = self.transports.get(source)
        if transport is None:
            raise MovieImportError(f"认不出这个来源：{source}。")
        return transport

    def fetch(self, ref: MovieRef) -> MovieInfo:
        fetcher = _FETCHERS.get(ref.source)
        if fetcher is None:
            raise MovieImportError(f"认不出这个来源：{ref.source}。")
        return fetcher(ref.code, client=self.transport_of(ref.source))

    def cover_bytes(self, movie: MovieInfo) -> bytes:
        """把封面下下来。

        **每个来源用自己的那条传输**：``Referer`` 与 cookie 各不相同（B 站的图床认 ``Referer``），
        限速的节奏也该各算各的。``cover_urls`` 按顺序试 —— YouTube 的 ``maxresdefault`` 不是每个
        视频都有，第一个下不下来就退到下一个。
        """
        return _cover(self.transport_of(movie.source), movie)


def _cover(transport: Transport, movie: MovieInfo) -> bytes:
    """封面按 :attr:`MovieInfo.cover_urls` 的顺序试，**第一个能下来的就用它**。

    :raises MovieImportError: 一个都没下来（或这个影片压根没有封面地址）。
    """
    problem: MovieImportError | None = None
    for url in movie.cover_urls:
        try:
            return transport.get_bytes(url, referer=movie.page_url)
        except MovieImportError as exc:
            problem = exc
    raise problem or MovieImportError(f"{movie.code} 没有封面图。")


__all__ = [
    "HttpSources",
    "MovieSource",
    "movie_url",
    "parse_ref",
    "source_of",
]
