"""「设置 › 背景 › 管理背景…」那个对话框。

锁两件事：**改的是本地那一份计划**（「完成」之前不写缓存、不改清单），以及列表用的是
对话框那一套底色 —— 默认的列表在深色下像贴上去的一张白纸。名字那一侧锁空名字与超长：
两者都能把清单写成一条用不了的名字，而界面上不会有任何地方报错。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.backdrops import MAX_NAME_LENGTH  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.dialogs.backdrop import BackdropManageDialog  # noqa: E402

ITEMS = (("a", "晨雾"), ("b", "海浪"))


def _dialog(items: tuple[tuple[str, str], ...] = ITEMS) -> BackdropManageDialog:
    dialog = BackdropManageDialog(None, items)
    dialog.resize(460, 320)
    return dialog


def _texts(dialog: BackdropManageDialog) -> list[str]:
    return [dialog._list.item(row).text() for row in range(dialog._list.count())]


def test_it_lists_what_is_saved_and_starts_on_the_first_row(qt_app: QApplication) -> None:
    dialog = _dialog()

    assert _texts(dialog) == ["晨雾", "海浪"]
    assert dialog._list.currentRow() == 0
    dialog.deleteLater()


def test_renaming_a_row_lands_in_the_plan(qt_app: QApplication) -> None:
    dialog = _dialog()

    dialog._list.item(0).setText("  雾蓝  ")

    assert dialog.renames() == {"a": "雾蓝"}
    dialog.deleteLater()


def test_renaming_back_leaves_no_change(qt_app: QApplication) -> None:
    """改回原来的名字不该在清单里留下一条「等于默认」的改动。"""
    dialog = _dialog()

    dialog._list.item(0).setText("雾蓝")
    dialog._list.item(0).setText("晨雾")

    assert dialog.renames() == {}
    dialog.deleteLater()


def test_clearing_the_name_falls_back_to_the_one_it_had(qt_app: QApplication) -> None:
    dialog = _dialog()

    dialog._list.item(0).setText("   ")

    assert _texts(dialog)[0] == "晨雾"
    assert dialog.renames() == {}
    dialog.deleteLater()


def test_a_name_longer_than_the_limit_is_cut(qt_app: QApplication) -> None:
    dialog = _dialog()

    dialog._list.item(0).setText("长" * (MAX_NAME_LENGTH + 20))

    assert len(_texts(dialog)[0]) == MAX_NAME_LENGTH
    dialog.deleteLater()


def test_deleting_takes_the_row_and_the_name_away(qt_app: QApplication) -> None:
    dialog = _dialog()
    dialog._list.setCurrentRow(0)

    dialog._remove_button.click()

    assert dialog.removed() == ("a",)
    assert _texts(dialog) == ["海浪"]
    assert dialog.renames() == {}
    dialog.deleteLater()


def test_deleting_the_last_row_leaves_a_placeholder(qt_app: QApplication) -> None:
    """删光之后是一个空白的框，用户会以为对话框坏了。"""
    dialog = _dialog((("a", "晨雾"),))

    dialog._remove_button.click()

    assert _texts(dialog) == ["还没有保存过背景图"]
    assert dialog._rename_button.isEnabled() is False
    assert dialog._remove_button.isEnabled() is False
    dialog.deleteLater()


def test_rename_edits_the_row_in_place(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**不弹系统的输入框**：那个框一出现就和这个对话框不是一套。"""
    dialog = _dialog()
    seen: list[str] = []
    monkeypatch.setattr(dialog._list, "editItem", lambda item: seen.append(item.text()))

    dialog._rename_button.click()

    assert seen == ["晨雾"]
    dialog.deleteLater()


def _over(base: QColor, top: QColor) -> QColor:
    alpha = top.alphaF()
    return QColor(
        round(top.red() * alpha + base.red() * (1 - alpha)),
        round(top.green() * alpha + base.green() * (1 - alpha)),
        round(top.blue() * alpha + base.blue() * (1 - alpha)),
    )


def test_the_list_is_painted_with_the_dialog_style(qt_app: QApplication) -> None:
    """列表空白处就是对话框底上叠 6% 正文色 —— 与其余几个对话框同一档。"""
    dialog = _dialog()
    app_theme = theme.current_theme()
    listing = dialog._list
    expected = _over(app_theme.background, app_theme.text_at(0.06))

    image = dialog.grab().toImage()
    spot = listing.geometry().bottomLeft()
    painted = image.pixelColor(spot.x() + 20, spot.y() - 10)

    assert abs(painted.red() - expected.red()) <= 4
    assert abs(painted.green() - expected.green()) <= 4
    assert abs(painted.blue() - expected.blue()) <= 4
    dialog.deleteLater()
