"""``gallery.core.events.mango``：纯数字 vid、接口、时长写法（ADR 0082）。

**不联网**：传输是假的。这一家没有发布日期（接口不给），时长写的是 ``mm:ss`` 而不是秒，
两样都在这里锁定。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import mango

#: ``video/info`` 的响应（实测字段，略去用不上的）。
INFO = {
    "code": 200,
    "msg": "",
    "data": {
        "info": {
            "title": "君九龄 朱攒九龄太好磕了",
            "videoName": "君九龄 朱攒九龄太好磕了",
            "videoImage": "https://ugc.hitv.com/3/2110222245130938/31540.png?",
            "clipImage": "",
            "time": "00:54",
            "plName": "君九龄",
            "clipName": "",
        }
    },
}


class FakeTransport:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = INFO if payload is None else payload
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        return json.dumps(self.payload, ensure_ascii=False)

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize("text", ["14544857", "123456", "9999999999"])
def test_a_vid_is_a_run_of_digits(text: str) -> None:
    assert mango.id_from_text(text) == text


@pytest.mark.parametrize("text", ["12345", "12345678901", "X14544857", "IPX-660"])
def test_other_shapes_are_not_vids(text: str) -> None:
    assert mango.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.mgtv.com/h/14544857.html", "14544857"),
        ("https://www.mgtv.com/b/336727/14544857.html", "14544857"),
        ("https://m.mgtv.com/h/14544857.html", "14544857"),
        ("https://www.mgtv.com/lib", None),
    ],
)
def test_a_url_yields_the_vid(url: str, expected: str | None) -> None:
    assert mango.id_from_url(url) == expected


def test_the_view_gives_title_cover_and_the_series() -> None:
    movie = mango.view_of(INFO, "14544857")

    assert movie.source == "芒果TV"
    assert movie.code == "14544857"
    assert movie.title == "君九龄 朱攒九龄太好磕了"
    assert movie.series == "君九龄"
    assert movie.cover_urls == ("https://ugc.hitv.com/3/2110222245130938/31540.png?",)
    assert movie.page_url == "https://www.mgtv.com/h/14544857.html"


def test_the_duration_is_written_as_minutes_and_seconds() -> None:
    """``00:54`` 是 54 秒（四舍五入成 1 分钟），``01:23:45`` 是 5025 秒。"""
    short = {**INFO, "data": {"info": {**INFO["data"]["info"], "time": "00:54"}}}
    long = {**INFO, "data": {"info": {**INFO["data"]["info"], "time": "01:23:45"}}}

    assert mango.view_of(short, "14544857").duration_minutes == 1
    assert mango.view_of(long, "14544857").duration_minutes == 84


@pytest.mark.parametrize("time", ["", "未知", "12:ab"])
def test_a_duration_we_cannot_read_is_left_empty(time: str) -> None:
    payload = {**INFO, "data": {"info": {**INFO["data"]["info"], "time": time}}}

    assert mango.view_of(payload, "14544857").duration_minutes is None


def test_there_is_no_release_date_at_all() -> None:
    """接口不给日期，页面上的 og 标签也只在正式剧集上填 —— 这一栏留空（ADR 0082 决策 4）。"""
    assert mango.view_of(INFO, "14544857").release_date == ""


def test_a_missing_video_is_reported_in_chinese() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        mango.view_of({"code": 10008, "msg": "视频信息获取失败"}, "999999999")

    assert "芒果 TV 上没有这个视频" in excinfo.value.message


def test_fetch_asks_the_info_api() -> None:
    transport = FakeTransport()

    movie = mango.fetch("14544857", client=transport)

    assert transport.urls == ["https://pcweb.api.mgtv.com/video/info?vid=14544857"]
    assert movie.title == "君九龄 朱攒九龄太好磕了"


def test_a_line_that_is_not_a_vid_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        mango.fetch("IPX-660", client=transport)

    assert transport.urls == []
