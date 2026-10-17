"""``Library`` 的影片导入：抓元数据、下封面、写文件、建事件（ADR 0063）。

**不联网**：传输是假实现。重点盯三件事：封面确实是写进目标文件夹的**新文件**（只增不改）、
事件与绑定都建起来了、以及每一步都在 journal 里留了记录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.library.facade import Library
from gallery.core.metadata.journal import ACTION_DOWNLOAD, read_records

#: 一张最小的合法 JPEG（这一层不解码，但用真图更像真实数据）。
COVER_BYTES = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")

#: 搜索页 / 详情页片段（形状照 JavDB，与 ``test_jav`` 各自独立）。
SEARCH_HTML = (
    '<a href="/v/Arbam" class="box">'
    '<img src="https://c0.example.com/covers/ar/Arbam.jpg">'
    '<div class="video-title"><strong>IPX-660</strong> 一个标题</div>'
    '<div class="meta">2021-05-13</div></a>'
)
DETAIL_HTML = (
    '<strong class="current-title">一个标题</strong>'
    '<img class="video-cover" src="https://c0.example.com/covers/ar/Arbam.jpg">'
    '<div class="panel-block"><strong>番號:</strong> IPX-660</div>'
    '<div class="panel-block"><strong>日期:</strong> 2021-05-13</div>'
    '<div class="panel-block"><strong>演員:</strong> 桃乃木かな</div>'
    "</nav>"
)


class FakeSource:
    """按 URL 返回页面 / 封面字节。"""

    def __init__(self, cover_url: str = "https://c0.example.com/covers/ar/Arbam.jpg") -> None:
        self._cover_url = cover_url
        self.cover_hits = 0

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        return DETAIL_HTML if "/v/" in url else SEARCH_HTML

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:
        assert url == self._cover_url
        self.cover_hits += 1
        return COVER_BYTES


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library.open(tmp_path, cache_base=tmp_path / "cache")


def test_importing_writes_a_new_cover_and_builds_the_event(
    library: Library, tmp_path: Path
) -> None:
    target = tmp_path / "影片"
    target.mkdir()
    source = FakeSource()

    plan = library.plan_movie_import(["ipx660"], client=source)
    assert plan.total == 1
    assert plan.duplicates == 0
    assert plan.failures == ()

    outcome = library.import_movies(plan, target=target, client=source)
    assert outcome.created == 1
    assert outcome.failures == ()
    assert outcome.journaled is True

    # 封面是新文件，内容就是下载回来的字节。
    assert len(outcome.files) == 1
    cover = outcome.files[0]
    assert cover.parent == target
    assert cover.read_bytes() == COVER_BYTES

    # 事件建起来了，番号、封面、绑定都对。
    events = library.events_by_kind("movie")
    assert len(events) == 1
    event = events[0]
    assert event.text("code") == "IPX-660"
    assert event.cover_path == cover
    assert library.events.photos_of_event(event.id) == (cover,)

    # 写文件这一步留了 journal。
    records = read_records(tmp_path)
    assert any(record.action == ACTION_DOWNLOAD and record.target == cover for record in records)
    assert paths.journal_dir(tmp_path).is_dir()


def test_the_cover_does_not_overwrite_an_existing_file(library: Library, tmp_path: Path) -> None:
    target = tmp_path / "影片"
    target.mkdir()
    (target / "IPX-660.jpg").write_bytes(b"existing")
    source = FakeSource()

    plan = library.plan_movie_import(["IPX-660"], client=source)
    outcome = library.import_movies(plan, target=target, client=source)

    # 已有的那个字节没动，新封面换了个编号的名字。
    assert (target / "IPX-660.jpg").read_bytes() == b"existing"
    assert outcome.files[0].name == "IPX-660 2.jpg"


def test_a_second_plan_reports_the_code_as_a_duplicate(library: Library, tmp_path: Path) -> None:
    target = tmp_path / "影片"
    target.mkdir()
    source = FakeSource()
    library.import_movies(
        library.plan_movie_import(["IPX-660"], client=source), target=target, client=source
    )

    again = library.plan_movie_import(["IPX-660"], client=source)
    assert again.duplicates == 1

    outcome = library.import_movies(again, target=target, skip_duplicates=True, client=source)
    assert outcome.created == 0
    assert outcome.duplicates == 1
    assert source.cover_hits == 1  # 跳过的没再去下封面


def test_a_missing_code_lands_in_failures(library: Library) -> None:
    class EmptySource:
        def get_text(self, url: str, *, referer: str | None = None) -> str:
            return "<div></div>"

        def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:  # pragma: no cover
            return b""

    plan = library.plan_movie_import(["NOPE-001"], client=EmptySource())
    assert plan.total == 0
    assert len(plan.failures) == 1
    assert "NOPE-001" in plan.failures[0]


def test_movie_url_uses_the_configured_site(library: Library, tmp_path: Path) -> None:
    target = tmp_path / "影片"
    target.mkdir()
    source = FakeSource()
    library.import_movies(
        library.plan_movie_import(["IPX-660"], client=source), target=target, client=source
    )
    event = library.events_by_kind("movie")[0]
    assert library.movie_url(event) == "https://jable.tv/videos/ipx-660/"
