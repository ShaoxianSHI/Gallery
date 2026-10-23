"""``gallery.core.events.bilibili``：BV 号、接口回包与解析（ADR 0068）。

**不联网**：接口回包按真实形状手写。这一份盯三件事：``av`` ↔ BV 的互转（**大小写敏感**）、
接口那个 ``code`` 里的错误要翻译成人话、以及标签那一趟是「补的」不算失败。
"""

from __future__ import annotations

import json

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import bilibili
from gallery.core.events.kinds import SOURCE_BILIBILI

#: 两个实测过的编号对：**大小写差一位就是两部不同的片子**（aid 80433022 与 444729062）。
PAIRS = ((80433022, "BV1GJ411x7h7"), (444729062, "BV1gj411X7h7"))

VIEW_JSON = json.dumps(
    {
        "code": 0,
        "message": "0",
        "data": {
            "bvid": "BV1GJ411x7h7",
            "aid": 80433022,
            "title": "【官方】一个视频",
            "pic": "http://i0.hdslb.com/bfs/archive/deadbeef.jpg",
            "pubdate": 1560000000,
            "duration": 213,
            "owner": {"mid": 1, "name": "某个 UP 主"},
            "tname": "日常",
            "ugc_season": {"id": 1, "title": "某个合集"},
        },
    },
    ensure_ascii=False,
)

TAGS_JSON = json.dumps(
    {
        "code": 0,
        "data": [
            {"tag_id": 1, "tag_name": "原神", "jump_url": ""},
            {"tag_id": 2, "tag_name": "实况", "jump_url": ""},
        ],
    },
    ensure_ascii=False,
)

VIEW_URL = "https://api.bilibili.com/x/web-interface/view?bvid=BV1GJ411x7h7"
TAGS_URL = "https://api.bilibili.com/x/web-interface/view/detail/tag?bvid=BV1GJ411x7h7"


class FakeTransport:
    """按 URL 返回预先备好的 JSON；``fail`` 里的那几个按预定异常抛出。"""

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self._fail = fail or {}
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        if url in self._fail:
            raise self._fail[url]
        return TAGS_JSON if "detail/tag" in url else VIEW_JSON

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一步不该下封面")


def test_the_old_number_and_the_new_one_are_the_same_video() -> None:
    """``av`` 号与 BV 号是同一个东西的两种写法，互转是纯算术。"""
    for aid, code in PAIRS:
        assert bilibili.av_to_bv(aid) == code
        assert bilibili.bv_to_av(code) == aid


def test_the_number_is_case_sensitive() -> None:
    """**不许顺手 ``upper()``**：``BV1GJ411x7h7`` 与 ``BV1gj411X7h7`` 是两部不同的片子。"""
    assert bilibili.id_from_text("BV1GJ411x7h7") == "BV1GJ411x7h7"
    assert bilibili.id_from_text("BV1gj411X7h7") == "BV1gj411X7h7"
    assert bilibili.id_from_text("bv1gj411x7h7") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("av80433022", "BV1GJ411x7h7"),
        ("AV80433022", "BV1GJ411x7h7"),
        ("av0", None),
        ("IPX-660", None),
        ("https://www.bilibili.com/video/BV1GJ411x7h7", None),  # 网址走 id_from_url
    ],
)
def test_the_number_comes_out_of_a_bare_token(text: str, expected: str | None) -> None:
    assert bilibili.id_from_text(text) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.bilibili.com/video/BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=333", "BV1GJ411x7h7"),
        ("https://www.bilibili.com/video/av80433022?p=2", "BV1GJ411x7h7"),
        ("https://b23.tv/abcd", None),  # 短链要展开才认得出，而识别这一步不联网
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_the_number_comes_out_of_a_url(url: str, expected: str | None) -> None:
    assert bilibili.id_from_url(url) == expected


def test_the_watch_url_can_start_from_the_beginning() -> None:
    """``t=0`` 会被 B 站忽略，所以起点用一个小正数；**资料页的地址不带它**。"""
    assert bilibili.movie_url("BV1GJ411x7h7", start_at_beginning=True) == (
        "https://www.bilibili.com/video/BV1GJ411x7h7/?t=0.001"
    )
    assert bilibili.movie_url("BV1GJ411x7h7") == "https://www.bilibili.com/video/BV1GJ411x7h7/"


def test_the_view_payload_yields_a_movie() -> None:
    movie = bilibili.view_of(json.loads(VIEW_JSON), "BV1GJ411x7h7")

    assert movie.source == SOURCE_BILIBILI
    assert movie.code == "BV1GJ411x7h7"
    assert movie.title == "【官方】一个视频"
    # ``pic`` 给的是 ``http://``，**要升成 https**（库里的封面地址不许是明文）。
    assert movie.cover_urls == ("https://i0.hdslb.com/bfs/archive/deadbeef.jpg",)
    assert movie.page_url == "https://www.bilibili.com/video/BV1GJ411x7h7/"
    assert movie.duration_minutes == 4
    assert movie.creator == "某个 UP 主"
    assert movie.series == "某个合集"
    # 发布时间戳按北京时间取日期（1560000000 秒 = 2019-06-08 21:20 UTC+8）。
    assert movie.release_date == "2019-06-08"


def test_the_tags_payload_yields_the_names() -> None:
    assert bilibili.tags_of(json.loads(TAGS_JSON)) == ("原神", "实况")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (-404, "没有这个稿件"),
        (-403, "权限不足"),
        (-352, "风控"),
        (62002, "不可见"),
        (62004, "审核中"),
        (62012, "只有 UP 主自己看得见"),
    ],
)
def test_the_interface_error_code_becomes_a_sentence(status: int, expected: str) -> None:
    """**HTTP 是 200，错在 JSON 里** —— 分得清「删了」和「还在审核」，用户才知道下一步做什么。"""
    payload = {"code": status, "message": "whatever"}

    with pytest.raises(MovieImportError) as excinfo:
        bilibili.view_of(payload, "BV1GJ411x7h7")

    assert expected in excinfo.value.message
    assert "BV1GJ411x7h7" in excinfo.value.message


def test_an_unknown_error_code_still_carries_the_number() -> None:
    """认不出的码**原样带上**：用户报问题时能照着它搜。"""
    with pytest.raises(MovieImportError) as excinfo:
        bilibili.view_of({"code": -9999, "message": "谁知道"}, "BV1GJ411x7h7")

    assert "-9999" in excinfo.value.message
    assert "谁知道" in excinfo.value.message


def test_a_malformed_body_says_it_could_not_be_read() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        bilibili.view_of({"code": 0, "data": "不是一份稿件信息"}, "BV1GJ411x7h7")

    assert "没给出" in excinfo.value.message


def test_fetch_reads_the_view_and_then_the_tags() -> None:
    transport = FakeTransport()

    movie = bilibili.fetch("BV1GJ411x7h7", client=transport)

    assert movie.title == "【官方】一个视频"
    assert movie.tags == ("原神", "实况")
    assert transport.urls == [VIEW_URL, TAGS_URL]


def test_the_tags_are_a_bonus_and_their_failure_is_not_fatal() -> None:
    """标签那一趟是**补的**（ADR 0068 决策 4）：拿不到不该让整部影片导不进来。"""
    transport = FakeTransport({TAGS_URL: MovieImportError("被限速了。")})

    movie = bilibili.fetch("BV1GJ411x7h7", client=transport)

    assert movie.title == "【官方】一个视频"
    assert movie.tags == ()


def test_fetch_refuses_something_that_is_not_a_bv_number() -> None:
    with pytest.raises(MovieImportError):
        bilibili.fetch("IPX-660", client=FakeTransport())
