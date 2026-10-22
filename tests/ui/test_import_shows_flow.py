"""「文件 → 导入演出 → 从「演迹」导入…」那条路（ADR 0047 / 0072）。

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

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox  # noqa: E402
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


def _import_menu(window: MainWindow) -> QMenu:
    return window._import_shows_menu


def _import_action(window: MainWindow):
    return next(
        action for action in _import_menu(window).actions() if action.text() == "从「演迹」导入…"
    )


def _choose(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    answer = ("", "") if path is None else (str(path), "")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: answer)


def test_the_import_menu_carries_the_shows_entry(window: MainWindow) -> None:
    """「导入演出」子菜单里只放「从「演迹」导入…」这一条（ADR 0072）。"""
    labels = [action.text() for action in _import_menu(window).actions()]

    assert labels == ["从「演迹」导入…"]


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

    assert _import_menu(window).isEnabled() is False
    assert window._import_movies_menu.isEnabled() is False


# ---- 重复（ADR 0047：写库之前先问一句）-----------------------------------------


def _two_shows(path: Path) -> Path:
    return _write_xlsx(
        path,
        [
            ["某演唱会", "某乐队", "某剧目", 46158.479166, "西安", "某场馆", 5],
            ["某话剧", "", "某剧", 46159.0, "北京", "某剧院", 0],
        ],
    )


def test_plan_counts_what_is_already_in_the_library(
    qt_app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    """预演只读不写：重复按名称 + 日期 + 场馆数出来。"""
    library = window._library
    assert library is not None
    table = _two_shows(tmp_path / "演出.xlsx")

    assert library.plan_show_import(table).duplicates == 0
    library.import_shows(library.plan_show_import(table), skip_duplicates=True)

    assert library.plan_show_import(table).duplicates == 2


def test_importing_again_can_skip_the_duplicates(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一个文件导两次、选「跳过重复」：库里还是那两场。"""
    library = window._library
    assert library is not None
    table = _two_shows(tmp_path / "演出.xlsx")
    _choose(monkeypatch, table)
    _import_action(window).trigger()
    assert len(library.events_by_kind("show")) == 2

    monkeypatch.setattr(window, "_ask_about_duplicate_shows", lambda plan: True)
    _import_action(window).trigger()

    assert len(library.events_by_kind("show")) == 2
    assert "跳过 2 场已存在的" in window._banner.text()


def test_importing_again_can_keep_everything(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """选「全部导入」就照建不误 —— 同名同日同场馆确实可能是两场。"""
    library = window._library
    assert library is not None
    table = _two_shows(tmp_path / "演出.xlsx")
    _choose(monkeypatch, table)
    _import_action(window).trigger()

    monkeypatch.setattr(window, "_ask_about_duplicate_shows", lambda plan: False)
    _import_action(window).trigger()

    assert len(library.events_by_kind("show")) == 4
    assert "已从「演迹」导入 2 场演出" in window._banner.text()
    assert "跳过" not in window._banner.text()


def test_cancelling_the_duplicate_prompt_changes_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = window._library
    assert library is not None
    table = _two_shows(tmp_path / "演出.xlsx")
    _choose(monkeypatch, table)
    _import_action(window).trigger()

    monkeypatch.setattr(window, "_ask_about_duplicate_shows", lambda plan: None)
    _import_action(window).trigger()

    assert len(library.events_by_kind("show")) == 2


def test_the_duplicate_dialog_answers_with_the_clicked_button(
    qt_app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    """真弹那个框、真点按钮：跳过 → True，全部导入 → False，取消 → None。"""
    library = window._library
    assert library is not None
    table = _two_shows(tmp_path / "演出.xlsx")
    library.import_shows(library.plan_show_import(table), skip_duplicates=True)
    plan = library.plan_show_import(table)
    assert plan.duplicates == 2

    def click(label: str) -> None:
        def act() -> None:
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox)
            button = next(candidate for candidate in box.buttons() if candidate.text() == label)
            button.click()

        QTimer.singleShot(0, act)

    click("跳过重复")
    assert window._ask_about_duplicate_shows(plan) is True
    click("全部导入")
    assert window._ask_about_duplicate_shows(plan) is False
    click("取消")
    assert window._ask_about_duplicate_shows(plan) is None
