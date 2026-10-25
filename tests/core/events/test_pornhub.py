"""``gallery.core.events.pornhub``：viewkey 的几种写法、页面里那几处字段（ADR 0083）。

**不联网**：传输是假的。这一家的元数据散在三处 —— ``og:`` 标签、``flashvars`` 那段 JSON、
以及 ``data-label`` 列表 —— 所以用例要分别盯「只有其中一处时还认不认得出」。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events import pornhub

CODE = "ph5af5fef7c2aa7"

COVER = "https://ei.phncdn.com/videos/202401/02/123/original/1.jpg"

#: 一份像样的影片页：三处字段都在，且互相有重叠（真实的页面就是这样）。
PAGE = f"""<!DOCTYPE html>
<html><head>
<meta property="og:title" content="BFFS - Cute Teen Girls Share Cock On The Floor">
<meta property="og:image" content="{COVER}">
<meta property="og:duration" content="622">
<meta property="og:video:release_date" content="2024-01-02">
<meta name="twitter:title" content="BFFS - Cute Teen Girls Share Cock On The Floor">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "VideoObject",
  "name": "BFFS - Cute Teen Girls Share Cock On The Floor",
  "thumbnailUrl": ["{COVER}"],
  "uploadDate": "2024-01-02T03:04:05+00:00",
  "duration": "PT10M22S",
  "author": {{"@type": "Person", "name": "BFFs"}}
}}
</script>
</head><body>
<h1 class="title">BFFS - Cute Teen Girls Share Cock On The Floor</h1>
<div class="userInfo"><span>From:&nbsp;<a href="/users/bffs">BFFs</a></span></div>
<div class="tagsWrapper">
  <a data-label="tag" href="/video/search?search=one">One &amp; Two</a>
  <a data-label="tag" href="/video/search?search=two"><span>Two</span></a>
  <a data-label="category" href="/categories/teen">Teen</a>
  <a data-label="pornstar" href="/pornstar/jane-doe">Jane Doe</a>
</div>
<script type="text/javascript">
var flashvars_12345 = {{"video_duration": 622, "image_url": "{COVER}"}};
</script>
</body></html>"""


class FakeTransport:
    """按 URL 返回预先备好的页面。**这一份只服务一部影片**。"""

    def __init__(self, html: str = PAGE, *, failure: Exception | None = None) -> None:
        self.html = html
        self.failure = failure
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if self.failure is not None:
            raise self.failure
        return self.html

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CODE, CODE),
        ("648719015", "648719015"),
        ("  PH5AF5FEF7C2AA7  ", CODE),
    ],
)
def test_a_bare_code_is_a_viewkey(text: str, expected: str) -> None:
    assert pornhub.id_from_text(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "IPX-660", "ph5af5fef7c2-aa7", "ph5af5fef7c2aa7!"])
def test_other_shapes_are_not_viewkeys(text: str) -> None:
    """viewkey 只有字母与数字 —— 带连字符、带标点的都不是。"""
    assert pornhub.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.pornhub.com/view_video.php?viewkey={CODE}", CODE),
        (f"https://cn.pornhub.com/view_video.php?viewkey={CODE}&pkey=1", CODE),
        (f"https://www.pornhub.net/view_video.php?viewkey={CODE}", CODE),
        (f"https://www.pornhubpremium.com/view_video.php?viewkey={CODE}", CODE),
        (f"https://www.pornhub.com/gay/view_video.php?viewkey={CODE}", CODE),
        (f"https://www.pornhub.com/embed/{CODE}", CODE),
        (f"https://www.pornhub.com/gay/embed/{CODE}?autoplay=1", CODE),
        (f"https://www.pornhub.com/pornstar/jane-doe/video/{CODE}", CODE),
        # 作者页、合集页与搜索页都不是某一部。
        ("https://www.pornhub.com/model/zoe_ph/videos", None),
        ("https://www.pornhub.com/playlist/44121572", None),
        ("https://www.pornhub.com/video/search?search=123", None),
        # 别的站即便也带 viewkey 也不认。
        ("https://www.example.com/view_video.php?viewkey=ph5af5fef7c2aa7", None),
    ],
)
def test_a_url_yields_the_viewkey(url: str, expected: str | None) -> None:
    assert pornhub.id_from_url(url) == expected


def test_the_page_address_is_the_view_video_form() -> None:
    assert pornhub.movie_url(CODE) == (
        "https://www.pornhub.com/view_video.php?viewkey=ph5af5fef7c2aa7"
    )


def test_the_view_gives_the_fields_the_page_offers() -> None:
    movie = pornhub.view_of(PAGE, CODE)

    assert movie.source == "Pornhub"
    assert movie.code == CODE
    assert movie.title == "BFFS - Cute Teen Girls Share Cock On The Floor"
    assert movie.cover_urls == (COVER,)
    assert movie.page_url == f"https://www.pornhub.com/view_video.php?viewkey={CODE}"
    assert movie.release_date == "2024-01-02"
    assert movie.duration_minutes == 10
    assert movie.creator == "BFFs"
    assert movie.tags == ("One & Two", "Two", "Teen")
    assert movie.actors == ("Jane Doe",)


def test_the_flashvars_fill_in_when_the_meta_tags_are_missing() -> None:
    """``og:`` 那几行不全时，时长与封面还有 ``flashvars`` 那一份可用。"""
    page = """<html><head></head><body>
<h1 class="title">只有一个标题</h1>
<script>var flashvars_99 = {"video_duration": 1753, "image_url": "//ei.phncdn.com/a.jpg"};</script>
</body></html>"""

    movie = pornhub.view_of(page, CODE)

    assert movie.title == "只有一个标题"
    assert movie.duration_minutes == 29
    assert movie.cover_urls == ("https://ei.phncdn.com/a.jpg",)


def test_the_json_ld_fills_in_the_rest() -> None:
    """JSON-LD 的 ``duration`` 是 ISO 8601，``uploadDate`` 带着时间与时区。"""
    page = """<html><head>
<meta property="og:title" content="标题">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "VideoObject",
  "name": "标题",
  "thumbnailUrl": "https://ei.phncdn.com/json-ld.jpg",
  "uploadDate": "2021-02-05T08:00:00+00:00",
  "duration": "PT1H2M3S",
  "author": {"@type": "Person", "name": "Some Studio"}}
</script>
</head><body></body></html>"""

    movie = pornhub.view_of(page, CODE)

    assert movie.cover_urls == ("https://ei.phncdn.com/json-ld.jpg",)
    assert movie.release_date == "2021-02-05"
    assert movie.duration_minutes == 62
    assert movie.creator == "Some Studio"


def test_a_page_without_a_title_says_it_cannot_be_read() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        pornhub.view_of("<html><body>确认是否满 18 岁</body></html>", CODE)

    assert "未提供影片信息" in excinfo.value.message


def test_a_removed_video_says_so() -> None:
    page = '<html><body><div class="removed">This video has been removed</div></body></html>'

    with pytest.raises(MovieImportError) as excinfo:
        pornhub.view_of(page, CODE)

    assert "已被删除 / 设为私密" in excinfo.value.message


def test_a_video_blocked_in_this_country_says_so() -> None:
    page = '<html><body><div class="geoBlocked">这里看不到</div></body></html>'

    with pytest.raises(MovieImportError) as excinfo:
        pornhub.view_of(page, CODE)

    assert "网络所在地" in excinfo.value.message


@pytest.mark.parametrize("duration", ["PT", "PTX", "", "10:22"])
def test_a_duration_we_cannot_read_is_left_empty(duration: str) -> None:
    page = (
        "<html><head><meta property='og:title' content='标题'>"
        '<script type="application/ld+json">'
        + json.dumps({"@type": "VideoObject", "duration": duration})
        + "</script></head></html>"
    )

    assert pornhub.view_of(page, CODE).duration_minutes is None


def test_fetch_asks_for_the_page_and_nothing_else() -> None:
    transport = FakeTransport()

    movie = pornhub.fetch(CODE, client=transport)

    assert transport.urls == [f"https://www.pornhub.com/view_video.php?viewkey={CODE}"]
    assert movie.code == CODE


def test_fetch_normalizes_the_code_it_was_handed() -> None:
    transport = FakeTransport()

    movie = pornhub.fetch(" PH5AF5FEF7C2AA7 ", client=transport)

    assert transport.urls == [f"https://www.pornhub.com/view_video.php?viewkey={CODE}"]
    assert movie.code == CODE


def test_a_missing_page_is_translated_into_chinese() -> None:
    transport = FakeTransport(failure=SourcePageError(404, "https://www.pornhub.com/"))

    with pytest.raises(MovieImportError) as excinfo:
        pornhub.fetch(CODE, client=transport)

    assert "Pornhub 未收录该视频" in excinfo.value.message


def test_a_line_that_is_not_a_viewkey_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        pornhub.fetch("IPX-660", client=transport)

    assert transport.urls == []


def test_the_meta_tags_are_read_in_any_attribute_order_and_quoting() -> None:
    page = "<html><head><meta content='标题' property='og:title'></head></html>"

    assert pornhub.metas_of(page)["og:title"] == "标题"
