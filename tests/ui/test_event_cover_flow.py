"""「设为事件封面」与「清除事件封面」这两条命令的界面流程（ADR 0048 决策 6）。

落点与相册封面同款（ADR 0028 决策 1）：命令挂在**照片**上，一按就是；一张照片可以同时属于
好几种事件，所以底下多一层「是哪一次」。而**能不能摆出来**是这一份用例的主要内容 ——
摆一个按了没反应的行，比不摆更糟。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from library_helpers import set_library_ui
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu
from waiting import wait_for_idle, wait_until

from gallery.core.events.store import EventDraft
from gallery.core.library.config import BROWSE_MODE_ALBUMS, prepare_library
from gallery.core.library.photos import GroupRef
from gallery.core.library.session import SessionState, save_session
from gallery.ui.main_window import MainWindow

# 与 test_event_flow 同一套造库：一层三个文件，其中两个在同一天（分组时属同一个事件）。
PHOTOS = {
    "京都-01.jpg": "2026:05:16 10:00:00",
    "京都-02.jpg": "2026:05:16 11:00:00",
    "大阪-01.jpg": "2025:10:02 09:00:00",
}


def _photo(root: Path, name: str, taken_at: str) -> None:
    from PIL import Image

    image = Image.new("RGB", (12, 9), (40, 90, 160))
    image.save(root / name, "JPEG", exif=_exif(taken_at))


def _exif(taken_at: str) -> bytes:
    from PIL import Image

    exif = Image.Exif()
    exif[0x9003] = taken_at  # DateTimeOriginal
    exif[0x9010] = taken_at  # Model
    exif[0x9011] = taken_at  # Make
    return exif.tobytes()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "照片"
    library.mkdir(parents=True)
    for name, taken_at in PHOTOS.items():
        _photo(library, name, taken_at)
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
    wait_for_idle(qt_app, instance, what="扫描与地点解析")
    yield instance
    instance.close()


def _paths(window: MainWindow, *names: str) -> tuple[Path, ...]:
    root = window._library.root  # type: ignore[union-attr]
    return tuple(root / name for name in names)


def _row_of(window: MainWindow, name: str) -> int:
    return window._model.row_of_path(_paths(window, name)[0])


def _select(window: MainWindow, *names: str) -> None:
    """选中若干张并把当前项摆正（菜单的判据两者都要看，见 test_event_flow 里那条注释）。"""
    rows = [_row_of(window, name) for name in names]
    window._grid.setCurrentIndex(window._model.index(rows[0], 0))
    selection = window._grid.selectionModel()
    assert selection is not None
    selection.clearSelection()
    for row in rows:
        selection.select(
            window._model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    window._grid.select_for_context(rows[0])


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if action.text()]


def _submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        child = action.menu()
        if child is not None and action.text() == title:
            return child
    raise AssertionError(f"菜单里没有「{title}」：{_texts(menu)}")


def _one_event(window: MainWindow, title: str = "京都五日") -> int:
    return window._library.create_event(  # type: ignore[union-attr]
        "trip", EventDraft(values={"title": title})
    ).event.id


def _bind(window: MainWindow, event_id: int, *names: str) -> None:
    library = window._library
    assert library is not None
    library.bind_photos(_paths(window, *names), event_id)


def _row_of_card(window: MainWindow, event_id: int) -> int:
    """屏幕上那张事件卡片的行号（**按事件 id 认**，不按位置）。"""
    for row in range(window._model.rowCount()):
        entry = window._model.entry_at(row)
        if isinstance(entry, GroupRef) and entry.key.endswith(f"#{event_id}"):
            return row
    raise AssertionError(f"屏幕上没有事件 {event_id} 的卡片")


def _cover_name(window: MainWindow, event_id: int) -> str:
    """卡片画的那张的文件名。**空组没有封面** —— 那样断会拿到一个 AttributeError 而不是断言失败。"""
    cover = _group_card_of(window, event_id).cover
    return cover.path.name if cover is not None else ""


def _group_card_of(window: MainWindow, event_id: int) -> GroupRef:
    for entry in window._model.entries():
        if isinstance(entry, GroupRef) and entry.key.endswith(f"#{event_id}"):
            return entry
    raise AssertionError(f"屏幕上没有事件 {event_id} 的卡片")


# ---- 那一条摆不摆 ----------------------------------------------------------------


def test_it_shows_up_for_a_photo_that_belongs_to_an_event(
    qt_app: QApplication, window: MainWindow
) -> None:
    """选中一张属于某个事件的照片 → 「设为事件封面」在。"""
    _bind(window, _one_event(window), "京都-01.jpg")
    row = _row_of(window, "京都-01.jpg")

    _select(window, "京都-01.jpg")
    menu = window.context_menu_for(row)

    assert menu is not None
    assert "设为事件封面" in _texts(menu)


def test_it_stays_away_from_a_photo_in_no_event(qt_app: QApplication, window: MainWindow) -> None:
    """不属于任何事件的照片上**不出现**（不是置灰）—— 没有对象就没有这条命令
    （与相册封面同款处置，ADR 0028 决策 1）。"""
    _bind(window, _one_event(window), "京都-01.jpg")
    row = _row_of(window, "大阪-01.jpg")

    _select(window, "大阪-01.jpg")
    menu = window.context_menu_for(row)

    assert menu is not None
    assert "设为事件封面" not in _texts(menu)


def test_it_stays_away_when_several_photos_are_selected(
    qt_app: QApplication, window: MainWindow
) -> None:
    """多选时**不出现**：把哪一张当封面说不清，与「重命名…」同一个理由。"""
    event_id = _one_event(window)
    _bind(window, event_id, "京都-01.jpg", "京都-02.jpg")
    row = _row_of(window, "京都-01.jpg")

    _select(window, "京都-01.jpg", "京都-02.jpg")
    menu = window.context_menu_for(row)

    assert menu is not None
    assert "设为事件封面" not in _texts(menu)


def test_it_stays_away_when_the_event_library_is_gone(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事件库用不上时这一条同样不出现 —— 与「记录 as…」走同一道判据（ADR 0043 决策 1）。

    **这里改判据而不造一个建不出配置目录的库**：``set_library_ui`` 自己也要往那个配置目录里
    写 ``config.toml``，于是在「目录建不出来」的库上它自己先失败了（实测 FileExistsError）。
    造不出配置目录那件事在 ``tests/core/events/test_store.py`` 里量过一次，那一层的断言
    「``attach`` 返回 ``None``」已经盯住了它；这里要盯的是**界面据此不摆这一条**。
    """
    monkeypatch.setattr(type(window._library), "events_available", property(lambda _self: False))
    _bind(window, _one_event(window), "京都-01.jpg")
    row = _row_of(window, "京都-01.jpg")

    _select(window, "京都-01.jpg")
    menu = window.context_menu_for(row)

    assert menu is not None
    assert "设为事件封面" not in _texts(menu)


# ---- 子菜单列的是「它属于哪些事件」----------------------------------------------


def test_the_submenu_lists_every_event_that_photo_belongs_to(
    qt_app: QApplication, window: MainWindow
) -> None:
    """一张照片可以同时属于一次演出与一次旅行，所以**要问是哪一次**。"""
    trip, show = _one_event(window, "京都五日"), _one_event(window, "岚山演出")
    _bind(window, trip, "京都-01.jpg")
    _bind(window, show, "京都-01.jpg")
    row = _row_of(window, "京都-01.jpg")

    _select(window, "京都-01.jpg")
    menu = window.context_menu_for(row)
    assert menu is not None
    layer = _submenu(menu, "设为事件封面")
    layer.aboutToShow.emit()

    assert sorted(_texts(layer)) == ["京都五日", "岚山演出"]


def test_the_card_shows_clear_cover_only_once_a_cover_is_set(
    qt_app: QApplication, window: MainWindow
) -> None:
    """没设过封面时卡片上**不出现**「清除事件封面」—— 没有需要撤销的东西。"""
    event_id = _one_event(window)
    _bind(window, event_id, "京都-01.jpg")
    window.set_group_facet("event")
    wait_for_idle(qt_app, window, what="按事件分组")

    menu = window.context_menu_for(window._model.rowCount() - 1)

    assert menu is not None
    assert "清除事件封面" not in _texts(menu)


# ---- 按下去之后 ------------------------------------------------------------------


def test_setting_the_cover_changes_the_card_right_away(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**当场重画**：那张卡片画的就是刚指定的那张 —— 不管它在当前排序里排第几。

    「按文件名分组」时第一张是 ``京都-01.jpg``，所以要指定 ``京都-02.jpg`` 才看得出差别。
    """
    event_id = _one_event(window)
    _bind(window, event_id, "京都-01.jpg", "京都-02.jpg")

    # 命令挂在**照片**上，所以先在平铺模式下选中它（分组那一层的行是组卡片）
    _select(window, "京都-02.jpg")
    window.set_selected_as_event_cover(event_id)
    window.set_group_facet("event")
    wait_for_idle(qt_app, window, what="按事件分组")

    # **等的是「卡片画的那张真的换了」**：``cover`` 永远不是 ``None``（它退回组内第一张），
    # 所以「等它不是 None」立刻就成立，于是重排还没回来这条就绿了。
    wait_until(qt_app, lambda: _cover_name(window, event_id) == "京都-02.jpg")

    assert _cover_name(window, event_id) == "京都-02.jpg"


def test_clearing_the_cover_puts_the_first_photo_back(
    qt_app: QApplication, window: MainWindow
) -> None:
    event_id = _one_event(window)
    _bind(window, event_id, "京都-01.jpg", "京都-02.jpg")
    library = window._library
    assert library is not None
    library.set_event_cover(event_id, _paths(window, "京都-02.jpg")[0])
    window.set_group_facet("event")
    wait_for_idle(qt_app, window, what="按事件分组")
    assert _cover_name(window, event_id) == "京都-02.jpg"

    # 命令挂在**卡片**上，所以先把光标落在那张卡片上（与右键那一条同一套判据）
    window._grid.setCurrentIndex(window._model.index(_row_of_card(window, event_id), 0))
    window._ensure_event_actions()
    window._clear_cover_of_group_card()
    wait_until(qt_app, lambda: _cover_name(window, event_id) == "京都-01.jpg")

    assert library.event(event_id).cover_path is None  # type: ignore[union-attr]
    assert _cover_name(window, event_id) == "京都-01.jpg"


def test_the_cover_survives_a_rename_and_the_card_still_shows_it(
    qt_app: QApplication, window: MainWindow
) -> None:
    """改名之后卡片画**新的那张**（同一张照片，路径变了）—— 这就是「封面跟着搬」在界面上的样子。"""
    event_id = _one_event(window)
    _bind(window, event_id, "京都-01.jpg", "京都-02.jpg")
    library = window._library
    assert library is not None
    library.set_event_cover(event_id, _paths(window, "京都-02.jpg")[0])

    # **改名要在平铺模式下做**：按事件分组那一层的行是组卡片，按路径找行号必然是 -1。
    window._grid.select_row(_row_of(window, "京都-02.jpg"))
    window.rename_current_entry()
    editor = window._grid._rename_edit
    editor.setText("京都-99.jpg")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    qt_app.processEvents()

    cover = library.event(event_id).cover_path  # type: ignore[union-attr]
    assert cover is not None and cover.name == "京都-99.jpg"

    window._after_events_changed()
    window.set_group_facet("event")
    wait_for_idle(qt_app, window, what="按事件分组")
    wait_until(qt_app, lambda: _cover_name(window, event_id) == "京都-99.jpg")

    assert _cover_name(window, event_id) == "京都-99.jpg"
