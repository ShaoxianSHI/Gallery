"""窗口标题栏那一行：平时显示名字，``⌘R`` 时原地换成输入框。

内容铺到标题栏底下之后，窗口顶边那一条已经是我们的客户区，文字也一并自己画（网格页是相册名、大图
页是照片文件名）。显示与编辑共用同一个 ``RenameEdit``，因此看不出换了控件（ADR 0025）；**这一条
只存在于 macOS**（高度来自安全区）。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from gallery.ui import theme
from gallery.ui.rename_edit import RenameEdit
from gallery.ui.theme import rgba

#: 标题栏那一条的高度（点）。macOS 的标题栏是 28 点，``WA_ContentsMarginsRespectsSafeArea``
#: 正好把内容区顶出这么多 —— 两边对得上，下面那一片才是真「摆在那一条上」。
HEIGHT = 28

#: 在安全区那一段之上**再加**这么多点（ADR 0026 决策 8）。
#:
#: 加高这一条要**同时把内容区也往下推同样的点数**（``MainWindow`` 给中央控件加同样的上边距），
#: 否则多出来的那一段就是盖在照片上的。
EXTRA_PADDING = 12

#: 输入框宽度：比文字宽一点（两侧各留 :data:`EDITOR_PADDING`），但不窄到放不下几个字、
#: 不宽到横穿整个窗口。
MIN_EDITOR_WIDTH = 260
MAX_EDITOR_WIDTH = 520
EDITOR_PADDING = 24

#: 输入框上下各留一点，让它不像贴在标题栏的边沿上。
EDITOR_MARGIN_V = 3

#: 左上角那三个按钮（红黄绿）占着的一段。它们画在最上层，标题与输入框都不该压上去。
TRAFFIC_LIGHTS_WIDTH = 78

#: 再窄也留这么宽 —— 窄到这一步的窗口本来就没法看，输入框挤一点也比看不见强。
FALLBACK_WIDTH = 120


def editor_rect(
    area: QRect,
    text: str,
    metrics: QFontMetrics,
    *,
    left: int = TRAFFIC_LIGHTS_WIDTH,
    right: int = 0,
) -> QRect:
    """输入框摆在 ``area``（标题栏那一条）的哪儿。

    **以文字为中心**：系统标题本来就是居中的，输入框若不居中，``⌘R`` 之后那一下「跳」看得见。
    宽度跟着文字走但有上下限；标题栏窄到摆不下时先让开左边那一份（那三个按钮），再让开右边
    那一份（搜索胶囊，ADR 0026 决策 8）。纯函数，因此能被单独测。
    """
    wanted = metrics.horizontalAdvance(text) + 2 * EDITOR_PADDING
    # 先让开那三个按钮：宽度不超过「标题栏减去左右两段」时，居中摆放自然就压不到它们。
    room = max(area.width() - left - right, FALLBACK_WIDTH)
    width = min(max(wanted, MIN_EDITOR_WIDTH), MAX_EDITOR_WIDTH, room)
    height = max(area.height() - 2 * EDITOR_MARGIN_V, 1)
    x = max(area.x() + (area.width() - width) // 2, area.x() + left)
    if x + width > area.right() + 1 - right:  # 窄窗口下别把右边顶出去
        x = max(area.right() + 1 - right - width, area.x())
    return QRect(x, area.y() + (area.height() - height) // 2, width, height)


class TitleBar(QWidget):
    """窗口顶边那一条：显示当前这一层的名字，``⌘R`` 时原地改成输入框。

    **它一个文件都不碰**（红线 1，ADR 0025 决策 3）：只回答「提交了一个新名字」与「用户放弃
    了」，其余由 ``MainWindow`` 与 ``write_ops`` 决定。
    """

    #: 用户确认了新名字。
    rename_committed = Signal(str)
    #: 用户放弃（Esc）。
    rename_cancelled = Signal()
    #: 改名的输入框开始了（``True``）或收起来了（``False``）。网格那边有同一个信号 ——
    #: 改到一半 Esc 归输入框、会动文件的命令一起让开（ADR 0025 决策 6）。
    edit_active_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        #: 右边那一段被别的东西占着多宽（右上角的搜索胶囊，ADR 0026 决策 8）。
        #: 标题文字与 ``⌘R`` 的输入框都要给它让出来 —— 否则一个长名字会从胶囊底下穿过去。
        self._right_reserved = 0
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 与网格那一个**同一个类**（ADR 0025 决策 5）：语义与外观只有一份。
        self._editor = RenameEdit(self)
        self._editor.committed.connect(self._on_committed)
        self._editor.cancelled.connect(self._on_cancelled)
        self._editor.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(self._label)
        layout.addStretch(1)

        # 不画底色：整块窗口是一整块底色（ADR 0005），这一条露出的就是窗口自己的底。
        self._set_mouse_transparent(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新下发一次颜色。

        **只管那行字** —— 输入框的样子由 :class:`~gallery.ui.rename_edit.RenameEdit` 自己管，
        于是两处的圆角、高亮永远一致（ADR 0025 决策 5）。
        """
        app_theme = theme.current_theme()
        self._label.setStyleSheet(
            f"color: {rgba(app_theme.text)}; font-size: 13px; background: transparent;"
        )

    def _set_mouse_transparent(self, transparent: bool) -> None:
        """不在编辑时对鼠标透明。

        那一段仍然是**拖窗口**的地方：按下要落到窗口自己身上才会发起 ``startSystemMove``。这个
        标志会连**子控件**一起罩住，所以编辑时必须摘掉，否则输入框收不到点击。
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, transparent)

    # ---- 内容 --------------------------------------------------------------

    @property
    def current_text(self) -> str:
        """这一行上正显示的名字。"""
        return self._text

    def set_text(self, text: str) -> None:
        """显示 ``text``。正在编辑时不去动输入框 —— 那里面是用户敲到一半的字。"""
        self._text = text
        self._update_label()

    def begin_edit(self) -> None:
        """把这一行换成输入框，开始改现在这个名字。

        没有名字可改时不进编辑（大图页那一格是相册或分组卡片时，窗口不给它起名字）。
        """
        if not self._text:
            return
        self._label.hide()
        self._set_mouse_transparent(False)
        self._editor.start(self._place_editor(), self._text, keep_suffix=True)
        self._editor.raise_()
        self.edit_active_changed.emit(True)

    def stop_edit(self) -> None:
        """外部强制收起（切页、换库、关窗），不发任何信号。"""
        was_editing = self._editor.is_editing()
        self._editor.stop()
        if was_editing:
            self._set_mouse_transparent(True)
            self._label.show()
            self._update_label()  # 收起之后回到「正在显示的名字」，不保留敲了一半的字
            self.edit_active_changed.emit(False)

    def is_editing(self) -> bool:
        return self._editor.is_editing()

    def reserve_right(self, width: int) -> None:
        """右边那一段被别的东西占着多宽（搜索胶囊）。

        标题文字与 ``⌘R`` 的输入框都要给它让出来，否则一个长相册名会从胶囊底下穿过去。
        """
        width = max(0, int(width))
        if width == self._right_reserved:
            return
        self._right_reserved = width
        self._update_label()
        if self._editor.is_editing():
            self._editor.setGeometry(self._place_editor())

    # ---- 内部 --------------------------------------------------------------

    def _place_editor(self) -> QRect:
        """输入框该在哪儿（见 :func:`editor_rect`）。"""
        return editor_rect(
            self.rect(),
            self._text,
            QFontMetrics(self._editor.font()),
            left=TRAFFIC_LIGHTS_WIDTH,
            right=self._right_reserved,
        )

    def _update_label(self) -> None:
        """刷新那行字：太长时中间省略（``ElideMiddle``，文件名最有信息量的是后半段），全文挂在
        提示气泡上。"""
        available = max(self.width() - TRAFFIC_LIGHTS_WIDTH - self._right_reserved, FALLBACK_WIDTH)
        metrics = QFontMetrics(self._label.font())
        self._label.setText(metrics.elidedText(self._text, Qt.TextElideMode.ElideMiddle, available))
        self.setToolTip(self._text)

    def _on_committed(self, text: str) -> None:
        """输入框提交：交出去的是名字本身，改不改由外面说了算（红线 1）。"""
        self._settle()
        self.rename_committed.emit(text)

    def _on_cancelled(self) -> None:
        self._settle()
        self._update_label()  # 敲了一半的字丢掉，显示的还是原来的名字
        self.rename_cancelled.emit()

    def _settle(self) -> None:
        """编辑结束（提交或取消）之后回到「只显示名字」的那副样子。"""
        self._set_mouse_transparent(True)
        self._label.show()
        self.edit_active_changed.emit(False)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._update_label()
        if self._editor.is_editing():
            self._editor.setGeometry(self._place_editor())  # 窗口改大小时输入框跟着重新居中


__all__ = [
    "EDITOR_PADDING",
    "EXTRA_PADDING",
    "HEIGHT",
    "MAX_EDITOR_WIDTH",
    "MIN_EDITOR_WIDTH",
    "TRAFFIC_LIGHTS_WIDTH",
    "TitleBar",
    "editor_rect",
]
