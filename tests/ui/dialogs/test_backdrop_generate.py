"""「设置 › 背景 › 生成水墨背景…」那个对话框里「+」那一格（ADR 0101）。

锁的是：那一格**不填颜色、只有加号**；点它挑一个颜色之后，多出一格同形状的色块并当场选中，
再挑一次换的是同一格；取消则什么都不变。生成本身在别的测试里锁，这里一次只关心色板。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QColorDialog, QPushButton  # noqa: E402

from gallery.core.backdrops import CUSTOM_PRESET, PALETTES, palette_from_color  # noqa: E402
from gallery.ui.dialogs.backdrop import BackdropGenerateDialog  # noqa: E402


def _dialog() -> BackdropGenerateDialog:
    return BackdropGenerateDialog(None, seed=5)


def _chips(dialog: BackdropGenerateDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog.findChildren(QPushButton)
        if button.objectName() == "backdropChip"
    ]


def _pick(monkeypatch: pytest.MonkeyPatch, color: str) -> None:
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *args, **kwargs: QColor(color))
    )


def test_the_add_entry_is_an_empty_plus(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    add = dialog._add_chip

    assert add is not None
    assert add.text() == "+"
    assert add.isCheckable() is False
    assert add.objectName() == "backdropAdd"
    assert "transparent" in add.styleSheet()
    assert "solid" in add.styleSheet()
    dialog.deleteLater()


def test_picking_a_colour_adds_one_filled_chip_next_to_the_plus(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick(monkeypatch, "#336699")

    dialog._add_chip.click()

    chip = dialog._custom_chip
    assert chip is not None
    assert chip.isChecked() is True
    assert chip.toolTip() == "自定义 · #336699"
    assert "#336699" in chip.styleSheet()
    row = dialog._palette_row
    assert row.indexOf(chip) + 1 == row.indexOf(dialog._add_chip)
    assert len(_chips(dialog)) == len(PALETTES) + 1
    dialog.deleteLater()


def test_the_custom_palette_is_what_the_preview_uses(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick(monkeypatch, "#336699")

    dialog._add_chip.click()

    expected = palette_from_color("#336699")
    assert dialog.recipe().preset == CUSTOM_PRESET
    assert dialog.recipe().colors == expected.colors
    assert dialog.name() == expected.title
    dialog.deleteLater()


def test_picking_again_replaces_the_same_chip(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick(monkeypatch, "#336699")
    dialog._add_chip.click()
    first = dialog._custom_chip

    _pick(monkeypatch, "#b06a3b")
    dialog._add_chip.click()

    assert dialog._custom_chip is first, "第二次挑颜色不该再多一格"
    assert dialog._custom_chip.toolTip() == "自定义 · #b06a3b"
    assert dialog.recipe().colors == palette_from_color("#b06a3b").colors
    assert len(_chips(dialog)) == len(PALETTES) + 1
    dialog.deleteLater()


def test_cancelling_the_picker_changes_nothing(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    before = dialog.recipe()
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))

    dialog._add_chip.click()

    assert dialog._custom_chip is None
    assert dialog.recipe() == before
    dialog.deleteLater()


def test_clicking_the_custom_chip_returns_to_it_after_a_preset(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自定义那一格的点击回调要在**点的时候**读当前的配色，不能抓住第一次那个对象。"""
    dialog = _dialog()
    _pick(monkeypatch, "#336699")
    dialog._add_chip.click()
    _pick(monkeypatch, "#b06a3b")
    dialog._add_chip.click()

    dialog._swatches[PALETTES[0].name].click()
    assert dialog.recipe().preset == PALETTES[0].name

    dialog._custom_chip.click()

    assert dialog.recipe().preset == CUSTOM_PRESET
    assert dialog.recipe().colors == palette_from_color("#b06a3b").colors
    dialog.deleteLater()
