"""``gallery.core.events.sources``：编号怎么认、按来源分派、打开哪一页（ADR 0068）。

识别那几条规则是**顺序**的一部分（先网址 → ``BV`` / ``av`` → 11 位 videoId → 番号），
所以这一份盯的是「同样一行文字会不会被派给错的那个来源」—— 派错了不会报错，只会抓回一片空白。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import jav, sources
from gallery.core.events.kinds import (
    SOURCE_BILIBILI,
    SOURCE_IQIYI,
    SOURCE_JAV,
    SOURCE_MANGO,
    SOURCE_TENCENT,
    SOURCE_YOUKU,
    SOURCE_YOUTUBE,
)
from gallery.core.events.models import MovieRef

URL = "https://jable.tv/videos/"

#: 搜索页 / 详情页片段（形状照 JavDB，与 ``test_jav`` 各自独立）。
SEARCH_HTML = (
    '<a href="/v/Arbam" class="box">'
    '<img src="https://c0.example.com/covers/ar/Arbam.jpg">'
    '<div class="video-title"><strong>IPX-660</strong> 一个标题</div>'
    '<div class="meta">2021-05-13</div></a>'
)
DETAIL_HTML = (
    '<strong class="current-title">一个标题</strong>'
    '<img class="video-cover" src="https://c0.example.com/covers/ar/Arbam.jpg">'
    '<div class="panel-block"><strong>番號:</strong> IPX-660</div>'
    "</nav>"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IPX-660", MovieRef(SOURCE_JAV, "IPX-660")),
        ("ipx660", MovieRef(SOURCE_JAV, "IPX-660")),
        ("ABCDE-12345", MovieRef(SOURCE_JAV, "ABCDE-12345")),
        ("https://jable.tv/videos/ipx-660/", None),  # 那不是三个来源里的任何一个
        ("BV1GJ411x7h7", MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7")),
        ("av80433022", MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7")),
        (
            "https://www.bilibili.com/video/BV1GJ411x7h7/",
            MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7"),
        ),
        (
            "https://www.bilibili.com/video/av80433022?p=2",
            MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7"),
        ),
        ("dQw4w9WgXcQ", MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ")),
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL0",
            MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ"),
        ),
        ("https://youtu.be/dQw4w9WgXcQ?t=1", MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ")),
        ("youtube.com/shorts/dQw4w9WgXcQ", MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ")),
        (
            "https://v.qq.com/x/page/d4102jjf5cm.html",
            MovieRef(SOURCE_TENCENT, "d4102jjf5cm"),
        ),
        (
            "https://v.qq.com/x/cover/mzc002001bk7moh/d4102jjf5cm.html",
            MovieRef(SOURCE_TENCENT, "d4102jjf5cm"),
        ),
        (
            "https://www.iqiyi.com/v_19rqxr146g.html",
            MovieRef(SOURCE_IQIYI, "19rqxr146g"),
        ),
        (
            "https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA==.html",
            MovieRef(SOURCE_YOUKU, "XNjQ3MjkwNTUwNA"),
        ),
        (
            "https://www.mgtv.com/b/336727/14544857.html",
            MovieRef(SOURCE_MANGO, "336727/14544857"),
        ),
    ],
)
def test_a_line_is_read_as_a_source_and_a_code(text: str, expected: MovieRef | None) -> None:
    if expected is None:
        with pytest.raises(MovieImportError):
            sources.parse_ref(text)
        return
    assert sources.parse_ref(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "https://b23.tv/abcd", "https://vimeo.com/12345"])
def test_a_line_that_cannot_be_read_says_so(text: str) -> None:
    """认不出来要给一句中文，**不许猜**：猜错的那一次会抓回一片空白，用户看不出哪里不对。"""
    with pytest.raises(MovieImportError):
        sources.parse_ref(text)


def test_a_short_link_is_told_apart_from_an_unknown_site() -> None:
    """``b23.tv`` 要展开才认得出，而识别这一步不联网 —— 提示要说清该怎么办。"""
    with pytest.raises(MovieImportError) as excinfo:
        sources.parse_ref("https://b23.tv/abcd")

    assert "短链" in excinfo.value.message


def test_an_eleven_character_code_shaped_like_a_number_stays_a_number() -> None:
    """``ABCDE-12345`` 也是 11 位，但长成番号 —— 它必须留给 JavDB（ADR 0068 决策 3）。

    这条是识别规则里最容易翻车的一条：判错了不会报错，只会拿着一个编号去 YouTube 上找。
    """
    assert sources.parse_ref("ABCDE-12345").source == SOURCE_JAV
    assert sources.parse_ref("dQw4w9WgXcQ").source == SOURCE_YOUTUBE


@pytest.mark.parametrize(
    ("text", "source", "expected"),
    [
        # 腾讯的 vid 与 YouTube 的 videoId 同形 —— 只凭形状会认成 YouTube（ADR 0082 决策 2）。
        ("d4102jjf5cm", None, MovieRef(SOURCE_YOUTUBE, "d4102jjf5cm")),
        ("d4102jjf5cm", SOURCE_TENCENT, MovieRef(SOURCE_TENCENT, "d4102jjf5cm")),
        ("d4102jjf5cm", SOURCE_YOUTUBE, MovieRef(SOURCE_YOUTUBE, "d4102jjf5cm")),
        # 纯数字在芒果与爱奇艺那里都可能是编号，面板说了算。
        ("14544857", SOURCE_MANGO, MovieRef(SOURCE_MANGO, "14544857")),
        ("19rqxr146g", SOURCE_IQIYI, MovieRef(SOURCE_IQIYI, "19rqxr146g")),
        ("XNjQ3MjkwNTUwNA==", SOURCE_YOUKU, MovieRef(SOURCE_YOUKU, "XNjQ3MjkwNTUwNA")),
    ],
)
def test_the_chosen_source_decides_an_ambiguous_bare_code(
    text: str, source: str | None, expected: MovieRef
) -> None:
    """网址优先，**裸编号才看面板选的来源**：粘错站的链接仍按粘的那一站认。"""
    assert sources.parse_ref(text, source=source) == expected


def test_a_url_still_wins_over_the_source_the_panel_offers() -> None:
    """在「导入腾讯视频」里粘一条 B 站链接，认出来的还是 B 站 —— 网址比面板上的选择可靠。"""
    assert sources.parse_ref(
        "https://www.bilibili.com/video/BV1GJ411x7h7/", source=SOURCE_TENCENT
    ) == MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7")


def test_an_album_address_is_turned_away_with_a_usable_hint() -> None:
    """整部剧的合集页没有具体某一集，认不出 —— 提示要说清「点开某一集再复制」。"""
    with pytest.raises(MovieImportError) as excinfo:
        sources.parse_ref("https://www.iqiyi.com/a_1fkgtbddd2x.html")

    assert "合集" in excinfo.value.message


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", SOURCE_JAV),
        ("   ", SOURCE_JAV),
        ("YouTube", SOURCE_YOUTUBE),
        ("Bilibili", SOURCE_BILIBILI),
    ],
)
def test_an_empty_source_means_javdb(text: str, expected: str) -> None:
    """没填来源的事件（手写的草稿、以及早于 ADR 0068 的那些）当 JavDB 来的。"""
    assert sources.source_of(text) == expected


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (MovieRef(SOURCE_JAV, "IPX-660"), "https://jable.tv/videos/ipx-660/"),
        (MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ"), "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        (
            MovieRef(SOURCE_BILIBILI, "BV1GJ411x7h7"),
            "https://www.bilibili.com/video/BV1GJ411x7h7/?t=0.001",
        ),
        (
            MovieRef(SOURCE_TENCENT, "d4102jjf5cm"),
            "https://v.qq.com/x/page/d4102jjf5cm.html",
        ),
        (
            MovieRef(SOURCE_IQIYI, "19rqxr146g"),
            "https://www.iqiyi.com/v_19rqxr146g.html",
        ),
        (
            MovieRef(SOURCE_YOUKU, "XNjQ3MjkwNTUwNA"),
            "https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA.html",
        ),
        (
            MovieRef(SOURCE_MANGO, "294541/3779644"),
            "https://www.mgtv.com/b/294541/3779644.html",
        ),
    ],
)
def test_each_source_opens_its_own_page(ref: MovieRef, expected: str) -> None:
    assert sources.movie_url(ref, jav_site=URL) == expected


def test_an_unknown_source_opens_nothing() -> None:
    assert sources.movie_url(MovieRef("Nope", "x"), jav_site=URL) is None


class FakeTransport:
    """按 URL 返回预先备好的页面。**这一份只服务 JavDB 那一条**。"""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if "search?f=all" in url:
            return SEARCH_HTML
        return DETAIL_HTML

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:
        self.urls.append(url)
        if url.endswith("first.jpg"):
            raise MovieImportError("这一张下不下来。")
        return b"cover"


def test_the_http_sources_route_by_source() -> None:
    transport = FakeTransport()
    fetchers = sources.HttpSources(transports={SOURCE_JAV: transport})

    movie = fetchers.fetch(MovieRef(SOURCE_JAV, "IPX-660"))

    assert movie.source == SOURCE_JAV
    assert movie.code == "IPX-660"
    assert transport.urls[0].endswith("search?f=all&q=IPX-660")


def test_a_source_we_have_no_transport_for_says_so() -> None:
    """面板的下拉框理论上给不出别的来源，但库里的值可能是手改过的 —— 那时也要有一句中文。"""
    fetchers = sources.HttpSources(transports={})

    with pytest.raises(MovieImportError):
        fetchers.fetch(MovieRef(SOURCE_YOUTUBE, "dQw4w9WgXcQ"))
    with pytest.raises(MovieImportError):
        fetchers.cover_bytes(jav.parse_detail(DETAIL_HTML, "IPX-660", base=jav.DEFAULT_SOURCE_BASE))


def test_the_cover_falls_through_to_the_next_candidate() -> None:
    """``cover_urls`` 按顺序试：YouTube 的 ``maxresdefault`` 不是每个视频都有（0068 决策 6）。"""
    transport = FakeTransport()
    movie = jav.parse_detail(DETAIL_HTML, "IPX-660", base=jav.DEFAULT_SOURCE_BASE)
    fetchers = sources.HttpSources(transports={SOURCE_JAV: transport})

    assert (
        fetchers.cover_bytes(
            replace(
                movie, cover_urls=("https://c.example/first.jpg", "https://c.example/second.jpg")
            )
        )
        == b"cover"
    )
    assert transport.urls[-1] == "https://c.example/second.jpg"
