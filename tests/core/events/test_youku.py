"""``gallery.core.events.youku``：编号、页面里那一段状态、风控页（ADR 0082）。

**不联网**：传输是假的。这一家最要紧的两条是编号有两种写法（带不带尾部 ``=``）与**风控页**——
后者也是 200，认不出它就会把「过一会儿再试」说成「没有这个视频」。
"""

from __future__ import annotations

import pytest

from gallery.core.errors import MovieImportError
from gallery.core.events import youku

#: 影片页里那一段状态（形状与真实页面一致：等号后面直接是花括号，斜杠是 ``\u002F``）。
PAGE = (
    '<script>window.__INITIAL_DATA__ ={"pageMap":{"extra":{'
    '"videoTitle":"一个标题",'
    '"videoImg":"https:\\u002F\\u002Fm.ykimg.com\\u002F0541010167ED0AD766E84817B6958818",'
    '"duration":70.56,"videoPublishTime":"2025-04-02 18:01:57",'
    '"videoCategory":"电视剧","showName":"某部剧"}}}</script>'
)

#: 风控页：一个 JS 跳转，正文里什么都没有。
CHALLENGE = (
    '<script>var url = window.location.protocol + "//v.youku.com//v_show/id_X.html'
    '/_____tmd_____/punish?x5secdata=abc";window.location.href = url;</script>'
)


class FakeTransport:
    def __init__(self, page: str = PAGE) -> None:
        self.page = page
        self.urls: list[str] = []

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        self.urls.append(url)
        return self.page

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
        raise AssertionError("这一条不该去下封面")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("XNjQ3MjkwNTUwNA", "XNjQ3MjkwNTUwNA"),
        ("XNjQ3MjkwNTUwNA==", "XNjQ3MjkwNTUwNA"),
        ("  XNjQ3MjkwNTUwNA  ", "XNjQ3MjkwNTUwNA"),
    ],
)
def test_the_same_video_has_two_spellings(text: str, expected: str) -> None:
    """带不带尾部 ``=`` 是同一个编号 —— 归一到不带的那种，判重才对得上。"""
    assert youku.normalize_code(text) == expected
    assert youku.id_from_text(text) == expected


@pytest.mark.parametrize("text", ["X12345", "NjQ3MjkwNTUwNA", "IPX-660", "14544857"])
def test_other_shapes_are_not_youku_ids(text: str) -> None:
    assert youku.id_from_text(text) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA==.html", "XNjQ3MjkwNTUwNA"),
        ("https://v.youku.com/video/id_XNTk0MDA0MTM0NA.html", "XNTk0MDA0MTM0NA"),
        ("https://www.youku.com/v_show/id_XNjQ3MjkwNTUwNA.html", "XNjQ3MjkwNTUwNA"),
        ("https://www.mgtv.com/h/14544857.html", None),
    ],
)
def test_a_url_yields_the_id(url: str, expected: str | None) -> None:
    assert youku.id_from_url(url) == expected


def test_the_page_gives_title_cover_duration_and_date() -> None:
    movie = youku.view_of(_payload(PAGE), "XNjQ3MjkwNTUwNA")

    assert movie.source == "优酷"
    assert movie.code == "XNjQ3MjkwNTUwNA"
    assert movie.title == "一个标题"
    assert movie.release_date == "2025-04-02"
    assert movie.duration_minutes == 1  # 70.56 秒
    assert movie.tags == ("电视剧",)
    assert movie.series == "某部剧"
    assert movie.cover_urls == ("https://m.ykimg.com/0541010167ED0AD766E84817B6958818",)


def test_a_page_without_that_state_is_reported_as_a_broken_page() -> None:
    with pytest.raises(MovieImportError) as excinfo:
        youku.fetch("XNjQ3MjkwNTUwNA", client=FakeTransport("<html>壳</html>"))

    assert "页面结构" in excinfo.value.message


def test_the_risk_control_page_is_told_apart_from_a_missing_video() -> None:
    """风控页也是 200。混成「没有这个视频」会让人去换编号，而该做的是等一会儿再试。"""
    assert youku.is_challenge(CHALLENGE)

    with pytest.raises(MovieImportError) as excinfo:
        youku.fetch("XNjQ3MjkwNTUwNA", client=FakeTransport(CHALLENGE))

    assert "风控" in excinfo.value.message
    assert "过一会儿再试" in excinfo.value.message


def test_the_page_is_fetched_at_the_canonical_address() -> None:
    transport = FakeTransport()

    movie = youku.fetch("XNjQ3MjkwNTUwNA", client=transport)

    assert transport.urls == ["https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA.html"]
    assert movie.title == "一个标题"


def test_a_line_that_is_not_an_id_says_so_without_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(MovieImportError):
        youku.fetch("14544857", client=transport)

    assert transport.urls == []


def _payload(html: str) -> dict[str, object]:
    payload = youku.payload_of(html)
    assert payload is not None
    return dict(payload)
