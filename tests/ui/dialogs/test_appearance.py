"""「设置 › 背景 › 自定义背景色…」那个对话框（ADR 0097）。

锁的是**左右两档各自只收自己那一侧**：正文色取的是系统那一套，只换底不换字，挑反了就是
浅字压浅底 —— 而那种界面不会有任何地方报错。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QColorDialog  # noqa: E402

from gallery.ui.dialogs.appearance import BackgroundColorDialog  # noqa: E402


@pytest.fixture
def picked(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch):  # noqa: ARG001
    """把系统取色器换掉：返回的函数设成哪个颜色，下一次「点色块」就挑到哪个。"""

    def _pick(color: str) -> None:
        monkeypatch.setattr(
            QColorDialog, "getColor", staticmethod(lambda *args, **kwargs: QColor(color))
        )

    return _pick


def test_the_dialog_starts_from_what_is_stored(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = BackgroundColorDialog(None, light="#f5f2ec", dark="#101018")

    assert dialog.values() == ("#f5f2ec", "#101018")
    assert dialog._swatches[False].text() == "#f5f2ec"
    dialog.deleteLater()


def test_an_unset_slot_says_default(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = BackgroundColorDialog(None, light=None, dark="#101018")

    assert dialog._swatches[False].text() == "默认"
    assert dialog.values() == (None, "#101018")
    dialog.deleteLater()


def test_a_colour_on_the_wrong_side_is_refused(qt_app: QApplication, picked) -> None:  # noqa: ARG001
    """浅色那一档挑了深色：值不变，对话框里说清楚为什么。"""
    dialog = BackgroundColorDialog(None, light="#f5f2ec")
    picked("#101010")

    dialog._swatches[False].click()

    assert dialog.values() == ("#f5f2ec", None)
    assert "浅色那一档" in dialog._error.text()
    assert dialog._error.isHidden() is False
    dialog.deleteLater()


def test_a_colour_on_the_right_side_replaces_the_slot(
    qt_app: QApplication,  # noqa: ARG001
    picked,
) -> None:
    dialog = BackgroundColorDialog(None)
    picked("#F7F4EE")  # 大写与空格都容忍，存下来的是规范化的写法

    dialog._swatches[False].click()

    assert dialog.values() == ("#f7f4ee", None)
    assert dialog._error.isHidden() is True
    dialog.deleteLater()


def test_cancelling_the_colour_picker_changes_nothing(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取色器里按了取消，返回的是无效颜色 —— 不该被当成「挑了一个黑」。"""
    dialog = BackgroundColorDialog(None, light="#f5f2ec")
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *args, **kwargs: QColor()))

    dialog._swatches[False].click()

    assert dialog.values() == ("#f5f2ec", None)
    dialog.deleteLater()


def test_reset_clears_both_slots(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = BackgroundColorDialog(None, light="#f5f2ec", dark="#101018")

    dialog._reset_button.click()

    assert dialog.values() == (None, None)
    assert dialog._swatches[True].text() == "默认"
    dialog.deleteLater()
