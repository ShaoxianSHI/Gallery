"""「设置 › 背景 › 生成水墨背景…」那个对话框里「+」那一格（ADR 0101 / 0102）。

锁的是：那一格**不填颜色、只有加号**；每点一次追加一个颜色，自定义色块按这几个颜色显示
渐变并当场选中；最多三个，到了上限「+」按不动；右键色块可以重新挑选。生成本身在别的
测试里锁，这里一次只关心色板。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from outline_helpers import assert_no_darker_dots  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QColorDialog, QPushButton  # noqa: E402

from gallery.core.backdrops import (  # noqa: E402
    CUSTOM_PRESET,
    PALETTES,
    palette_from_color,
    palette_from_colors,
)
from gallery.ui import theme  # noqa: E402
from gallery.ui.dialogs.backdrop import (  # noqa: E402
    MAX_CUSTOM_COLORS,
    BackdropGenerateDialog,
)
from gallery.ui.outline import opaque_over  # noqa: E402


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


def _pick_many(
    dialog: BackdropGenerateDialog, monkeypatch: pytest.MonkeyPatch, *colors: str
) -> None:
    for color in colors:
        _pick(monkeypatch, color)
        dialog._add_chip.click()


def test_the_add_entry_is_an_empty_plus(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    add = dialog._add_chip

    assert add is not None
    assert add.text() == "+"
    assert add.isCheckable() is False
    assert add.objectName() == "backdropAdd"
    assert "transparent" in add.styleSheet()
    assert "solid" in add.styleSheet()
    # 边框要按对话框那层底色合成成不透明的（半透明会在圆角接缝叠出深点，ADR 0035）。
    app_theme = theme.current_theme()
    expected = opaque_over(app_theme.text_at(0.35), app_theme.background)
    assert expected.name() in add.styleSheet()
    dialog.deleteLater()


def test_the_chip_outline_has_no_darker_corners(qt_app: QApplication) -> None:
    """色块那一圈**八个等分点不比别处更深**（量法在 :mod:`tests.ui.outline_helpers`）。

    半透明的边框被 Qt 描成八段，接缝处调两遍就叠成更深的点 —— 所以边框颜色要预先与色块
    自己的底色合成成不透明的（ADR 0035）。渐变那一格按平均色合成，这里也按平均色量。
    """
    dialog = _dialog()
    dialog.show()
    qt_app.processEvents()
    fill = QColor(PALETTES[0].colors[-2][1])
    chip = dialog._swatches[PALETTES[0].name]

    assert_no_darker_dots(chip, fill.lightness(), radius=5, label="配色色块")

    chip.setChecked(True)
    qt_app.processEvents()
    assert_no_darker_dots(chip, fill.lightness(), radius=5, label="选中时的配色色块")
    dialog.deleteLater()


def test_the_chip_border_is_composited_to_an_opaque_colour(qt_app: QApplication) -> None:  # noqa: ARG001
    """样式表里那两档边框都是不透明色：颜色值就是「压在色块底色上」之后该有的那一档。"""
    dialog = _dialog()
    fill = QColor(PALETTES[0].colors[-2][1])
    expected_edge = opaque_over(QColor(0, 0, 0, 30), fill)
    expected_checked = opaque_over(QColor(0, 0, 0, 140), fill)

    style = dialog._swatches[PALETTES[0].name].styleSheet()

    assert expected_edge.name() in style
    assert expected_checked.name() in style
    assert "rgba(0,0,0,0.12)" not in style
    assert "rgba(0, 0, 0, 0.55)" not in style
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
    assert chip.toolTip().startswith("自定义 · #336699")
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


def test_a_second_colour_appends_to_a_gradient(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再点一次「+」是**追加**一个色相，不是替换掉第一个。"""
    dialog = _dialog()
    _pick_many(dialog, monkeypatch, "#336699", "#b06a3b")

    expected = palette_from_colors(["#336699", "#b06a3b"])
    style = dialog._custom_chip.styleSheet()
    assert dialog.recipe().colors == expected.colors
    assert dialog.name() == expected.title
    assert "qlineargradient" in style
    assert "#336699" in style and "#b06a3b" in style
    assert len(_chips(dialog)) == len(PALETTES) + 1, "两个颜色合成一格，不是两格"
    dialog.deleteLater()


def test_the_third_colour_is_the_last_one_it_takes(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick_many(dialog, monkeypatch, "#336699", "#b06a3b", "#7a9e7e")

    assert "→".join(["#336699", "#b06a3b", "#7a9e7e"]).replace("→", " → ") in dialog.name()
    assert dialog._add_chip.isEnabled() is False
    assert "最多三个颜色" in dialog._add_chip.toolTip()
    assert len(dialog._custom_colors) == MAX_CUSTOM_COLORS
    dialog.deleteLater()


def test_resetting_removes_the_custom_chip_and_goes_back_to_a_preset(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _pick_many(dialog, monkeypatch, "#336699", "#b06a3b")

    dialog._reset_custom_colors()

    assert dialog._custom_chip is None
    assert dialog._custom_palette is None
    assert dialog._custom_colors == []
    assert dialog._add_chip.isEnabled() is True
    assert dialog.recipe().preset == PALETTES[0].name
    assert len(_chips(dialog)) == len(PALETTES)
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
    _pick_many(dialog, monkeypatch, "#336699", "#b06a3b")

    dialog._swatches[PALETTES[0].name].click()
    assert dialog.recipe().preset == PALETTES[0].name

    dialog._custom_chip.click()

    assert dialog.recipe().preset == CUSTOM_PRESET
    assert dialog.recipe().colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    dialog.deleteLater()
