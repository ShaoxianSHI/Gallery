"""「最近打开」那份名单的增删：怎么列、怎么逐条拿掉、怎么一次清空（ADR 0062）。

两处投影（应用内那份菜单与系统那一份，Dock 右键菜单读的就是后者）都从那一条 ``recent_roots``
派生，所以这里同时盯着菜单、会话文件与「交给系统的那一份」。**交给系统那一次换成记账的假实现**
—— 真去登记动的是本机的「最近项目」。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QAction, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.external import recent_documents  # noqa: E402
from gallery.core.library.session import load_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """一个还没有打开任何库的窗口。"""
    instance = MainWindow(session_base=tmp_path / "会话")
    instance.resize(900, 600)
    instance.show()
    yield instance
    instance.close()


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, ...]]:
    """每次「交给系统那一份」都记下来。"""
    calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(recent_documents, "publish", lambda roots: calls.append(tuple(roots)))
    return calls


def _folder(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def _entries(window: MainWindow) -> list[str]:
    """菜单里那几条库（撇开分隔线与「清除最近打开」）。"""
    return [action.text() for action in window._recent_menu.actions() if action.data()]


def _clear_action(window: MainWindow) -> QAction:
    return window._recent_menu.actions()[-1]


def _press(window: MainWindow, action: QAction, button: Qt.MouseButton) -> QMouseEvent:
    """在那一条上按下一个键（坐标取它的中心）。

    条目的位置要**展开之后**才算得出来，所以调用方得先 ``popup()`` 一次。
    """
    local = window._recent_menu.actionGeometry(action).center()
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(local),
        QPointF(window._recent_menu.mapToGlobal(local)),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def test_the_menu_lists_the_roots_then_a_separator_and_clear(
    window: MainWindow, tmp_path: Path
) -> None:
    """新的在前，末尾是分隔线加「清除最近打开」。"""
    first = _folder(tmp_path, "库一")
    second = _folder(tmp_path, "库二")

    window._remember(first)
    window._remember(second)

    assert _entries(window) == [str(second), str(first)]
    assert window._recent_menu.actions()[-2].isSeparator() is True
    assert _clear_action(window).text() == "清除最近打开"


def test_an_empty_list_says_so_and_offers_nothing_to_clear(
    window: MainWindow, tmp_path: Path
) -> None:
    """一条都没有时只剩那一句「（还没有）」：没有分隔线，也没有「清除最近打开」。"""
    window._remember(_folder(tmp_path, "库一"))
    window._forget_all_recent_roots()

    (only,) = window._recent_menu.actions()
    assert only.text() == "（还没有）"
    assert only.isEnabled() is False


def test_forgetting_one_root_leaves_the_others(
    window: MainWindow, tmp_path: Path, published: list[tuple[Path, ...]]
) -> None:
    """逐条拿掉：菜单、会话文件、交给系统的那一份三处一起变，**磁盘上那个文件夹不动**。"""
    first = _folder(tmp_path, "库一")
    second = _folder(tmp_path, "库二")
    window._remember(first)
    window._remember(second)

    window._forget_recent_root(second)

    assert _entries(window) == [str(first)]
    assert load_session(base=tmp_path / "会话").recent_roots == (first,)
    assert published[-1] == (first,)
    assert second.is_dir() is True, "「只增不改」：拿掉的只是我们记着的那一条"


def test_forgetting_the_same_root_twice_is_harmless(window: MainWindow, tmp_path: Path) -> None:
    first = _folder(tmp_path, "库一")
    window._remember(first)

    window._forget_recent_root(first)
    window._forget_recent_root(first)

    assert _entries(window) == []
    assert load_session(base=tmp_path / "会话").recent_roots == ()


def test_clear_all_drops_every_root(
    window: MainWindow, tmp_path: Path, published: list[tuple[Path, ...]]
) -> None:
    """「清除最近打开」= 整份清空，交给系统的那一份也跟着空。"""
    window._remember(_folder(tmp_path, "库一"))
    window._remember(_folder(tmp_path, "库二"))

    _clear_action(window).trigger()

    assert _entries(window) == []
    assert load_session(base=tmp_path / "会话").recent_roots == ()
    assert published[-1] == ()
    assert window._recent_menu.actions()[0].text() == "（还没有）"


def test_the_removal_menu_names_the_folder_it_takes_away(
    window: MainWindow, tmp_path: Path
) -> None:
    """右键弹出来的那个小菜单。点不点由 ``_offer_recent_removal`` 决定，这里只看它长什么样。"""
    menu = window._recent_item_menu(tmp_path / "库一")
    try:
        (action,) = menu.actions()
        assert action.text() == "从列表中移除「库一」"
    finally:
        menu.deleteLater()


def test_choosing_remove_in_the_popup_takes_the_root_away(
    window: MainWindow,
    tmp_path: Path,
    published: list[tuple[Path, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在那个小菜单里选了「从列表中移除」：**先收掉那份还开着的老菜单**，再把那一条拿掉。

    小菜单换成桩（``exec`` 说「选了那一项」），因为真的弹一次会转起一个嵌套事件循环 ——
    那正是这一条要避开的东西。
    """
    root = _folder(tmp_path, "库一")
    window._remember(root)
    monkeypatch.setattr(window, "_recent_item_menu", lambda _root: _StubPopup(chosen=True))
    window._recent_menu.popup(QPoint(60, 60))

    window._offer_recent_removal(root, QPoint(60, 60))

    assert _entries(window) == []
    assert window._recent_menu.isVisible() is False, "重建之前先把那份老菜单收掉"
    assert published[-1] == ()


def test_dismissing_the_popup_changes_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """那一项没被选中（用户按了 Esc 或点了别处）：名单原样不动。"""
    root = _folder(tmp_path, "库一")
    window._remember(root)
    monkeypatch.setattr(window, "_recent_item_menu", lambda _root: _StubPopup(chosen=False))

    window._offer_recent_removal(root, QPoint(60, 60))

    assert _entries(window) == [str(root)]


class _StubPopup:
    """小菜单的替身：``exec`` 直接回答「用户选没选中那一项」。"""

    def __init__(self, *, chosen: bool) -> None:
        self._chosen = chosen

    def exec(self, *_args: object) -> object | None:
        return object() if self._chosen else None

    def deleteLater(self) -> None:  # noqa: N802 - 名字是 Qt 定的
        """真菜单用完就扔（见 ``_offer_recent_removal``），替身不用接。"""


def test_right_clicking_a_recent_entry_offers_to_remove_it(
    qt_app: QApplication,
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """右键那一条：**不被当成一次「打开这个库」**，而是弹那个小菜单。

    弹出安排在下一轮事件循环里（见 ``_recent_removal_at``），所以这里要转一圈事件循环。
    """
    root = _folder(tmp_path, "库一")
    window._remember(root)
    offered: list[Path] = []
    monkeypatch.setattr(window, "_offer_recent_removal", lambda target, _at: offered.append(target))
    entry = window._recent_menu.actions()[0]
    window._recent_menu.popup(QPoint(60, 60))
    try:
        press = _press(window, entry, Qt.MouseButton.RightButton)
        handled = window.eventFilter(window._recent_menu, press)
        qt_app.processEvents()
    finally:
        window._recent_menu.close()

    assert handled is True, "这一下要被吞掉，否则 QMenu 会顺手打开这个库"
    assert window._library is None
    assert offered == [root]


def test_left_clicking_a_recent_entry_is_left_alone(window: MainWindow, tmp_path: Path) -> None:
    """左键那一下不拦：它走 Qt 自己那条「触发 action」的路（``triggered`` → ``open_library``）。"""
    window._remember(_folder(tmp_path, "库一"))
    entry = window._recent_menu.actions()[0]
    window._recent_menu.popup(QPoint(60, 60))
    try:
        press = _press(window, entry, Qt.MouseButton.LeftButton)
        handled = window.eventFilter(window._recent_menu, press)
    finally:
        window._recent_menu.close()

    assert handled is False


def test_right_clicking_something_with_nothing_to_remove_is_left_alone(
    window: MainWindow, tmp_path: Path
) -> None:
    """分隔线与「清除最近打开」没有那条路径，右键它们不该弹出「从列表中移除」。"""
    window._remember(_folder(tmp_path, "库一"))
    separator = window._recent_menu.actions()[-2]
    clear = _clear_action(window)
    assert separator.data() is None, "分隔线不是一条最近打开"
    window._recent_menu.popup(QPoint(60, 60))
    try:
        on_separator = window.eventFilter(
            window._recent_menu, _press(window, separator, Qt.MouseButton.RightButton)
        )
        on_clear = window.eventFilter(
            window._recent_menu, _press(window, clear, Qt.MouseButton.RightButton)
        )
    finally:
        window._recent_menu.close()

    assert on_separator is False
    assert on_clear is False
