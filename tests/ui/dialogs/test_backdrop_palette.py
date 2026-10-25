"""「编辑颜色」那个对话框：一套配色的颜色清单（ADR 0103）。

锁的是「看到什么就是什么」：每一行一个颜色，点击换色、可以增删，至少两个、最多八个；
它只交出颜色，怎么变成停点归调用方。编辑已有配色时还多一个出口：删掉整套（ADR 0104）。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QColorDialog,
    QDialog,
    QPushButton,
)

from gallery.ui.dialogs.backdrop import (  # noqa: E402
    MAX_PALETTE_COLOURS,
    MIN_PALETTE_COLOURS,
    BackdropPaletteDialog,
)

COLOURS = ("#dae3ed", "#cad7e4", "#cfcccc", "#dfc3b1", "#d6b299", "#d0a689")


def _dialog(
    colours: tuple[str, ...] = COLOURS, *, can_delete: bool = False
) -> BackdropPaletteDialog:
    return BackdropPaletteDialog(
        None, title="编辑颜色 · 雾蓝", colours=colours, can_delete=can_delete
    )


def _pick(monkeypatch: pytest.MonkeyPatch, color: str) -> None:
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *args, **kwargs: QColor(color))
    )


def _buttons(dialog: BackdropPaletteDialog, name: str) -> list[QPushButton]:
    return [button for button in dialog.findChildren(QPushButton) if button.objectName() == name]


def test_it_starts_with_the_palettes_colours(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    assert dialog.colours() == COLOURS
    assert len(_buttons(dialog, "paletteSwatch")) == len(COLOURS)
    dialog.deleteLater()


def test_clicking_a_swatch_replaces_that_colour(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick(monkeypatch, "#336699")

    _buttons(dialog, "paletteSwatch")[1].click()

    assert dialog.colours()[1] == "#336699"
    dialog.deleteLater()


def test_removing_drops_that_row(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    _buttons(dialog, "paletteRemove")[2].click()

    assert dialog.colours() == COLOURS[:2] + COLOURS[3:]
    dialog.deleteLater()


def test_it_will_not_go_below_two_colours(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog(COLOURS[:2])

    assert all(not button.isEnabled() for button in _buttons(dialog, "paletteRemove"))
    dialog._remove_colour(0)
    assert dialog.colours() == COLOURS[:2]
    assert len(dialog.colours()) == MIN_PALETTE_COLOURS
    dialog.deleteLater()


def test_adding_appends_a_colour(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick(monkeypatch, "#7a9e7e")

    dialog._add_button.click()

    assert dialog.colours()[-1] == "#7a9e7e"
    assert len(dialog.colours()) == len(COLOURS) + 1
    dialog.deleteLater()


def test_it_will_not_go_above_the_limit(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog(tuple(f"#00000{index}" for index in range(MAX_PALETTE_COLOURS)))

    assert dialog._add_button.isEnabled() is False
    dialog._add_colour()
    assert len(dialog.colours()) == MAX_PALETTE_COLOURS
    dialog.deleteLater()


def test_cancelling_the_picker_changes_nothing(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))

    _buttons(dialog, "paletteSwatch")[0].click()
    dialog._add_button.click()

    assert dialog.colours() == COLOURS
    dialog.deleteLater()


def test_a_brand_new_palette_has_no_delete_exit(qt_app: QApplication) -> None:  # noqa: ARG001
    """新增配色时还没有「这一套」可删，那个按钮不该出现（ADR 0104）。"""
    dialog = _dialog()

    assert _buttons(dialog, "paletteDelete") == []
    assert dialog.deleted() is False
    dialog.deleteLater()


def test_deleting_closes_as_accepted_and_reports_it(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog(can_delete=True)

    _buttons(dialog, "paletteDelete")[0].click()

    assert dialog.deleted() is True
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.deleteLater()


def test_saving_does_not_report_a_deletion(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog(can_delete=True)

    dialog.accept()

    assert dialog.deleted() is False
    dialog.deleteLater()
