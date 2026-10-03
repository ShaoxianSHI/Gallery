"""大图页那一行上的编辑图标条（ADR 0027）。

三件事要钉住：**四枚图标摆得对**、**间距是那几个数**、**图标本身画得出东西**
—— 最后一条挡的是一个具体踩过的坑：``drawArc`` 在这条绘制路径下整段不画，
而少了那一段弧，图标看着就像坏了。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from gallery.ui import theme
from gallery.ui.viewer import glyphs
from gallery.ui.viewer.toolbar import ACTIONS, BUTTON_EDGE, GAP, EditToolbar


@pytest.fixture
def toolbar(qt_app: QApplication) -> EditToolbar:
    """一根**顶层**图标条。

    挂在临时父控件上是不行的：fixture 一返回那个父控件就只剩 C++ 侧的引用，
    回收时连工具条一起析构，于是每个用例都报 ``Internal C++ object already deleted``。
    顶层控件由 ``conftest`` 的 ``qt_teardown`` 确定性收尾（见那里对销毁顺序的说明）。
    """
    return EditToolbar()


def _ink(pixmap) -> int:  # noqa: ANN001 - QImage 的类型在测试里不必写全
    """画布上有多少个不透明像素（为 0 就说明这一枚压根没画出来）。"""
    image = pixmap.toImage()
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 10
    )


def test_there_are_four_buttons_in_a_known_order(toolbar: EditToolbar) -> None:
    assert [name for name, _ in ACTIONS] == ["rotate_left", "rotate_right", "mirror", "crop"]
    assert len(toolbar.buttons) == 4
    tips = [button.toolTip() for button in toolbar.buttons]
    assert tips == [tip for _, tip in ACTIONS]
    assert all(tip for tip in tips), "四枚都要有提示：图标本身就说不清自己干什么"


def test_the_buttons_are_clickable_and_bigger_than_their_icons(toolbar: EditToolbar) -> None:
    """命中区比图形大一圈 —— 这是图标看着「疏」而不是「挤」的那一档。"""
    for button in toolbar.buttons:
        assert button.width() == BUTTON_EDGE
        assert button.iconSize().width() == glyphs.ICON_EDGE
        assert BUTTON_EDGE > glyphs.ICON_EDGE


def test_content_width_counts_the_gaps(toolbar: EditToolbar) -> None:
    """摆位靠这个数算宽度，间距漏算一次图标就会压到搜索胶囊上。"""
    assert toolbar.content_width() == 4 * BUTTON_EDGE + 3 * GAP


def test_pressing_a_button_reports_its_name(toolbar: EditToolbar) -> None:
    seen: list[str] = []
    toolbar.action_requested.connect(seen.append)
    toolbar.buttons[2].click()
    assert seen == ["mirror"]


def test_the_whole_group_can_be_turned_off_at_once(toolbar: EditToolbar) -> None:
    """四枚要么都有对象要么都没有（大图页看的永远是照片），所以是整组开关。"""
    toolbar.set_enabled_actions(False)
    assert all(not button.isEnabled() for button in toolbar.buttons)
    toolbar.set_enabled_actions(True)
    assert all(button.isEnabled() for button in toolbar.buttons)


def test_it_starts_hidden(toolbar: EditToolbar) -> None:
    """窗口刚建好时不该有一排图标浮在欢迎页上（只在 ``_layout_edit_toolbar`` 之后才摆）。"""
    assert not toolbar.isVisible()


@pytest.mark.parametrize("name", [name for name, _ in ACTIONS])
def test_every_glyph_draws_something(qt_app: QApplication, name: str) -> None:
    """**每一枚都要真的画出东西。**

    这条挡的是一个具体踩过的坑：``QPainter.drawArc`` 在这条绘制路径下不画任何东西，
    而旋转图标少一段弧看上去就是一个坏掉的按钮（另一个 ``drawLine`` 却照画，
    所以只靠「肉眼看」很容易混过去）。
    """
    for ratio in (1.0, 2.0):
        pixmap = glyphs.glyph(name, theme.current_theme().text, ratio)
        assert pixmap.width() == round(glyphs.ICON_EDGE * ratio)
        assert _ink(pixmap) > 20, f"{name} 在 {ratio}x 下几乎是空的"


def test_the_glyph_looks_the_same_at_any_ratio(toolbar: EditToolbar) -> None:
    """同一枚图标在 1x 与 3x 下笔迹范围按比例走 —— 缩放逻辑写错的话这里会露出来。"""
    for name, _ in ACTIONS:
        small = glyphs.glyph(name, theme.current_theme().text, 1.0)
        large = glyphs.glyph(name, theme.current_theme().text, 3.0)
        assert large.width() == 3 * small.width()


def test_an_unknown_glyph_is_an_empty_pixmap_not_a_crash(qt_app: QApplication) -> None:
    """界面不该为一枚不存在的图标崩掉。"""
    assert glyphs.glyph("没有这一枚", theme.current_theme().text).isNull()


def test_the_two_rotate_glyphs_are_not_the_same(qt_app: QApplication) -> None:
    """左转与右转画的是两个不同的箭头 —— 共用一段代码不等于画出同一张图。"""
    color = theme.current_theme().text
    left = glyphs.glyph("rotate_left", color).toImage()
    right = glyphs.glyph("rotate_right", color).toImage()
    assert left != right
