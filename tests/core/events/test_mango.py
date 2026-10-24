"""``gallery.core.events.mango``：两个编号、接口、时长写法（ADR 0082）。

**不联网**：传输是假的。这一家有两处容易做错的地方：影片页的地址要「合集 + 单集」两个编号
（只给单集是 404），以及时长写的是 ``mm:ss`` 而不是秒。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import mango

#: 一集正片：``info`` 里带着合集 id，``detail`` 里带着上映日期与类型。
EPISODE = {
    "code": 200,
    "msg": "",
    "data": {
        "info": {
            "title": "变形计 第一季",
            "videoName": "变形计20060904期：网变（一）",
            "clipName": "变形计 第一季",
            "clipId": "294541",
            "seriesId": "54764",
            "videoImage": "http://2img.hitv.com/preview/sp_images/2017/zongyi/294541/3779644/x.jpg",
            "clipImage": "",
            "time": "31:08",
            "detail": {
                "area": "内地",
                "releaseTime": "2006-09-04",
                "presenter": "王姬 / 陆立",
                "director": "赵文海",
                "kind": "真人秀 / 纪实",
                "url": "/b/294541/3779644.html",
            },
        }
    },
}

#: 一条 UGC 片段：没有合集、没有 ``detail``（它在网页上没有页面）。
CLIP = {
    "code": 200,
    "msg": "",
    "data": {
        "info": {
            "title": "君九龄 朱攒九龄太好磕了",
            "videoName": "君九龄 朱攒九龄太好磕了",
            "clipName": "",
            "clipId": "",
            "videoImage": "https://ugc.hitv.com/3/2110222245130938/31540.png?",
            "clipImage": "",
            "time": "00:54",
            "detail": {},
        }
    },
}


class FakeTransport:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = EPISODE if payload is None else payload
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        return json.dumps(self.payload, ensure_ascii=False)

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize("text", ["14544857", "123456", "9999999999"])
def test_a_bare_code_is_a_vid(text: str) -> None:
    assert mango.id_from_text(text) == text


@pytest.mark.parametrize("text", ["12345", "12345678901", "X14544857", "IPX-660"])
def test_other_shapes_are_not_vids(text: str) -> None:
    assert mango.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.mgtv.com/b/294541/3779644.html", "294541/3779644"),
        ("https://m.mgtv.com/b/294541/3779644.html", "294541/3779644"),
        ("https://www.mgtv.com/b/294541/3779644.html?from=1", "294541/3779644"),
        # 整部剧的页面（``/h/``）不指向某一集。
        ("https://www.mgtv.com/h/294541.html", None),
        ("https://www.mgtv.com/lib", None),
    ],
)
def test_a_url_yields_the_collection_and_the_video(url: str, expected: str | None) -> None:
    assert mango.id_from_url(url) == expected


def test_the_page_address_needs_the_collection_id() -> None:
    """**只给单集是 404**（实测），``/h/<单集>.html`` 也一样 —— 地址必须带合集 id。"""
    assert mango.movie_url("294541/3779644") == "https://www.mgtv.com/b/294541/3779644.html"
    assert mango.movie_url("3779644") is None


def test_the_view_gives_title_cover_and_the_show_name() -> None:
    movie = mango.view_of(EPISODE, "3779644")

    assert movie.source == "芒果TV"
    assert movie.title == "变形计20060904期：网变（一）"
    assert movie.series == "变形计 第一季"
    assert movie.page_url == "https://www.mgtv.com/b/294541/3779644.html"
    assert movie.cover_urls == (
        "https://2img.hitv.com/preview/sp_images/2017/zongyi/294541/3779644/x.jpg",
    )


def test_a_bare_vid_still_ends_up_with_the_pageable_code() -> None:
    """用户只粘了一串单集编号也要能打开：合集 id 是抓的时候补上的（这正是那个 404 的成因）。"""
    movie = mango.view_of(EPISODE, "3779644")

    assert movie.code == "294541/3779644"
    assert mango.movie_url(movie.code) == "https://www.mgtv.com/b/294541/3779644.html"


def test_the_view_gives_the_date_and_the_detail_lines() -> None:
    movie = mango.view_of(EPISODE, "294541/3779644")

    assert movie.release_date == "2006-09-04"
    assert movie.tags == ("真人秀", "纪实")
    assert movie.actors == ("王姬", "陆立")
    assert movie.director == "赵文海"


def test_a_clip_has_no_detail_at_all() -> None:
    """UGC 片段没有 ``detail``：日期与那几栏空着，**编号也只有单集那一段**（网页上没有它）。"""
    movie = mango.view_of(CLIP, "14544857")

    assert movie.code == "14544857"
    assert movie.release_date == ""
    assert movie.tags == ()
    assert movie.actors == ()
    assert mango.movie_url(movie.code) is None


def test_the_duration_is_written_as_minutes_and_seconds() -> None:
    """``00:54`` 是 54 秒（四舍五入成 1 分钟），``31:08`` 是 1868 秒。"""
    assert mango.view_of(CLIP, "14544857").duration_minutes == 1
    assert mango.view_of(EPISODE, "3779644").duration_minutes == 31


@pytest.mark.parametrize("time", ["", "未知", "12:ab"])
def test_a_duration_we_cannot_read_is_left_empty(time: str) -> None:
    payload = {**CLIP, "data": {"info": {**CLIP["data"]["info"], "time": time}}}

    assert mango.view_of(payload, "14544857").duration_minutes is None


def test_a_missing_video_is_reported_in_chinese() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        mango.view_of({"code": 10008, "msg": "视频信息获取失败"}, "999999999")

    assert "芒果 TV 上没有这个视频" in excinfo.value.message


def test_fetch_only_asks_for_the_vid_but_keeps_the_pageable_code() -> None:
    transport = FakeTransport()

    movie = mango.fetch("3779644", client=transport)

    assert transport.urls == ["https://pcweb.api.mgtv.com/video/info?vid=3779644"]
    assert movie.code == "294541/3779644"


def test_a_pasted_page_address_also_works() -> None:
    transport = FakeTransport()

    movie = mango.fetch("294541/3779644", client=transport)

    assert transport.urls == ["https://pcweb.api.mgtv.com/video/info?vid=3779644"]
    assert movie.code == "294541/3779644"


def test_a_line_that_is_not_a_vid_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        mango.fetch("IPX-660", client=transport)

    assert transport.urls == []
