"""影片是「哪儿的哪一个」：编号怎么认、按来源分派抓取、打开哪一页（ADR 0068）。

用户粘进来的是一坨自由文本，每一行可能是番号、是链接、也可能是一句抱怨。识别规则按**可靠性**
排序（ADR 0068 决策 3）：认网址 → 认 ``BV`` / ``av`` 这两种不可能与番号撞的形状 → 认 11 位的
videoId（把番号那个形状排除在外）→ 剩下的当番号。**识别是纯函数**，不联网 —— 所以 ``b23.tv``
那种要展开的短链在这里只能给一句中文。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

from gallery.core.errors import MovieImportError
from gallery.core.events import bilibili, jav, youtube
from gallery.core.events.kinds import SOURCE_BILIBILI, SOURCE_JAV, SOURCE_YOUTUBE
from gallery.core.events.models import MovieInfo, MovieRef
from gallery.core.events.transport import Transport

#: 像网址的东西。**带不带的 ``scheme`` 都算**（用户从地址栏复制时多半带着，手打时多半不带）。
_URL_RE: Final = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?[\w-]+(?:\.[\w-]+)+(?:[/:?#]|$)", re.I)


def source_of(text: str) -> str:
    """事件里「来源」那一栏的值。**空的当 JavDB 来的**。

    两种情形会没填：早于 ADR 0068 的影片事件（迁移已经回填过，但代码里手写的事件草稿不在其中，
    例如 ``EventDraft.of(code="IPX-660")``）。那时能打开的只有 JavDB 那一页 —— 这个库本来
    就只有它一个来路。
    """
    return (text or "").strip() or SOURCE_JAV


def parse_ref(text: str) -> MovieRef:
    """把用户敲的一行读成「哪儿的哪一个」。

    :raises MovieImportError: 空行，或者认不出来（例如 ``b23.tv`` 短链）。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise MovieImportError("编号或链接不能为空。")
    if _URL_RE.match(cleaned):
        return _ref_from_url(cleaned)
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
    video_id = youtube.id_from_url(url)
    if video_id:
        return MovieRef(source=SOURCE_YOUTUBE, code=video_id)
    code = bilibili.id_from_url(url)
    if code:
        return MovieRef(source=SOURCE_BILIBILI, code=code)
    raise MovieImportError(
        f"这个地址认不出来：{url}。B 站的短链请先在浏览器里打开，把完整地址粘进来。"
    )


def movie_url(ref: MovieRef, *, jav_site: str) -> str | None:
    """这个影片该打开的网址；**来源认不出就是 ``None``**。

    ``jav_site`` 只管 JavDB 那一栏（用户可以把它换成 jable.tv 之类的镜像）；YouTube 与 B 站的
    地址形状是固定的，没有可配置的余地。**B 站那一页带上 ``t``**，于是打开时从开头播放，
    而不是接上 B 站自己的「上次播放位置」。
    """
    if ref.source == SOURCE_JAV:
        return jav.movie_url(jav_site, ref.code)
    if ref.source == SOURCE_YOUTUBE:
        return youtube.movie_url(ref.code)
    if ref.source == SOURCE_BILIBILI:
        return bilibili.movie_url(ref.code, start_at_beginning=True)
    return None


class MovieSource(Protocol):
    """抓取影片的入口。:class:`HttpSources` 是真实实现，测试传假实现（**不必联网**）。

    ``cover_bytes`` 与 ``fetch`` 分开：抓元数据要联网解析，下封面只是取字节 —— 而**封面写盘是
    调用方的事**（那要写进用户的文件夹，见 ``Library.import_movies``）。
    """

    def fetch(self, ref: MovieRef) -> MovieInfo: ...

    def cover_bytes(self, movie: MovieInfo) -> bytes: ...


#: 来源 → 抓取那一份元数据的函数。三个模块的函数签名是同一个形状（``code`` + ``client=``），
#: 所以分派是一张表而不是三行 ``if``。
_FETCHERS: Final[dict[str, Callable[..., MovieInfo]]] = {
    SOURCE_JAV: jav.fetch,
    SOURCE_YOUTUBE: youtube.fetch,
    SOURCE_BILIBILI: bilibili.fetch,
}


def _default_transports() -> dict[str, Transport]:
    """三条传输。**每个来源只在自己的 ``base`` / ``Referer`` / cookie 上不同。**"""
    return {
        SOURCE_JAV: jav.transport(),
        SOURCE_YOUTUBE: youtube.transport(),
        SOURCE_BILIBILI: bilibili.transport(),
    }


@dataclass
class HttpSources:
    """三个来源各一条传输，按 ``ref.source`` 分派。

    **三条传输活在同一份对象里**：限速的「上一次请求是什么时候」记在每条传输上，所以一次导入里的
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
