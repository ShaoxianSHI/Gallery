"""「最近打开」那份名单怎么列、怎么清空（ADR 0062）。

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

from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.external import recent_documents  # noqa: E402
from gallery.core.library.session import load_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 菜单末尾那一条动作，与空名单时那一句占位。
CLEAR_TEXT = "清除最近打开"
PLACEHOLDER_TEXT = "（还没有）"


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
    """菜单里那几条库（撇开分隔线、占位句与末尾那条「清除最近打开」）。"""
    return [
        action.text()
        for action in window._recent_menu.actions()
        if not action.isSeparator() and action.text() not in (CLEAR_TEXT, PLACEHOLDER_TEXT)
    ]


def _clear_action(window: MainWindow) -> QAction:
    return window._recent_menu.actions()[-1]


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
    assert _clear_action(window).text() == CLEAR_TEXT


def test_an_empty_list_says_so_and_offers_nothing_to_clear(
    window: MainWindow, tmp_path: Path
) -> None:
    """一条都没有时只剩那一句「（还没有）」：没有分隔线，也没有「清除最近打开」。"""
    window._remember(_folder(tmp_path, "库一"))
    window._forget_all_recent_roots()

    (only,) = window._recent_menu.actions()
    assert only.text() == PLACEHOLDER_TEXT
    assert only.isEnabled() is False


def test_clear_all_drops_the_other_roots(
    window: MainWindow, tmp_path: Path, published: list[tuple[Path, ...]]
) -> None:
    """「清除最近打开」：别的都清掉，交给系统的那一份跟着变。

    这里**还没打开过任何库**（欢迎页），所以清完就是空的。
    """
    window._remember(_folder(tmp_path, "库一"))
    window._remember(_folder(tmp_path, "库二"))

    _clear_action(window).trigger()

    assert _entries(window) == []
    assert load_session(base=tmp_path / "会话").recent_roots == ()
    assert published[-1] == ()
    assert window._recent_menu.actions()[0].text() == PLACEHOLDER_TEXT


def test_clear_all_keeps_the_library_that_is_open(
    window: MainWindow, tmp_path: Path, published: list[tuple[Path, ...]]
) -> None:
    """**当前正开着的那个库要留下** —— 它是开着的，本来就该在名单里。

    清空这一下不该把它自己也抹掉：那份名单答的是「最近打开过哪些库」，而它此刻就开着。
    """
    current = _folder(tmp_path, "正开着的")
    other = _folder(tmp_path, "之前开过的")
    window._remember(other)
    window._remember(current)
    assert window.open_library(current) is True

    _clear_action(window).trigger()

    assert _entries(window) == [str(current)]
    assert load_session(base=tmp_path / "会话").recent_roots == (current,)
    assert published[-1] == (current,)


def test_clear_all_is_idempotent_while_a_library_is_open(
    window: MainWindow, tmp_path: Path
) -> None:
    """清两次还是只有那一个：留下的是「此刻开着的」，不是「清空之前最前面那个」。"""
    current = _folder(tmp_path, "正开着的")
    window._remember(_folder(tmp_path, "之前开过的"))
    window._remember(current)
    window.open_library(current)

    _clear_action(window).trigger()
    _clear_action(window).trigger()

    assert _entries(window) == [str(current)]
