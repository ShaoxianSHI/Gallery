"""搜索框那一次「重新下发样式表」。

**再下发一次样式表不是「重新拼一遍字符串」那么轻**：Qt 会把整棵子树重新 polish
一遍（``repolish``），于是每次焦点进出都要把输入框、放大镜、候选列表那一整棵树走一遍。
而这一份样式表的输入只有三个：主题、圆角半径（按胶囊高度算）、有没有焦点。
三个都没变时应当**什么都不做**。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui import theme  # noqa: E402
from gallery.ui.search_field import SearchField  # noqa: E402


class _CountingSearchField(SearchField):
    """记下每一次 ``setStyleSheet`` —— 计数的是**下发**，不是拼字符串。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        self.applied: list[str] = []  # 构造里就会调一次，先建好
        super().__init__(parent)

    def setStyleSheet(self, style: str) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.applied.append(style)
        super().setStyleSheet(style)


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


def test_focus_really_does_republish_once(field: _CountingSearchField) -> None:
    """焦点那一下**要**重下（边框换高亮色）—— 上面那条不是说「永远别下」。

    少了 ``focused`` 这个判据，缓存就会把这一次也吃掉，于是点进搜索框边框不变色 ——
    而这一条既不崩也不报错。
    """
    before = len(field.applied)

    field.focus_gained()

    assert field._applied_focused is True
    assert len(field.applied) == before + 1
    assert field.applied[-1] != field.applied[-2]


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
