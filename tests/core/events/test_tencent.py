"""``gallery.core.events.tencent``：vid、解析、抓取（ADR 0082）。

**不联网**：传输是假的，喂的是实测抓回来的那几段响应的形状。重点盯三件事：封面由 vid 拼出来
（不是从页面里读的）、日期与标签来自那一页、以及接口报的错要落成一句中文。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import tencent

#: ``getinfo`` 的响应：外面裹着 ``QZOutputJson=``，时长写成字符串。
GETINFO = (
    'QZOutputJson={"em":0,"vl":{"cnt":1,"vi":[{"vid":"d4102jjf5cm","ti":"一个标题",'
    '"td":"82.8817","cid":"mzc002001bk7moh"}]}};'
)

#: 接口说没得看。
GETINFO_GONE = 'QZOutputJson={"em":61,"msg":"该内容暂不可观看"};'

#: 移动分享页里那几个 ``itemprop`` 标签（属性顺序与真实页面一致）。
PAGE = (
    '<meta itemprop="name" name="title" content="【腾讯视频】 一个标题">'
    '<meta itemprop="keywords" name="keywords" content="沈腾,白敬亭,综艺">'
    '<meta itemprop="image" content="//puui.qpic.cn/vpic_cover/d4102jjf5cm/d4102jjf5cm_hz.jpg/496">'
    '<meta itemprop="datePublished" content="2026-10-10">'
)


class FakeTransport:
    def __init__(self, *, page: str | Exception = PAGE) -> None:
        self.page = page
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if "getinfo" in url:
            return GETINFO
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize("text", ["d4102jjf5cm", "a3364znwdsm", "s00242sxrne", "x0033b5z0m3"])
def test_a_vid_is_eleven_lowercase_characters(text: str) -> None:
    assert tencent.is_vid(text)
    assert tencent.id_from_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "d4102jjf5c",  # 少一位
        "d4102jjf5cmx",  # 多一位
        "D4102jjf5cm",  # 大写不算：vid 只有小写
        "4102jjf5cm0",  # 首位必须是字母
        "IPX-660",
        "dQw4w9WgXcQ",  # 那是 YouTube 的
    ],
)
def test_other_shapes_are_not_vids(text: str) -> None:
    assert not tencent.is_vid(text)
    assert tencent.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://v.qq.com/x/cover/mzc002001bk7moh/d4102jjf5cm.html", "d4102jjf5cm"),
        ("https://v.qq.com/x/page/d4102jjf5cm.html", "d4102jjf5cm"),
        ("https://m.v.qq.com/play.html?vid=d4102jjf5cm", None),
        ("https://v.qq.com/x/cover/mzc002001bk7moh.html", None),  # 合集页没有具体某一集
        ("https://www.youtube.com/watch?v=d4102jjf5cm", None),
    ],
)
def test_a_url_yields_the_vid(url: str, expected: str | None) -> None:
    assert tencent.id_from_url(url) == expected


def test_the_view_gives_title_duration_and_a_cover_built_from_the_vid() -> None:
    movie = tencent.view_of(_payload(GETINFO), "d4102jjf5cm")

    assert movie.source == "腾讯视频"
    assert movie.code == "d4102jjf5cm"
    assert movie.title == "一个标题"
    assert movie.duration_minutes == 1  # 82.9 秒
    assert movie.cover_urls == (
        "https://puui.qpic.cn/vpic_cover/d4102jjf5cm/d4102jjf5cm_hz.jpg/1280",
    )
    assert movie.page_url == "https://v.qq.com/x/page/d4102jjf5cm.html"


def test_the_page_adds_the_release_date_and_the_tags() -> None:
    movie = tencent.page_of(PAGE, tencent.view_of(_payload(GETINFO), "d4102jjf5cm"))

    assert movie.release_date == "2026-10-10"
    assert movie.tags == ("沈腾", "白敬亭", "综艺")


def test_a_page_without_those_tags_leaves_them_empty() -> None:
    """**页面上少一样就少一样** —— 空页面不该把已经拿到的标题与封面弄丢。"""
    movie = tencent.page_of("<html></html>", tencent.view_of(_payload(GETINFO), "d4102jjf5cm"))

    assert movie.release_date == ""
    assert movie.tags == ()
    assert movie.title == "一个标题"


def test_the_play_api_saying_no_lands_on_a_chinese_sentence() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        tencent.view_of(_payload(GETINFO_GONE), "d4102jjf5cm")

    assert "下架" in excinfo.value.message


def test_an_unrecognized_error_code_is_reported_as_is() -> None:
    """认不出的码原样带上 —— 用户报问题时能照着搜。"""
    with pytest.raises(MovieImportError) as excinfo:
        tencent.view_of(_payload('QZOutputJson={"em":99,"msg":"怪事"}'), "d4102jjf5cm")

    assert "99" in excinfo.value.message and "怪事" in excinfo.value.message


def test_fetch_asks_the_api_then_the_page() -> None:
    transport = FakeTransport()

    movie = tencent.fetch("d4102jjf5cm", client=transport)

    assert len(transport.urls) == 2
    assert "getinfo" in transport.urls[0] and "vids=d4102jjf5cm" in transport.urls[0]
    assert transport.urls[1].startswith("https://m.v.qq.com/play.html")
    assert movie.release_date == "2026-10-10"


def test_a_broken_page_does_not_lose_the_title_and_the_cover() -> None:
    """那一刀是补的：站方改版只该让日期与标签空着（与 YouTube 那一支同一个取向）。"""
    transport = FakeTransport(page=MovieImportError("这一页无法解析。"))

    movie = tencent.fetch("d4102jjf5cm", client=transport)

    assert movie.title == "一个标题"
    assert movie.cover_urls
    assert movie.release_date == ""


def test_a_line_that_is_not_a_vid_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        tencent.fetch("IPX-660", client=transport)

    assert transport.urls == []


def _payload(raw: str) -> dict[str, object]:
    """把那层 JS 赋值剥掉，给解析函数一份 JSON。"""
    body = raw.strip()[len("QZOutputJson=") :].rstrip(";")
    payload = json.loads(body)
    assert isinstance(payload, dict)
    return payload
