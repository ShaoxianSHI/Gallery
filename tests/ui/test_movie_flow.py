"""「导入影片」与「⌥ 点开影片网站」的界面接线（ADR 0063 / 0068）。

**不联网**：`launch.open_in_browser` 被换掉，事件库是现场建的临时库。这一份盯的是**接线** ——
菜单在不在、⌥ 点一下会不会去开那个网址、普通照片会不会被误触发。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.events.store import EventDraft  # noqa: E402
from gallery.core.external import launch  # noqa: E402
from gallery.core.library.config import BROWSE_MODE_ALBUMS, prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.core.metadata.download import save_downloaded_file  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402


def _photo(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (16, 12), (40, 120, 200))
    try:
        image.save(path)
    finally:
        image.close()
    return path


#: 一张最小的合法 JPEG（测试里只关心它作为一个「照片文件」出现，不解码）。
_JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "照片"
    library.mkdir(parents=True)
    _photo(library, "封面.jpg")
    _photo(library, "普通照片.jpg")
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描")
    yield instance
    instance.close()


def _make_movie(window: MainWindow, root: Path) -> int:
    """建一个影片事件，把封面绑进去并设为封面，返回它的 id。"""
    library = window._library
    assert library is not None
    cover = library.root / "封面.jpg"
    outcome = library.create_event("movie", EventDraft.of(title="一部影片", code="IPX-660"))
    library.bind_photos([cover], outcome.event.id)
    library.set_event_cover(outcome.event.id, cover)
    return outcome.event.id


def _row_of_event_card(window: MainWindow, event_id: int) -> int:
    """屏幕上那张事件卡片的行号（**按事件 id 认**，不按位置）。"""
    for row in range(window._model.rowCount()):
        entry = window._model.entry_at(row)
        if isinstance(entry, GroupRef) and entry.key.endswith(f"#{event_id}"):
            return row
    raise AssertionError(f"屏幕上没有事件 {event_id} 的卡片")


def _spy_on_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def fake_open(url: str) -> launch.OpenOutcome:
        opened.append(url)
        return launch.OpenOutcome(where=launch.WHERE_BROWSER)

    monkeypatch.setattr("gallery.ui.window.event_flow.launch.open_in_browser", fake_open)
    return opened


def test_the_import_menus_sit_in_the_file_menu(window: MainWindow) -> None:
    titles = [action.text() for action in window.menuBar().actions()]
    assert "导入" not in titles, f"不该再有顶层「导入」菜单，实际菜单栏是 {titles}"
    file_menu = next(
        action.menu() for action in window.menuBar().actions() if action.text() == "文件"
    )
    assert file_menu is not None
    labels = [action.text() for action in file_menu.actions()]
    assert "导入影片" in labels and "导入演出" in labels
    assert labels.index("导入影片") > labels.index("最近打开")
    assert [action.text() for action in window._import_movies_menu.actions()] == [
        "导入 Bilibili…",
        "导入 YouTube…",
        "导入 JavDB…",
    ]
    assert [action.text() for action in window._import_shows_menu.actions()] == ["从「演迹」导入…"]


def test_each_movie_entry_hands_its_source_to_the_dialog(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(window, "import_movies_from_dialog", seen.append)

    for action in window._import_movies_menu.actions():
        action.trigger()

    assert seen == ["Bilibili", "YouTube", "JavDB"]


def test_every_top_level_menu_belongs_to_the_menu_bar(window: MainWindow) -> None:
    """每个顶层菜单都要**认在菜单栏名下** —— 与「文件 / 编辑 / 视图」同一条路。

    ``menuBar().addMenu(title)`` 把菜单建在菜单栏名下；``addMenu(QMenu)`` 不会，那只菜单会留在
    外面。四条菜单用同一种建法，父子关系才一致。
    """
    for action in window.menuBar().actions():
        menu = action.menu()
        assert menu is not None, f"「{action.text()}」不是一个菜单"
        assert menu.parent() is window.menuBar(), f"「{menu.title()}」没认在菜单栏名下"


def test_alt_clicking_a_movie_photo_opens_the_site(
    window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_movie(window, root)
    opened = _spy_on_browser(monkeypatch)
    row = window._model.row_of_path(window._library.root / "封面.jpg")  # type: ignore[union-attr]
    assert row >= 0

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == ["https://jable.tv/videos/ipx-660/"]


def test_alt_clicking_a_youtube_movie_opens_that_page(
    window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """来源是 YouTube 的影片要打开视频页，而不是拼到 ``movie_site`` 上（ADR 0068 决策 5）。"""
    library = window._library
    assert library is not None
    cover = library.root / "封面.jpg"
    outcome = library.create_event(
        "movie", EventDraft.of(title="一个视频", source="YouTube", code="dQw4w9WgXcQ")
    )
    library.bind_photos([cover], outcome.event.id)
    library.set_event_cover(outcome.event.id, cover)
    opened = _spy_on_browser(monkeypatch)
    row = window._model.row_of_path(cover)

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_alt_clicking_a_movie_group_card_opens_the_site(
    qt_app: QApplication, window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按事件分组时，⌥ 点**影片卡片本身**也要打开那个影片网站（ADR 0063 决策 6）。

    这一格背后就是那个影片事件，不必再从照片反查 —— 而卡片不是 ``PhotoRef``，
    照旧只认照片的那条路会把它整个跳过。
    """
    event_id = _make_movie(window, root)
    window.set_group_facet("event")
    assert wait_until(qt_app, lambda: window._model.group_count() > 0)
    opened = _spy_on_browser(monkeypatch)
    row = _row_of_event_card(window, event_id)

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == ["https://jable.tv/videos/ipx-660/"]


def test_alt_clicking_a_non_movie_group_card_does_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旅行卡片上的 ⌥ 点要静默（与普通照片同一取向）—— 只有影片卡片才去开网站。"""
    library = window._library
    assert library is not None
    trip = library.create_event(
        "trip", EventDraft(values={"title": "京都五日", "started_on": "2026-05-16"})
    ).event
    library.bind_photos([library.root / "普通照片.jpg"], trip.id)
    window.set_group_facet("event")
    assert wait_until(qt_app, lambda: window._model.group_count() > 0)
    opened = _spy_on_browser(monkeypatch)
    row = _row_of_event_card(window, trip.id)

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == []


def test_alt_clicking_a_plain_photo_does_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _spy_on_browser(monkeypatch)
    row = window._model.row_of_path(window._library.root / "普通照片.jpg")  # type: ignore[union-attr]

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == []


def test_an_imported_cover_appears_by_itself(qt_app: QApplication, window: MainWindow) -> None:
    """导入写的封面（临时文件 + ``os.replace``）要**自己**出现在网格里，不必切换浏览方式。

    这是「导入成功后不出现、改浏览方式才出现」那条的回归测试：原子写盘留下的是一对
    临时文件 → 真文件，监听器过去把这种移动当成改名丢掉，于是网格一直等下一次整层重扫。
    """
    library = window._library
    assert library is not None
    target = window._current_folder()
    assert target is not None

    created = save_downloaded_file(library.root, target, "新封面.jpg", _JPEG).created

    shown = wait_until(qt_app, lambda: window._model.row_of_path(created) >= 0)

    assert shown, "新封面没有自动出现在网格里"
