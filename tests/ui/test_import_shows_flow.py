"""「文件 → 导入演出 → 从「演迹」导入」那条路（ADR 0047）。

真正的解析在 ``tests/core/events/test_import_shows.py`` 里测；这里测的是**接线**：菜单里有没有
那一项、点下去认不认选中的文件、结果有没有变成事件与一句话。
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog, QMenu  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

HEADER = ("演出名称", "艺人", "主题/剧目", "日期", "城市", "场馆", "评分")


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_xlsx(path: Path, rows: list[list[object]]) -> Path:
    """一个最小可读的 .xlsx（内联字符串 + 数字日期），够这一层读了。"""
    body = []
    for number, row in enumerate([HEADER, *rows], start=1):
        cells = ""
        for index, value in enumerate(row):
            if value is None or value == "":
                continue
            reference = f"{_column(index)}{number}"
            if isinstance(value, (int, float)):
                cells += f'<c r="{reference}"><v>{value}</v></c>'
            else:
                cells += (
                    f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                )
        body.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "照片"
    library.mkdir(parents=True)
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描与地点解析")
    yield instance
    instance.close()


def _file_menu(window: MainWindow) -> QMenu:
    menu = window.menuBar().actions()[0].menu()
    assert menu is not None
    return menu


def _submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None and submenu.title() == title:
            return submenu
    raise AssertionError(f"菜单上没有「{title}」：{[a.text() for a in menu.actions()]}")


def _import_action(window: MainWindow):
    menu = _submenu(_file_menu(window), "导入演出")
    return next(action for action in menu.actions() if action.text() == "从「演迹」导入")


def _choose(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    answer = ("", "") if path is None else (str(path), "")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: answer)


def test_the_file_menu_carries_the_import_entry(window: MainWindow) -> None:
    """「文件」里有一层「导入演出」，里面那一条叫「从「演迹」导入」。"""
    menu = _submenu(_file_menu(window), "导入演出")

    assert [action.text() for action in menu.actions()] == ["从「演迹」导入"]


def test_choosing_a_table_creates_the_shows(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """选中一张表 → 每一行变成一条演出事件 → 横幅报出数量。"""
    library = window._library
    assert library is not None
    table = _write_xlsx(
        tmp_path / "演出.xlsx",
        [
            ["某演唱会", "某乐队", "某剧目", 46158.479166, "西安", "某场馆", 5],
            ["某话剧", "", "某剧", 46159.0, "北京", "某剧院", 0],
        ],
    )
    _choose(monkeypatch, table)

    _import_action(window).trigger()

    events = library.events_by_kind("show")
    assert sorted(event.title for event in events) == ["某演唱会", "某话剧"]
    assert "已从「演迹」导入 2 场演出" in window._banner.text()


def test_cancelling_the_file_dialog_changes_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = window._library
    assert library is not None
    _choose(monkeypatch, None)

    _import_action(window).trigger()

    assert library.events_by_kind("show") == ()


def test_a_bad_table_says_why_and_creates_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = window._library
    assert library is not None
    bad = tmp_path / "不是表格.xlsx"
    bad.write_text("这不是一个 zip", encoding="utf-8")
    _choose(monkeypatch, bad)

    _import_action(window).trigger()

    assert library.events_by_kind("show") == ()
    assert "不是「演迹」导出的" in window._banner.text()


def test_the_entry_is_greyed_without_an_event_store(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事件库用不上时那一层置灰，而不是点下去才报错。"""
    library = window._library
    assert library is not None
    library.events._connection = None

    window._update_event_actions()

    assert _submenu(_file_menu(window), "导入演出").isEnabled() is False
