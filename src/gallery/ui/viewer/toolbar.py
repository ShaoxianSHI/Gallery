"""大图页那一行上的编辑图标条：左转 · 右转 · 镜像 · 裁剪（ADR 0027）。

## 为什么它是窗口的子控件，而不是 ``TitleBar`` 的子控件

因为 ``TitleBar`` 整块挂着 ``WA_TransparentForMouseEvents`` —— 不这样标题栏那一段就拖不动
窗口，而那个标志会**连子控件一起罩住**（见 ``title_bar`` 里那一条注释），图标放进去就成了
点不动的画。因此这里与右上角那个搜索胶囊一样，是 ``MainWindow`` 自己建的子控件，
在 ``_layout_title_bar`` 里摆位、建在 ``TitleBar`` 之后（Qt 按创建顺序叠兄弟控件）。

## 摆在哪由外面决定，这里只管自己的形状

图标组摆在哪一行、离文件名多远、离搜索胶囊多远，都是**那一行的排版**，因此那几个
间距钉在 ``ui.title_bar`` 里（与 ``TRAFFIC_LIGHTS_WIDTH`` 同处）。这里只有「按钮多大、
按钮之间隔多远」这两个自身的尺寸。

**它一个文件都不碰。** 按下哪一枚由 :attr:`EditToolbar.action_requested` 报出去，写不写盘、
存到哪里，全在 ``core.metadata.export`` 与 ``ui.write_ops`` 那边（红线 1）。
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba
from gallery.ui.viewer.glyphs import ICON_EDGE, glyph

#: 每个按钮的边长（点）。macOS 上最小可点尺寸是 22 —— **比图标本身大一圈**：
#: 命中区与图形分开画，图标看着才是「疏」而不是「挤」。
BUTTON_EDGE: Final = 22

#: 按钮之间隔多远（点）。两枚 22 点的按钮隔 6 点，看上去就是工具栏而不是图标墙。
GAP: Final = 6

#: 摆出来的顺序，也是快捷键提示与菜单里的顺序（ADR 0027 决策 8）。
ACTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("rotate_left", "逆时针旋转 90°"),
    ("rotate_right", "顺时针旋转 90°"),
    ("mirror", "左右镜像"),
    ("crop", "裁剪"),
)

#: 圆角（悬停时那圈高亮）。
_RADIUS: Final = 5

#: 悬停高亮只用一层很淡的主色：这一行是照片上的浮层，抢了照片的戏就不对了。
_HOVER_ALPHA: Final = 0.10

#: 图标本身的颜色比正文淡一档：它是一组工具，不必与文件名一样实心。
_ICON_ALPHA: Final = 0.72


def toolbar_style(app_theme: AppTheme) -> str:
    """按钮的样子。**平时完全透明**，只有悬停才浮起一层淡面。"""
    hover = app_theme.text_at(_HOVER_ALPHA)
    return f"""
    QToolButton#editTool {{
        background: transparent;
        border: none;
        border-radius: {_RADIUS}px;
    }}
    QToolButton#editTool:hover {{ background: {rgba(hover)}; }}
    QToolButton#editTool:pressed {{ background: {rgba(app_theme.text_at(0.2))}; }}
    QToolButton#editTool:disabled {{ background: transparent; }}
    """


class EditToolbar(QWidget):
    """四枚图标。**只报「用户按了哪一枚」**，自己不解释那是什么意思。"""

    #: 用户按了哪一枚（值与 :data:`ACTIONS` 里的名字一致）。
    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("editToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._buttons: dict[str, QToolButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(GAP)
        for name, tip in ACTIONS:
            button = QToolButton(self)
            button.setObjectName("editTool")
            button.setToolTip(tip)
            button.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
            button.setFixedSize(BUTTON_EDGE, BUTTON_EDGE)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _checked=False, action=name: self.action_requested.emit(action)
            )
            layout.addWidget(button)
            self._buttons[name] = button
        layout.addStretch(1)

        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self.hide()

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新画一遍图标（颜色全部从主题派生，见 ``ui.theme``）。"""
        app_theme = theme.current_theme()
        self.setStyleSheet(toolbar_style(app_theme))
        for name, button in self._buttons.items():
            button.setIcon(glyph(name, app_theme.text_at(_ICON_ALPHA), self.devicePixelRatioF()))

    # ---- 对外 --------------------------------------------------------------

    @property
    def buttons(self) -> tuple[QToolButton, ...]:
        """按摆出来的顺序给出去（测试与「关掉全部」那类整段操作要用）。"""
        return tuple(self._buttons[name] for name, _ in ACTIONS)

    def set_enabled_actions(self, enabled: bool) -> None:
        """整组一起亮或一起灰。**不做逐枚判据** —— 四枚要么都有对象，要么都没有
        （大图页正看着的那一张永远是照片，见 ADR 0021）。"""
        for button in self.buttons:
            button.setEnabled(enabled)

    def content_width(self) -> int:
        """这组图标有多宽 —— 摆位时用它把文件名右边那一段算出来。

        **不是 ``sizeHint``**：那一个会把布局边距算进去，而这里要的是纯粹「图标占多宽」
        这个数，好让 :mod:`gallery.ui.title_bar` 按它留出间距。
        """
        return len(ACTIONS) * BUTTON_EDGE + (len(ACTIONS) - 1) * GAP


__all__ = ["ACTIONS", "BUTTON_EDGE", "GAP", "EditToolbar", "toolbar_style"]
