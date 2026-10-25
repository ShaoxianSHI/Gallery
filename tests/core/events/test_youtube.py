"""``gallery.core.events.youtube``：按 videoId 抓取与解析（ADR 0068）。

**不联网**：oEmbed 的 JSON 与 watch 页片段按真实形状手写。这一份盯的是两步走那条分界：
**oEmbed 那一步必须成**（标题 / 频道 / 封面），watch 页那一刀失败了也照样算抓到了。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events import youtube
from gallery.core.events.kinds import SOURCE_YOUTUBE

CODE = "dQw4w9WgXcQ"

#: 官方的 oEmbed 回包（只留这一层用得到的几项）。
OEMBED_JSON = (
    '{"title":"Never Gonna Give You Up","author_name":"Rick Astley",'
    '"thumbnail_url":"https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",'
    '"thumbnail_width":480,"thumbnail_height":360,"type":"video"}'
)

#: watch 页里那份 ``ytInitialPlayerResponse``（只留这一层要抠的几项）。**日期是带时区偏移的**
#: 完整时间戳，与真实页面一样 —— 按原样取前 10 位会差一天。
WATCH_HTML = (
    '<script>var ytInitialPlayerResponse = {"videoDetails":{"videoId":"dQw4w9WgXcQ",'
    '"lengthSeconds":"213","ownerChannelName":"Rick Astley",'
    '"keywords":["rick astley","never gonna give you up","\\u30ea\\u30c3\\u30af"]},'
    '"microformat":{"playerMicroformatRenderer":'
    '{"publishDate":"2009-10-24T23:57:33-07:00"}}};</script>'
)

OEMBED_URL = (
    "https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DdQw4w9WgXcQ"
    "&format=json"
)


class FakeTransport:
    """按 URL 返回预先备好的页面；``fail`` 里的那几个按预定异常抛出。"""

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self._fail = fail or {}
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if url in self._fail:
            raise self._fail[url]
        return OEMBED_JSON if url == OEMBED_URL else WATCH_HTML

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一步不该下封面")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", CODE),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL0", CODE),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", CODE),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", CODE),
        ("https://youtu.be/dQw4w9WgXcQ", CODE),
        ("https://youtu.be/dQw4w9WgXcQ?t=1", CODE),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", CODE),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", CODE),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", CODE),
        ("https://www.youtube.com/v/dQw4w9WgXcQ", CODE),
        # **别的站一律不认**：认错的那一次会拿一个视频去 B 站找。
        ("https://www.bilibili.com/video/BV1GJ411x7h7", None),
        ("https://vimeo.com/12345", None),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/@RickAstleyYT", None),
    ],
)
def test_the_video_id_comes_out_of_the_url(url: str, expected: str | None) -> None:
    assert youtube.id_from_url(url) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("dQw4w9WgXcQ", True),
        ("dQw4w9WgXc", False),
        ("", False),
        # 12 位的 BV 号不是 videoId（它由识别那一层先认掉，见 test_sources）。
        ("BV1GJ411x7h7", False),
    ],
)
def test_the_video_id_shape(text: str, expected: bool) -> None:
    assert youtube.is_video_id(text) is expected


def test_the_oembed_payload_gives_the_title_channel_and_cover() -> None:
    movie = youtube.oembed_of(json.loads(OEMBED_JSON), CODE)

    assert movie.source == SOURCE_YOUTUBE
    assert movie.code == CODE
    assert movie.title == "Never Gonna Give You Up"
    assert movie.creator == "Rick Astley"
    assert movie.page_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    # 封面是按顺序试的一串候选：大图在前，oEmbed 给的那一张（4:3 带黑边）垫底。
    assert movie.cover_urls[0] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
    assert movie.cover_urls[-1] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_the_watch_page_fills_in_the_duration_date_and_tags() -> None:
    movie = youtube.with_page(WATCH_HTML, youtube.oembed_of(json.loads(OEMBED_JSON), CODE))

    assert movie.duration_minutes == 4  # 213 秒 → 4 分钟（四舍五入）
    # 页面上写的是 ``2009-10-24T23:57:33-07:00``，按 UTC 取那一天是 25 日（官方 API 也是 25 日）。
    assert movie.release_date == "2009-10-25"
    # 页面上那一串是 JSON 里的一段，**要解码一次**（``\\u30ea\\u30c3\\u30af`` → リック）。
    assert movie.tags == ("rick astley", "never gonna give you up", "リック")
    assert movie.creator == "Rick Astley"


def test_fetch_reads_the_oembed_first_then_the_watch_page() -> None:
    transport = FakeTransport()

    movie = youtube.fetch(CODE, client=transport)

    assert movie.title == "Never Gonna Give You Up"
    assert movie.duration_minutes == 4
    assert transport.urls == [OEMBED_URL, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "不允许嵌入"), (403, "不允许嵌入"), (404, "未收录该视频")],
)
def test_the_oembed_status_is_told_apart(status: int, expected: str) -> None:
    """**401 / 403 与 404 是两回事**：一个该换工具，一个该换视频。"""
    transport = FakeTransport({OEMBED_URL: SourcePageError(status, OEMBED_URL)})

    with pytest.raises(MovieImportError) as excinfo:
        youtube.fetch(CODE, client=transport)

    assert expected in excinfo.value.message
    assert CODE in excinfo.value.message


def test_a_broken_watch_page_still_leaves_a_usable_movie() -> None:
    """watch 页那一刀是**补的**（ADR 0068 决策 4）：它断了，标题与封面照样要有。"""
    transport = FakeTransport(
        {"https://www.youtube.com/watch?v=dQw4w9WgXcQ": MovieImportError("被反爬挡住了。")}
    )

    movie = youtube.fetch(CODE, client=transport)

    assert movie.title == "Never Gonna Give You Up"
    assert movie.creator == "Rick Astley"
    assert movie.cover_urls
    assert movie.duration_minutes is None
    assert movie.tags == ()


def test_a_teaser_too_short_to_be_a_minute_stays_empty() -> None:
    """时长那一栏是给人看的整数，**不足半分钟的就是空**（ADR 0068 决策 2）。"""
    empty = youtube.oembed_of({}, CODE)
    assert youtube.with_page('"lengthSeconds":"20"', empty).duration_minutes is None
    assert youtube.with_page('"lengthSeconds":"40"', empty).duration_minutes == 1


def test_a_date_without_an_offset_keeps_the_day_it_says() -> None:
    """认不出、或者没带时区的都不猜：前者退回前 10 位，后者它自己写的就是哪一天。"""
    empty = youtube.oembed_of({}, CODE)
    assert youtube.with_page('"uploadDate":"2009-10-25"', empty).release_date == "2009-10-25"
    assert youtube.with_page('"publishDate":"不是时间"', empty).release_date == "不是时间"


def test_fetch_refuses_something_that_is_not_a_video_id() -> None:
    with pytest.raises(MovieImportError):
        youtube.fetch("IPX-660", client=FakeTransport())
