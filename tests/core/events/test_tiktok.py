"""``gallery.core.events.tiktok``：视频 id、分享链接与 oEmbed（ADR 0085）。

**不联网**：传输是假的。oEmbed 一次给出标题、作者与封面，短链也靠它换编号；网页那一刀
只补时长、发布日期与话题，失败了不该让整部影片导不进来。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events import tiktok

ID = "6718335390845095173"
COVER = "https://p16.muscdn.com/obj/a.jpg"
PAGE_COVER = "https://p16-common-sign.tiktokcdn.com/cover.jpg"

OEMBED = {
    "title": "一个视频标题",
    "author_name": "Scout",
    "author_url": "https://www.tiktok.com/@scout2015",
    "thumbnail_url": COVER,
    "embed_product_id": ID,
}

ITEM = {
    "id": ID,
    "desc": "一个视频标题",
    "createTime": "1590000000",
    "author": {"uniqueId": "scout2015", "nickname": "Scout"},
    "video": {"duration": 62, "cover": PAGE_COVER},
    "challenges": [{"title": "fyp"}, {"title": "fyp"}, {"title": "旅行"}],
}

PAGE = (
    '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
    + json.dumps(
        {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": ITEM}}}},
        ensure_ascii=False,
    )
    + "</script></html>"
)


class FakeTransport:
    """oEmbed 与网页各返回预先备好的那一份。**这一份只服务一部影片**。"""

    def __init__(
        self,
        *,
        oembed: dict[str, object] | None = OEMBED,
        page: str = PAGE,
        oembed_failure: Exception | None = None,
        page_failure: Exception | None = None,
    ) -> None:
        self.oembed = oembed
        self.page = page
        self.oembed_failure = oembed_failure
        self.page_failure = page_failure
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if "/oembed" in url:
            if self.oembed_failure is not None:
                raise self.oembed_failure
            return json.dumps(self.oembed or {}, ensure_ascii=False)
        if self.page_failure is not None:
            raise self.page_failure
        return self.page

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")

    def get_final_url(self, url: str, *, referer: str | None = None) -> str:  # pragma: no cover
        raise AssertionError("TikTok 靠 oEmbed 换编号，不跟跳转")


@pytest.mark.parametrize(("text", "expected"), [(ID, ID), ("  6718335390845095173 ", ID)])
def test_a_bare_code_is_a_video_id(text: str, expected: str) -> None:
    assert tiktok.id_from_text(text) == expected


@pytest.mark.parametrize("text", ["", "IPX-660", "ZMabc123", "123"])
def test_other_shapes_are_not_video_ids(text: str) -> None:
    assert tiktok.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.tiktok.com/@scout2015/video/{ID}", ID),
        (f"https://www.tiktok.com/@scout2015/photo/{ID}", ID),
        (f"https://m.tiktok.com/v/{ID}.html", ID),
        (f"https://www.tiktok.com/embed/v2/{ID}", ID),
        (f"https://www.tiktok.com/player/v1/{ID}", ID),
        ("https://vm.tiktok.com/ZMabc123/", "ZMabc123"),
        ("https://vt.tiktok.com/ZSabc123/", "ZSabc123"),
        ("https://www.tiktok.com/t/ZTabc123/", "ZTabc123"),
        ("https://www.tiktok.com/@scout2015", None),
        ("https://www.example.com/@scout2015/video/6718335390845095173", None),
    ],
)
def test_a_url_yields_an_id_or_a_share_token(url: str, expected: str | None) -> None:
    assert tiktok.id_from_url(url) == expected


def test_the_page_address_uses_the_placeholder_handle() -> None:
    assert tiktok.movie_url(ID) == f"https://www.tiktok.com/@i/video/{ID}"


def test_oembed_gives_the_title_author_and_cover() -> None:
    movie = tiktok.oembed_of(OEMBED, ID)

    assert movie.source == "TikTok"
    assert movie.code == ID
    assert movie.title == "一个视频标题"
    assert movie.cover_urls == (COVER,)
    assert movie.creator == "Scout"
    assert movie.page_url == f"https://www.tiktok.com/@i/video/{ID}"


def test_the_page_gives_the_rest() -> None:
    movie = tiktok.with_item(tiktok.oembed_of(OEMBED, ID), tiktok.item_of(PAGE))

    assert movie.release_date == "2020-05-20"
    assert movie.duration_minutes == 1
    assert movie.tags == ("fyp", "旅行")


def test_the_page_fills_title_and_cover_when_oembed_only_returns_the_id() -> None:
    """oEmbed 有时只回 ``embed_product_id``，标题与封面只在网页那份 ``itemStruct`` 里。"""
    movie = tiktok.with_item(tiktok.oembed_of({"embed_product_id": ID}, ID), tiktok.item_of(PAGE))

    assert movie.title == "一个视频标题"
    assert movie.cover_urls == (PAGE_COVER,)


def test_a_page_without_the_universal_data_is_empty() -> None:
    assert tiktok.item_of("<html><body>验证码</body></html>") == {}


def test_fetch_asks_oembed_and_then_the_page() -> None:
    transport = FakeTransport()

    movie = tiktok.fetch(ID, client=transport)

    assert transport.urls[0].startswith(f"{tiktok.OEMBED}?url=")
    assert transport.urls[1] == f"https://www.tiktok.com/@scout2015/video/{ID}"
    assert movie.code == ID
    assert movie.release_date == "2020-05-20"


def test_the_page_failing_leaves_the_oembed_fields_intact() -> None:
    transport = FakeTransport(page_failure=MovieImportError("被挡住了"))

    movie = tiktok.fetch(ID, client=transport)

    assert movie.title == "一个视频标题"
    assert movie.release_date == ""


def test_a_share_link_is_resolved_by_oembed() -> None:
    transport = FakeTransport()

    movie = tiktok.fetch("ZMabc123", client=transport)

    assert transport.urls[0] == (f"{tiktok.OEMBED}?url=https%3A%2F%2Fvm.tiktok.com%2FZMabc123%2F")
    assert movie.code == ID


def test_a_missing_video_is_translated_into_chinese() -> None:
    transport = FakeTransport(oembed_failure=SourcePageError(404, "https://www.tiktok.com/oembed"))

    with pytest.raises(MovieImportError) as excinfo:
        tiktok.fetch(ID, client=transport)

    assert "TikTok 上没有这个视频" in excinfo.value.message


def test_a_line_that_is_not_a_code_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        tiktok.fetch("a b", client=transport)

    assert transport.urls == []
