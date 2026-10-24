"""``gallery.core.events.iqiyi``：qipu 编号、tvid、解析、抓取（ADR 0082）。

**不联网**：传输是假的，喂的是实测抓回来的那两段响应的形状。这一家最要紧的两条：
地址里那串 qipu 编号接口不认（要先去页面里把数字 tvid 读出来），以及封面的大图靠在地址后面
接一个尺寸段。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events import iqiyi

#: 移动页里的那一段状态。**真实的页面里它前面还有一大堆别的东西**。
PAGE = (
    '<script>window.__INITIAL_STATE__={"videoInfo":{"shortTitle":"沙丘",'
    '"period":"20211022","tvid":2006461700,"aid":2006461700}}</script>'
)

#: ``playervideoinfo`` 的响应（实测字段，略去用不上的）。
VIDEO = {
    "code": "A00000",
    "data": {
        "vn": "沙丘",
        "vpic": "http://pic2.iqiyipic.com/image/20231110/c9/4d/v_125590472_m_601_m9.jpg",
        "vu": "http://www.iqiyi.com/v_19rqxr146g.html",
        "an": "沙丘",
        "s": "沙丘",
        "tg": "战争 科幻 奇幻 英语 美国 院线",
        "issueTime": 20211022,
        "plg": 9314,
        "payMark": 1,
    },
}


class FakeTransport:
    def __init__(self, *, page: str | Exception = PAGE) -> None:
        self.page = page
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if "playervideoinfo" in url:
            return json.dumps(VIDEO, ensure_ascii=False)
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize("text", ["19rqxr146g", "xkt6z3z798", "ilvuaaq0cs"])
def test_a_qipu_code_is_alphanumeric(text: str) -> None:
    assert iqiyi.is_qipu(text)
    assert iqiyi.id_from_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "2006461700",  # 纯数字是 tvid，拼不出网址
        "19rqxr1",  # 太短
        "IPX-660",
        "dQw4w9WgXcQ",
    ],
)
def test_other_shapes_are_not_qipu_codes(text: str) -> None:
    assert not iqiyi.is_qipu(text)
    assert iqiyi.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.iqiyi.com/v_19rqxr146g.html", "19rqxr146g"),
        ("https://m.iqiyi.com/v_xkt6z3z798.html", "xkt6z3z798"),
        ("https://www.iqiyi.com/a_1fkgtbddd2x.html", None),  # 专辑页没有具体某一集
        ("https://www.youku.com/v_19rqxr146g.html", None),
    ],
)
def test_a_url_yields_the_qipu_code(url: str, expected: str | None) -> None:
    assert iqiyi.id_from_url(url) == expected


def test_the_view_gives_everything_the_payload_has() -> None:
    movie = iqiyi.view_of(VIDEO, "19rqxr146g")

    assert movie.source == "爱奇艺"
    assert movie.code == "19rqxr146g"
    assert movie.title == "沙丘"
    assert movie.release_date == "2021-10-22"
    assert movie.duration_minutes == 155  # 9314 秒
    assert movie.tags == ("战争", "科幻", "奇幻", "英语", "美国", "院线")
    assert movie.page_url == "https://www.iqiyi.com/v_19rqxr146g.html"


def test_the_series_is_empty_when_the_album_is_the_video_itself() -> None:
    """单片电影的专辑名就是片名，填进「系列」只会让分组多一堆一集一项的组。"""
    assert iqiyi.view_of(VIDEO, "19rqxr146g").series == ""

    episodic = {**VIDEO, "data": {**VIDEO["data"], "vn": "狂飙第1集", "an": "狂飙"}}
    assert iqiyi.view_of(episodic, "xkt6z3z798").series == "狂飙"


def test_the_cover_asks_for_the_large_variant_and_upgrades_to_https() -> None:
    cover = iqiyi.view_of(VIDEO, "19rqxr146g").cover_urls

    assert cover == (
        "https://pic2.iqiyipic.com/image/20231110/c9/4d/v_125590472_m_601_m9_1280_720.jpg",
    )


def test_the_tvid_is_read_out_of_the_rendered_page() -> None:
    assert iqiyi.tvid_of(PAGE) == "2006461700"
    assert iqiyi.tvid_of("<html></html>") is None


def test_the_api_saying_parameter_error_lands_on_a_chinese_sentence() -> None:
    """``A00001`` 是接口对「参数不对」的统称 —— 不存在的 tvid 也是这一句。"""
    with pytest.raises(MovieImportError) as excinfo:
        iqiyi.view_of({"code": "A00001", "data": "参数错误"}, "19rqxr146g")

    assert "爱奇艺" in excinfo.value.message


def test_fetch_walks_the_page_then_the_api() -> None:
    transport = FakeTransport()

    movie = iqiyi.fetch("19rqxr146g", client=transport)

    assert transport.urls[0] == "https://m.iqiyi.com/v_19rqxr146g.html"
    assert "tvid=2006461700" in transport.urls[1]
    assert movie.title == "沙丘"


def test_a_page_without_a_tvid_says_what_to_do() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        iqiyi.fetch("19rqxr146g", client=FakeTransport(page="<html></html>"))

    assert "单集" in excinfo.value.message


def test_a_missing_page_is_told_apart_from_a_broken_one() -> None:
    """404 = 没有这个视频（换一条）；别的失败原样报出去（等一会儿再试）。"""
    with pytest.raises(MovieImportError) as excinfo:
        iqiyi.fetch(
            "19rqxr146g",
            client=FakeTransport(page=SourcePageError(404, "https://m.iqiyi.com/v_x.html")),
        )
    assert "没有这个视频" in excinfo.value.message

    with pytest.raises(SourcePageError):
        iqiyi.fetch(
            "19rqxr146g",
            client=FakeTransport(page=SourcePageError(403, "https://m.iqiyi.com/v_x.html")),
        )


def test_a_line_that_is_not_a_qipu_code_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        iqiyi.fetch("2006461700", client=transport)

    assert transport.urls == []
