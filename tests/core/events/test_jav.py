"""``gallery.core.events.jav``：按番号抓取与解析（ADR 0063）。

**不联网**：页面片段按真实站点的形状手写，传输用假实现 —— 站点结构一变，改的是解析规则，
而这几条用例盯的正是「按现在这份形状能读出什么」。
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import jav

#: 搜索页里那一条结果卡片（形状照 JavDB 的搜索结果）。
SEARCH_HTML = """
<div class="movie-list">
  <div class="item">
    <a href="/v/Arbam" class="box">
      <img src="https://c0.example.com/covers/ar/Arbam.jpg">
      <div class="video-title"><strong>IPX-660</strong> 一个标题</div>
      <div class="meta">2021-05-13</div>
    </a>
  </div>
</div>
"""

#: 详情页（形状照 JavDB 的影片页：每个字段一个 ``panel-block``）。
DETAIL_HTML = """
<h2 class="title is-4"><strong class="current-title">一个标题</strong></h2>
<img class="video-cover" width="600" src="https://c0.example.com/covers/ar/Arbam.jpg">
<div class="panel-block"><strong>番號:</strong> IPX -660</div>
<div class="panel-block"><strong>日期:</strong> 2021-05-13</div>
<div class="panel-block"><strong>時長:</strong> 170 分鍾</div>
<div class="panel-block"><strong>導演:</strong> キョウセイ</div>
<div class="panel-block"><strong>片商:</strong> IDEA POCKET</div>
<div class="panel-block"><strong>系列:</strong> 何発ヤッても…</div>
<div class="panel-block"><strong>評分:</strong> 4.59分, 由5664人評價</div>
<div class="panel-block"><strong>類別:</strong> 單體作品 , 放尿 , 口交</div>
<div class="panel-block"><strong>演員:</strong> 桃乃木かな , 武田大樹</div>
</nav>
"""


class FakeSource:
    """按 URL 返回预先备好的页面 / 字节（:class:`jav.JavSource` 的假实现）。"""

    def __init__(self, pages: dict[str, str], images: dict[str, bytes] | None = None) -> None:
        self._pages = pages
        self._images = images or {}
        self.text_urls: list[str] = []
        self.byte_urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.text_urls.append(url)
        return self._pages[url]

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:
        self.byte_urls.append(url)
        return self._images[url]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ipx660", "IPX-660"),
        ("IPX_660", "IPX-660"),
        (" IPX-660 ", "IPX-660"),
        ("ＩＰＸ６６０", "IPX-660"),
        ("", ""),
    ],
)
def test_the_code_is_normalized(raw: str, expected: str) -> None:
    assert jav.normalize_code(raw) == expected


def test_the_movie_url_appends_a_lowercase_code() -> None:
    assert (
        jav.movie_url("https://jable.tv/videos/", "IPX-660") == "https://jable.tv/videos/ipx-660/"
    )
    # 前后多余的空格与斜杠都不影响。
    assert (
        jav.movie_url(" https://jable.tv/videos ", "ipx-660") == "https://jable.tv/videos/ipx-660/"
    )


def test_the_search_page_lists_its_hits() -> None:
    hits = jav.parse_search_items(SEARCH_HTML, jav.DEFAULT_SOURCE_BASE)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.id == "IPX-660"
    assert hit.url == "https://javdb.com/v/Arbam"
    assert hit.cover == "https://c0.example.com/covers/ar/Arbam.jpg"
    assert hit.date == "2021-05-13"


def test_locate_takes_the_exact_code() -> None:
    hits = jav.parse_search_items(SEARCH_HTML, jav.DEFAULT_SOURCE_BASE)
    assert jav.locate(hits, "IPX-660").url == "https://javdb.com/v/Arbam"


def test_locate_reports_a_missing_code() -> None:
    with pytest.raises(MovieImportError):
        jav.locate((), "IPX-660")


def test_the_detail_page_yields_a_movie() -> None:
    movie = jav.parse_detail(DETAIL_HTML, "IPX-660", base=jav.DEFAULT_SOURCE_BASE)
    assert movie.code == "IPX-660"
    assert movie.title == "一个标题"
    assert movie.cover_url == "https://c0.example.com/covers/ar/Arbam.jpg"
    assert movie.release_date == "2021-05-13"
    assert movie.duration_minutes == 170
    assert movie.studio == "IDEA POCKET"
    assert movie.director == "キョウセイ"
    assert movie.actors == ("桃乃木かな", "武田大樹")
    assert movie.tags == ("單體作品", "放尿", "口交")
    assert movie.rating == 4.59


def test_the_draft_matches_the_movie_event_fields() -> None:
    movie = jav.parse_detail(DETAIL_HTML, "IPX-660", base=jav.DEFAULT_SOURCE_BASE)
    values = movie.to_draft().values
    assert values["title"] == "一个标题"
    assert values["code"] == "IPX-660"
    assert values["started_on"] == "2021-05-13"
    assert values["duration"] == "170"
    assert values["actors"] == "桃乃木かな、武田大樹"
    assert values["tags"] == "單體作品、放尿、口交"
    assert values["rating"] == "4.59"


def test_fetch_movie_walks_search_then_detail() -> None:
    source = FakeSource(
        {
            "https://javdb.com/search?f=all&q=IPX-660": SEARCH_HTML,
            "https://javdb.com/v/Arbam": DETAIL_HTML,
        }
    )
    movie = jav.fetch_movie("ipx660", client=source)
    assert movie.code == "IPX-660"
    assert movie.title == "一个标题"
    assert source.text_urls[0].endswith("search?f=all&q=IPX-660")


def test_fetch_movie_refuses_a_page_without_a_cover() -> None:
    no_cover_search = SEARCH_HTML.replace(
        '<img src="https://c0.example.com/covers/ar/Arbam.jpg">', ""
    )
    no_cover_detail = DETAIL_HTML.replace(
        '<img class="video-cover" width="600" src="https://c0.example.com/covers/ar/Arbam.jpg">',
        "",
    )
    source = FakeSource(
        {
            "https://javdb.com/search?f=all&q=IPX-660": no_cover_search,
            "https://javdb.com/v/Arbam": no_cover_detail,
        }
    )
    with pytest.raises(MovieImportError):
        jav.fetch_movie("IPX-660", client=source)


# ---- 网络那一半：断了要重试，读到的不是搜索结果页要说清是哪一种（ADR 0064）----------


class _FakeResponse:
    """一个够用的响应对象：``urlopen`` 的上下文管理器协议 + ``read`` / ``geturl``。"""

    headers = {"Content-Encoding": None}

    def __init__(self, body: bytes = b"<html>ok</html>") -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return "https://javdb.com/x"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """退避等待在测试里没有意义（真等就是几秒），而重试次数与判定才是要验的。"""
    monkeypatch.setattr(jav.time, "sleep", lambda _seconds: None)


def test_a_connection_dropped_mid_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """限速与反爬常常直接掐连接（``RemoteDisconnected`` / ``IncompleteRead``）。

    它们**不是** ``URLError`` 的子类，原先会一路传递到界面那句「出现了一个未预期的问题」——
    而重试一次多半就好了。
    """
    _no_sleep(monkeypatch)
    calls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    raw, _ = jav.JavHttp(delay=0, retries=3)._get("https://javdb.com/x", None)

    assert raw == b"<html>ok</html>"
    assert len(calls) == 2


def test_a_half_read_body_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """正文说到一半就没了（``IncompleteRead``，连 ``OSError`` 都不是）也算网络抖动。"""
    _no_sleep(monkeypatch)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise http.client.IncompleteRead(b"half", 100)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(MovieImportError) as excinfo:
        jav.JavHttp(delay=0, retries=2)._get("https://javdb.com/x", None)

    assert "稍后再试" in excinfo.value.message
    assert "IncompleteRead" in excinfo.value.message


def test_a_truncated_body_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Content-Encoding: gzip`` 而正文被截断：解压炸掉，也是等一会儿再试的那一种。"""
    _no_sleep(monkeypatch)
    calls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        calls.append(request.full_url)
        if len(calls) == 1:
            response = _FakeResponse(b"not gzip at all")
            response.headers = {"Content-Encoding": "gzip"}
            return response
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    raw, _ = jav.JavHttp(delay=0, retries=3)._get("https://javdb.com/x", None)

    assert raw == b"<html>ok</html>"
    assert len(calls) == 2


def test_a_search_page_with_no_results_at_all_says_which_kind_it_is() -> None:
    """源站那一段「暫無內容」在不在，决定说的是「换番号」还是「等一会儿再试」。"""
    empty = jav.no_search_results('<div class="empty-message">暫無內容</div>', "ZZZZ-99999")
    blocked = jav.no_search_results("<html><body>请稍候…</body></html>", "PRED-375")

    assert "没有这个番号" in empty.message
    assert "限速" in blocked.message


def test_fetch_movie_tells_a_blocked_search_page_from_a_missing_code() -> None:
    """抓取这一层也要把两种情形分开 —— 「在源站查不到番号」会把用户支到换番号那条路上。"""
    source = FakeSource(
        {"https://javdb.com/search?f=all&q=PRED-375": "<html><body>请稍候…</body></html>"}
    )

    with pytest.raises(MovieImportError) as excinfo:
        jav.fetch_movie("PRED-375", client=source)

    assert "限速" in excinfo.value.message
