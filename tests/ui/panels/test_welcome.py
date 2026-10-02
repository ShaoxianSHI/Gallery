"""欢迎页文字在深浅两套主题下都要看得清。

这里补的是一个真实缺陷：欢迎页那两行次要文字原先写的是 ``palette(mid)``。
``Mid`` 是 Qt 的**填充色**角色（给未选中的行、表格隔行铺底用的），拿它当字色，
深色下在 #171717 的底上只有 3.6:1 的对比度，达不到 WCAG AA 对正文的 4.5:1。

所以这里不检查控件树，而是**两件事各钉一半**：

1. 样式表里真正写进去的颜色，必须是主题派生出来的那些 —— 挡住「改回 palette(mid)」；
2. 这些颜色压到该主题的底色上，对比度必须够 —— 挡住「把透明度调太低换层次感」。
   黑字压白底的对比度掉得比白字压黑底快得多，只验深色会漏掉一半的问题。

调色板是显式设上去的，因此浅色与深色都能在离屏模式下确定性跑到，
不依赖这台机器此刻是什么外观。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui import theme as theme_module  # noqa: E402
from gallery.ui.panels.welcome import (  # noqa: E402
    SECONDARY_ALPHA,
    SUBTITLE_ALPHA,
    WelcomeView,
    welcome_style,
)

#: WCAG AA 对正文的要求。
MIN_CONTRAST = 4.5

#: macOS 深浅两套外观下，Qt 给内容区与正文的真实取值（实测，见 ui.theme）。
LIGHT_BACKGROUND = "#ffffff"
LIGHT_TEXT = "#000000"
DARK_BACKGROUND = "#171717"
DARK_TEXT = "#ffffff"

THEMES = (
    pytest.param(LIGHT_BACKGROUND, LIGHT_TEXT, id="浅色"),
    pytest.param(DARK_BACKGROUND, DARK_TEXT, id="深色"),
)

#: 「与当前这套相反的那套」——用来模拟一次系统外观切换。
OPPOSITE = {
    LIGHT_BACKGROUND: (DARK_BACKGROUND, DARK_TEXT),
    DARK_BACKGROUND: (LIGHT_BACKGROUND, LIGHT_TEXT),
}


def luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def contrast_ratio(first: QColor, second: QColor) -> float:
    brighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (brighter + 5) / (darker + 5)


def flattened(color: QColor, background: QColor) -> QColor:
    """把带透明度的字色压到实底上，算出眼睛真正看到的那一色。"""
    alpha = color.alphaF()
    return QColor(
        round(alpha * color.red() + (1 - alpha) * background.red()),
        round(alpha * color.green() + (1 - alpha) * background.green()),
        round(alpha * color.blue() + (1 - alpha) * background.blue()),
    )


def build_theme(background: QColor, text: QColor) -> theme_module.AppTheme:
    return theme_module.AppTheme(
        background=background,
        text=text,
        link=QColor("#4c9aff"),
        notice=QColor("#ffcf70"),
        error=QColor("#ff9b8a"),
    )


def build_palette(background_hex: str, text_hex: str) -> QPalette:
    """造一份只有内容区与正文被指定的应用调色板，其余沿用当前值。"""
    palette = QPalette(QApplication.instance().palette())
    background = QColor(background_hex)
    text = QColor(text_hex)
    palette.setColor(theme_module.BACKGROUND_ROLE, background)
    for role in theme_module.BACKGROUND_ROLES:
        palette.setColor(role, background)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Mid, QColor("#5f5f5f"))  # 深色下的真实取值
    return palette


@pytest.mark.parametrize(("background_hex", "text_hex"), THEMES)
def test_secondary_text_keeps_aa_contrast(background_hex: str, text_hex: str) -> None:
    """两档次要文字压到该主题的底色上，都要守住 WCAG AA。"""
    background = QColor(background_hex)
    theme = build_theme(background, QColor(text_hex))
    style = welcome_style(theme)

    for alpha in (SUBTITLE_ALPHA, SECONDARY_ALPHA):
        color = theme.text_at(alpha)
        assert theme_module.rgba(color) in style, (
            f"alpha={alpha} 的字色 {theme_module.rgba(color)} 没被写进样式表"
            " —— 次要文字必须由主题派生，不能写死 palette(...)"
        )
        rendered = flattened(color, background)
        assert contrast_ratio(rendered, background) >= MIN_CONTRAST, (
            f"alpha={alpha} 在 {background_hex} 底上只有 "
            f"{contrast_ratio(rendered, background):.2f}:1"
            f"（{rendered.name()} 压 {background_hex}），低于 {MIN_CONTRAST}:1"
        )


@pytest.mark.parametrize(("background_hex", "text_hex"), THEMES)
def test_welcome_view_repaints_text_on_theme_change(
    qt_app: QApplication, background_hex: str, text_hex: str
) -> None:
    """系统换浅色 / 深色时字色要跟着重算，不能停在构造那一刻。

    样式表是在 ``__init__`` 里设的，之后不会自己重算 —— 所以必须连上
    ``ThemeWatcher.changed``，否则用户切外观后欢迎页的字色是上一次的那套。

    这里换的是**应用调色板本身**（而不是直接塞一份样式表）：``ThemeWatcher``
    收到信号后是从 ``current_theme()`` 重新取色的，只有真的换掉调色板，
    才是在测它承诺的那件事。
    """
    original = QPalette(qt_app.palette())
    try:
        qt_app.setPalette(build_palette(background_hex, text_hex))
        theme_module.apply_theme(qt_app)

        view = WelcomeView()
        qt_app.processEvents()
        watcher = theme_module.ensure_installed(qt_app)
        assert view._theme_watcher is watcher, "欢迎页没有接上主题监听"

        switched_background, switched_text = OPPOSITE[background_hex]
        qt_app.setPalette(build_palette(switched_background, switched_text))
        watcher.changed.emit()

        # 切换**之后**的样式表应当跟新调色板走。
        expected = build_theme(QColor(switched_background), QColor(switched_text))
        assert theme_module.rgba(expected.text_at(SECONDARY_ALPHA)) in view.styleSheet(), (
            f"切到 {switched_background} 之后，样式表没有按新调色板重算"
        )
        view.deleteLater()
    finally:
        qt_app.setPalette(original)
        theme_module.apply_theme(qt_app)


def test_welcome_view_builds_with_recent_libraries(qt_app: QApplication) -> None:
    """顺带守住「最近打开」列表的装配（此前完全没有测试覆盖）。"""
    view = WelcomeView()
    view.set_recent([Path("/tmp/相册甲"), Path("/tmp/相册乙")])
    view.show()
    qt_app.processEvents()

    buttons = view.findChildren(QWidget)
    assert any(b.objectName() == "recentButton" for b in buttons)
    view.deleteLater()
