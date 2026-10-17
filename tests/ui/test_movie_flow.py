"""「导入影片」与「⌥ 点开影片网站」的界面接线（ADR 0063）。

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


def _make_movie(window: MainWindow, root: Path) -> None:
    """建一个影片事件，把封面绑进去并设为封面。"""
    library = window._library
    assert library is not None
    cover = library.root / "封面.jpg"
    outcome = library.create_event("movie", EventDraft.of(title="一部影片", code="IPX-660"))
    library.bind_photos([cover], outcome.event.id)
    library.set_event_cover(outcome.event.id, cover)


def test_the_import_menu_sits_after_view(window: MainWindow) -> None:
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles[-1] == "导入", f"「导入」应当在最后（视图右边），实际菜单栏是 {titles}"
    assert "视图" in titles
    labels = [action.text() for action in window._import_menu.actions()]
    assert labels == ["导入影片…", "从「演迹」导入演出…"]


def test_every_top_level_menu_belongs_to_the_menu_bar(window: MainWindow) -> None:
    """每个顶层菜单都要**认在菜单栏名下** —— 与「文件 / 编辑 / 视图」同一条路。

    一个没有父的 ``QMenu`` 加进菜单栏也不会被认成它的子菜单，于是 macOS 的原生菜单栏不按原生
    样式画它（「导入」曾经就是这么建出来的，看上去与相邻几个不一样）。
    """
    for action in window.menuBar().actions():
        menu = action.menu()
        assert menu is not None, f"「{action.text()}」不是一个菜单"
        assert menu.parent() is window.menuBar(), f"「{menu.title()}」没认在菜单栏名下"


def test_alt_clicking_a_movie_photo_opens_the_site(
    window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_movie(window, root)
    opened: list[str] = []

    def fake_open(url: str, **_kwargs: object) -> launch.OpenOutcome:
        opened.append(url)
        return launch.OpenOutcome(target_id="movie", where=launch.WHERE_BROWSER)

    monkeypatch.setattr("gallery.ui.window.event_flow.launch.open_in_browser", fake_open)
    row = window._model.row_of_path(window._library.root / "封面.jpg")  # type: ignore[union-attr]
    assert row >= 0

    window.open_movie_for_row(window._model.index(row, 0))

    assert opened == ["https://jable.tv/videos/ipx-660/"]


def test_alt_clicking_a_plain_photo_does_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fake_open(url: str, **_kwargs: object) -> launch.OpenOutcome:
        opened.append(url)
        return launch.OpenOutcome(target_id="movie", where=launch.WHERE_BROWSER)

    monkeypatch.setattr("gallery.ui.window.event_flow.launch.open_in_browser", fake_open)
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
