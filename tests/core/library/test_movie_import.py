"""``Library`` 的影片导入：抓元数据、下封面、写文件、建事件（ADR 0063 / 0068）。

**不联网**：来源是假实现（它自己走真的解析）。重点盯三件事：封面确实是写进目标文件夹的**新文件**
（只增不改）、事件与绑定都建起来了、以及每一步都在 journal 里留了记录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.events import jav
from gallery.core.events.models import EventDraft, MovieInfo, MovieRef
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
    """一个够用的「来源」：**真的走 jav 那套解析**，传输是假的（不必联网）。

    它实现的是 :class:`gallery.core.events.sources.MovieSource` —— 抓元数据与下封面分开，
    正是 ``Library`` 那两步各自要用的东西。
    """

    def __init__(self, cover_url: str = "https://c0.example.com/covers/ar/Arbam.jpg") -> None:
        self._cover_url = cover_url
        self.cover_hits = 0

    def fetch(self, ref: MovieRef) -> MovieInfo:
        hits = jav.parse_search_items(SEARCH_HTML, jav.DEFAULT_SOURCE_BASE)
        hit = jav.locate(hits, ref.code)
        return jav.parse_detail(
            DETAIL_HTML, ref.code, base=jav.DEFAULT_SOURCE_BASE, hit=hit
        )

    def cover_bytes(self, movie: MovieInfo) -> bytes:
        assert movie.cover_urls == (self._cover_url,)
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
        """搜索页上一条结果都没有（源站给的是那份「暫無內容」的空页）。"""

        def fetch(self, ref: MovieRef) -> MovieInfo:  # pragma: no cover - 一定会抛
            raise jav.no_search_results('<div class="empty-message">暫無內容</div>', ref.code)

        def cover_bytes(self, movie: MovieInfo) -> bytes:  # pragma: no cover
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


def test_movie_url_follows_the_source(library: Library) -> None:
    """三个来源各打开各的那一页；没填来源的当 JavDB（手写的草稿、以及早于 ADR 0068 的那些）。"""
    moments = {
        "YouTube": "dQw4w9WgXcQ",
        "Bilibili": "BV1GJ411x7h7",
        "": "IPX-660",
    }
    opened = {
        source: library.movie_url(
            library.event(
                library.create_event(
                    "movie", EventDraft.of(title="一部影片", source=source, code=code)
                ).event.id
            )
        )
        for source, code in moments.items()
    }

    assert opened["YouTube"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert opened["Bilibili"] == "https://www.bilibili.com/video/BV1GJ411x7h7/"
    assert opened[""] == "https://jable.tv/videos/ipx-660/"
