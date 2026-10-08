"""搜索框那一次「重新下发样式表」。

**再下发一次样式表不是「重新拼一遍字符串」那么轻**：Qt 会把整棵子树重新 polish
一遍（``repolish``），于是每次焦点进出都要把输入框、放大镜、候选列表那一整棵树走一遍。
而这一次下发的输入只有两个：主题与圆角半径（按胶囊高度算）。都没变时应当**什么都不做**。

**第三样「有没有焦点」仍然要留在判据里**，只是它管的不再是样式表：那一圈轮廓已经不在
样式表里了（改由 :func:`~gallery.ui.search_field.draw_outline` 自己填），焦点那一下看得见
的变化是**换了轮廓的颜色 + 标记为需要重绘**。少了它缓存就会把这一次也忽略掉，点进搜索框边框不变色
—— 而这一条既不崩也不报错。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui import theme  # noqa: E402
from gallery.ui.search_field import SearchField, outline_color  # noqa: E402


class _CountingSearchField(SearchField):
    """记下每一次 ``setStyleSheet`` 与 ``update`` —— 前者数**下发**，后者数**重画**。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        self.applied: list[str] = []  # 构造里就会调一次，先建好
        self.repaints = 0
        super().__init__(parent)

    def setStyleSheet(self, style: str) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.applied.append(style)
        super().setStyleSheet(style)

    def update(self) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.repaints += 1
        super().update()


@pytest.fixture
def field(qt_app: QApplication) -> _CountingSearchField:  # noqa: ARG001 - 造它需要那个 App
    host = QWidget()
    host.resize(320, 120)
    return _CountingSearchField(host)


def test_the_first_theme_always_goes_out(field: _CountingSearchField) -> None:
    """起手那一次必须真的下发（缓存从「没有」开始）。"""
    assert field.applied
    assert field._applied_theme is not None
    assert field._applied_radius == field.height() // 2
    assert field._applied_focused is False


def test_nothing_changed_means_nothing_is_republished(field: _CountingSearchField) -> None:
    """主题、高度、焦点三样都没变 → **一次都不再下发**。

    ``focus_gained`` / ``edit_ended`` / ``resizeEvent`` 都走 ``_apply_theme``，而重复的那一次
    照样会把整棵树 polish 一遍 —— 每点一次搜索框就付一次。
    """
    before = len(field.applied)

    field._apply_theme()
    field._apply_theme()

    assert len(field.applied) == before


def test_focus_really_does_repaint_the_outline(field: _CountingSearchField) -> None:
    """焦点那一下**要**看得见：轮廓换高亮色，而且要真的重画一遍。

    少了 ``focused`` 这个判据，缓存就会把这一次也忽略掉，于是点进搜索框边框不变色 ——
    而这一条既不崩也不报错。

    **盯的是颜色与重画，不是样式表字符串**：那一圈轮廓已经不在样式表里了（自己填的，
    理由见 :func:`~gallery.ui.search_field.draw_outline`），字符串在焦点那一下根本不变。
    """
    before = len(field.applied)
    resting = outline_color(field, accent=False)

    field.focus_gained()

    assert field._applied_focused is True
    assert len(field.applied) == before + 1
    assert field.repaints >= 1, "没人标脏，那一圈还是上一次的颜色"
    assert outline_color(field, accent=True) != resting, "轮廓的颜色没跟着焦点换"


def test_a_new_height_republishes_it(field: _CountingSearchField) -> None:
    """高度变了**必须**重下 —— 圆角半径是 ``height() // 2``（见 :meth:`resizeEvent`）。"""
    field.resize(field.width(), field.height() + 18)
    field._apply_theme()

    assert field._applied_radius == field.height() // 2


def test_a_new_theme_republishes_it(
    field: _CountingSearchField, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换浅色 / 深色照样要重下（``ThemeWatcher`` 走的是同一条路）。"""
    before = len(field.applied)
    now = theme.current_theme()
    other = theme.AppTheme(
        background=now.background,
        text=now.text,
        link=now.link,
        notice=now.notice,
        error=QColor(1, 2, 3),  # 与当前那份**不等**：只有 error 换了
    )
    monkeypatch.setattr(theme, "current_theme", lambda *args, **kwargs: other)

    field._apply_theme()

    assert len(field.applied) == before + 1
