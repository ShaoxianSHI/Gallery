"""``gallery.core.events.douyin``：aweme_id、分享短链与两处元数据（ADR 0085）。

**不联网**：传输是假的。分享短链要跟一次跳转才拿得到编号，所以 ``get_final_url`` 是单独
测的那一步；接口与分享页两条路各有一条用例，接口被挡时仍要导得进来。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events import douyin

ID = "7345678901234567890"
COVER = "https://p3.douyinpic.com/img/a.jpg"

ITEM = {
    "aweme_id": ID,
    "desc": "一个视频标题",
    "create_time": 1710000000,
    "author": {"nickname": "作者名字"},
    "video": {
        "duration": 61000,
        "origin_cover": {"url_list": [COVER]},
        "cover": {"url_list": ["https://p3.douyinpic.com/img/b.jpg"]},
    },
    "text_extra": [{"hashtag_name": "旅行"}, {"hashtag_name": "旅行"}, {"hashtag_name": "风景"}],
}

API_JSON = json.dumps({"status_code": 0, "item_list": [ITEM]}, ensure_ascii=False)

ROUTER_HTML = (
    "<html><script>window._ROUTER_DATA = "
    + json.dumps(
        {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [ITEM]}}}},
        ensure_ascii=False,
    )
    + ";</script></html>"
)


class FakeTransport:
    """按 URL 返回接口或分享页。**这一份只服务一部影片**。"""

    def __init__(
        self,
        *,
        api: str | None = API_JSON,
        page: str = ROUTER_HTML,
        shell: str = "<html>空壳</html>",
        final: str = f"https://www.douyin.com/video/{ID}",
        api_failure: Exception | None = None,
        page_failure: Exception | None = None,
    ) -> None:
        self.api = api
        self.page = page
        self.shell = shell
        self.final = final
        self.api_failure = api_failure
        self.page_failure = page_failure
        self.urls: list[str] = []
        self.final_urls: list[str] = []
        self.page_calls = 0

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if "iteminfo" in url:
            if self.api_failure is not None:
                raise self.api_failure
            return self.api or ""
        if self.page_failure is not None:
            raise self.page_failure
        self.page_calls += 1
        return self.shell if self.page_calls == 1 else self.page

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")

    def get_final_url(self, url: str, *, referer: str | None = None) -> str:
        self.final_urls.append(url)
        return self.final


@pytest.mark.parametrize(("text", "expected"), [(ID, ID), ("  7345678901234567890 ", ID)])
def test_a_bare_code_is_an_aweme_id(text: str, expected: str) -> None:
    assert douyin.id_from_text(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "IPX-660", "iRNBho6u", "123"])
def test_other_shapes_are_not_aweme_ids(text: str) -> None:
    assert douyin.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.douyin.com/video/{ID}", ID),
        (f"https://www.douyin.com/video/{ID}?previous_page=app_code_link", ID),
        (f"https://www.iesdouyin.com/share/video/{ID}/?region=CN", ID),
        (f"https://www.douyin.com/note/{ID}", ID),
        (f"https://www.douyin.com/user/MS4wLjAB?modal_id={ID}", ID),
        ("https://v.douyin.com/iRNBho6u/", "iRNBho6u"),
        ("https://www.douyin.com/user/MS4wLjAB", None),
        ("https://www.example.com/video/7345678901234567890", None),
    ],
)
def test_a_url_yields_an_id_or_a_share_token(url: str, expected: str | None) -> None:
    assert douyin.id_from_url(url) == expected


def test_the_page_address_is_the_video_form() -> None:
    assert douyin.movie_url(ID) == f"https://www.douyin.com/video/{ID}"


def test_the_item_gives_the_fields_it_offers() -> None:
    movie = douyin.movie_of(ITEM, ID)

    assert movie.source == "抖音"
    assert movie.code == ID
    assert movie.title == "一个视频标题"
    assert movie.cover_urls == (COVER,)
    assert movie.page_url == f"https://www.douyin.com/video/{ID}"
    assert movie.release_date == "2024-03-10"  # 北京时间
    assert movie.duration_minutes == 1
    assert movie.creator == "作者名字"
    assert movie.tags == ("旅行", "风景")


def test_the_share_page_router_carries_the_same_item() -> None:
    assert douyin.item_from_router(ROUTER_HTML)["aweme_id"] == ID


def test_a_page_without_the_router_data_is_empty() -> None:
    assert douyin.item_from_router("<html><body>验证码</body></html>") == {}


def test_a_share_link_is_resolved_through_one_redirect() -> None:
    transport = FakeTransport()

    assert douyin.resolve_share_code("iRNBho6u", client=transport) == ID
    assert transport.final_urls == ["https://v.douyin.com/iRNBho6u/"]


def test_a_share_link_that_does_not_land_on_a_video_says_so() -> None:
    transport = FakeTransport(final="https://www.douyin.com/")

    with pytest.raises(MovieImportError) as excinfo:
        douyin.resolve_share_code("iRNBho6u", client=transport)

    assert "没能展开" in excinfo.value.message


def test_fetch_asks_the_api_and_never_needs_the_page() -> None:
    transport = FakeTransport()

    movie = douyin.fetch(ID, client=transport)

    assert transport.urls == [f"{douyin.ITEMINFO}?item_ids={ID}"]
    assert movie.code == ID


def test_the_page_fills_in_when_the_api_is_blocked() -> None:
    """接口被挡只记一条日志，分享页那条路仍要把影片导进来。

    **分享页要问两次**：第一次只拿到 ``ttwid`` cookie，带上它才是服务端渲染的那一版。
    """
    transport = FakeTransport(api_failure=MovieImportError("被挡住了"))

    movie = douyin.fetch(ID, client=transport)

    assert movie.title == "一个视频标题"
    assert transport.urls[-1] == f"{douyin.SHARE}/{ID}/"
    assert transport.page_calls == 2


def test_a_missing_page_is_translated_into_chinese() -> None:
    transport = FakeTransport(
        api=None, page_failure=SourcePageError(404, "https://www.iesdouyin.com/")
    )

    with pytest.raises(MovieImportError) as excinfo:
        douyin.fetch(ID, client=transport)

    assert "抖音上没有这个视频" in excinfo.value.message


def test_a_line_that_is_not_a_code_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        douyin.fetch("IPX-660", client=transport)

    assert transport.urls == [] and transport.final_urls == []
