"""「设置 › 背景 › 生成水墨背景…」那个对话框的色板（ADR 0103 / 0104）。

锁的是三件事各是一个入口：**双击一格 = 编辑这一套配色的颜色**（预设也能改，改完还在原位），
**编辑对话框里的「删除这套配色」= 这一格消失**，**右边那个「+」= 新增一套配色**
（不是给某一套加颜色）。生成本身在别的测试里锁。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from outline_helpers import assert_no_darker_dots  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

from gallery.core.backdrops import (  # noqa: E402
    CUSTOM_PRESET,
    PALETTES,
    palette_from_color,
    palette_from_colors,
)
from gallery.ui import theme  # noqa: E402
from gallery.ui.dialogs import backdrop as backdrop_module  # noqa: E402
from gallery.ui.dialogs.backdrop import (  # noqa: E402
    MAX_ADDED_PALETTES,
    MAX_PALETTES,
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


DELETE = object()


def _fake_palette_dialog(
    monkeypatch: pytest.MonkeyPatch, *answers: tuple[str, ...] | None | object
) -> list[tuple[str, tuple[str, ...], bool]]:
    """把「编辑颜色」对话框换成一个按剧本作答的桩；返回每次被问到的 (标题, 初始颜色, 能否删除)。

    ``None`` 表示用户按了取消，``DELETE`` 表示按了「删除这套配色」。
    """
    seen: list[tuple[str, tuple[str, ...], bool]] = []
    queue = list(answers)

    class Fake:
        def __init__(
            self,
            parent: object,
            *,
            title: str,
            colours: tuple[str, ...],
            can_delete: bool = False,
        ) -> None:
            seen.append((title, tuple(colours), can_delete))
            self.answer = queue.pop(0)

        def exec(self) -> QDialog.DialogCode:
            return (
                QDialog.DialogCode.Rejected if self.answer is None else QDialog.DialogCode.Accepted
            )

        def deleted(self) -> bool:
            return self.answer is DELETE

        def colours(self) -> tuple[str, ...]:
            if isinstance(self.answer, (list, tuple)):
                return tuple(self.answer)
            return ()

    monkeypatch.setattr(backdrop_module, "BackdropPaletteDialog", Fake)
    return seen


def test_the_add_entry_is_an_empty_plus(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    add = dialog._add_chip

    assert add is not None
    assert add.text() == "+"
    assert add.isCheckable() is False
    assert add.objectName() == "backdropAdd"
    assert add.toolTip() == "新增一套配色"
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
    自己的底色合成成不透明的（ADR 0035）。每一格都是一条渐变，边框按平均色合成。
    """
    dialog = _dialog()
    dialog.show()
    qt_app.processEvents()
    chip = dialog._buttons[0]
    mid = QColor(PALETTES[0].colors[len(PALETTES[0].colors) // 2][1])

    assert_no_darker_dots(chip, mid.lightness(), radius=5, label="配色色块")

    chip.setChecked(True)
    qt_app.processEvents()
    assert_no_darker_dots(chip, mid.lightness(), radius=5, label="选中时的配色色块")
    dialog.deleteLater()


def test_a_chip_shows_the_whole_ramp(qt_app: QApplication) -> None:  # noqa: ARG001
    dialog = _dialog()

    style = dialog._buttons[0].styleSheet()

    assert "qlineargradient" in style
    assert PALETTES[0].colors[0][1] in style
    assert PALETTES[0].colors[-1][1] in style
    dialog.deleteLater()


def test_the_add_entry_creates_a_new_palette(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    seen = _fake_palette_dialog(monkeypatch, ("#336699", "#b06a3b"))
    before = len(dialog._palettes)

    dialog._add_chip.click()

    assert len(dialog._palettes) == before + 1
    created = dialog._palettes[-1]
    assert created.colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    assert dialog._palette is created, "新增的那一套当场选中"
    assert len(_chips(dialog)) == before + 1
    assert dialog._buttons[-1].isChecked() is True
    assert seen and seen[-1][0] == "新增配色"
    dialog.deleteLater()


def test_the_add_entry_opens_with_a_starter_ramp(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「+」打开的是一个**已经有内容的**起点，不是一张空表。"""
    dialog = _dialog()
    seen = _fake_palette_dialog(monkeypatch, None)

    dialog._add_chip.click()

    assert len(seen[0][1]) == len(palette_from_color("#8ea8bf").colors)
    dialog.deleteLater()


def test_cancelling_the_new_palette_adds_nothing(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(monkeypatch, None)
    before = len(dialog._palettes)

    dialog._add_chip.click()

    assert len(dialog._palettes) == before
    assert dialog._palette is PALETTES[0]
    dialog.deleteLater()


def test_the_add_entry_stops_after_the_limit(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(
        monkeypatch,
        *(["#336699", "#b06a3b"] for _ in range(MAX_ADDED_PALETTES)),
    )

    for _ in range(MAX_ADDED_PALETTES):
        dialog._add_chip.click()

    assert dialog._add_chip.isEnabled() is False
    assert f"最多 {MAX_PALETTES} 套" in dialog._add_chip.toolTip()
    assert len(dialog._palettes) == MAX_PALETTES
    dialog.deleteLater()


def test_double_clicking_a_chip_edits_that_palette(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预设也改得：双击那一格，改完还在那一格上。"""
    dialog = _dialog()
    seen = _fake_palette_dialog(monkeypatch, ("#336699", "#b06a3b"))

    dialog._buttons[0].double_clicked.emit()

    assert seen[0][0] == f"编辑颜色 · {PALETTES[0].title}"
    assert seen[0][1] == tuple(color for _, color in PALETTES[0].colors)
    edited = dialog._palettes[0]
    assert edited.name == CUSTOM_PRESET
    assert edited.title == PALETTES[0].title
    assert edited.colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    assert len(dialog._palettes) == len(PALETTES), "编辑不新增一格"
    dialog.deleteLater()


def test_editing_the_selected_palette_updates_the_preview(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(monkeypatch, ("#336699", "#b06a3b"))

    dialog._buttons[0].double_clicked.emit()

    assert dialog._palette is dialog._palettes[0]
    assert dialog.recipe().colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    assert "qlineargradient" in dialog._buttons[0].styleSheet()
    dialog.deleteLater()


def test_editing_another_chip_keeps_the_current_palette(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(monkeypatch, ("#336699", "#b06a3b"))

    dialog._buttons[1].double_clicked.emit()

    assert dialog._palette is PALETTES[0], "改的不是正在用的那一套，预览不该跳走"
    assert dialog.recipe().preset == PALETTES[0].name
    assert dialog._palettes[1].colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    dialog.deleteLater()


def test_an_unchanged_colour_list_leaves_the_palette_alone(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原样确认不该把预设转成「自定义」，也不该让它的样子变一点。"""
    dialog = _dialog()
    same = tuple(color for _, color in PALETTES[0].colors)
    _fake_palette_dialog(monkeypatch, same)

    dialog._buttons[0].double_clicked.emit()

    assert dialog._palettes[0] is PALETTES[0]
    assert dialog._palettes[0].name == PALETTES[0].name
    dialog.deleteLater()


def test_deleting_a_palette_drops_that_chip(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编辑对话框里按「删除这套配色」：那一格消失，别的格留在原位。"""
    dialog = _dialog()
    _fake_palette_dialog(monkeypatch, DELETE)

    dialog._buttons[2].double_clicked.emit()

    assert len(dialog._palettes) == len(PALETTES) - 1
    assert PALETTES[2] not in dialog._palettes
    assert len(_chips(dialog)) == len(PALETTES) - 1
    assert dialog._palette is PALETTES[0], "删的不是正在用的那一套，预览不该跳走"
    dialog.deleteLater()


def test_deleting_the_selected_palette_lands_on_the_one_after_it(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(monkeypatch, DELETE)

    dialog._buttons[0].double_clicked.emit()

    assert dialog._palette is PALETTES[1]
    assert dialog.recipe().colors == PALETTES[1].colors
    assert dialog._buttons[0].isChecked() is True
    assert dialog._buttons[0].toolTip().startswith(PALETTES[1].title)
    dialog.deleteLater()


def test_the_chips_after_a_deleted_one_still_edit_their_own_palette(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删掉一格之后它后面那些格的序号全都变了，双击必须还落在自己那一套上。"""
    dialog = _dialog()
    seen = _fake_palette_dialog(monkeypatch, DELETE, ("#336699", "#b06a3b"))

    dialog._buttons[0].double_clicked.emit()
    dialog._buttons[0].double_clicked.emit()

    assert seen[1][0] == f"编辑颜色 · {PALETTES[1].title}"
    assert dialog._palettes[0].colors == palette_from_colors(["#336699", "#b06a3b"]).colors
    dialog.deleteLater()


def test_deleting_frees_a_slot_for_a_new_palette(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog()
    _fake_palette_dialog(
        monkeypatch,
        *(["#336699", "#b06a3b"] for _ in range(MAX_ADDED_PALETTES)),
        DELETE,
    )
    for _ in range(MAX_ADDED_PALETTES):
        dialog._add_chip.click()
    assert dialog._add_chip.isEnabled() is False

    dialog._buttons[0].double_clicked.emit()

    assert dialog._add_chip.isEnabled() is True
    assert dialog._add_chip.toolTip() == "新增一套配色"
    dialog.deleteLater()


def test_the_last_palette_has_no_delete_exit(
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """至少留一套：只剩一格时编辑对话框不再给出「删除这套配色」。"""
    dialog = _dialog()
    seen = _fake_palette_dialog(
        monkeypatch,
        *([DELETE] * (len(PALETTES) - 1)),
        ("#336699", "#b06a3b"),
    )

    while len(dialog._palettes) > 1:
        dialog._buttons[0].double_clicked.emit()
    dialog._buttons[0].double_clicked.emit()

    assert len(dialog._palettes) == 1
    assert seen[-1][2] is False
    dialog.deleteLater()
