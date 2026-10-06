"""鼠标侧键：后侧键 = 「上一级」那一条命令（ADR 0043）。

全世界的软件都拿靠前的那个侧键当「返回」，而「返回」在 Gallery 里已经有一条命令了（``Esc``
= 弹栈顶一帧，ADR 0041 决策 1）。这一层要解决的是**怎么接住这一次按下**。

**接在窗口的 ``mousePressEvent`` 上接不住**：侧键在 Qt 里是一次普通的鼠标按下，落在光标底下那个
控件上，而网格是 ``QListView`` —— 它把这一次按下收下并 accept，压根不让它冒到窗口。实测（摘掉
这一层、只在窗口上挂一个探针）：网格的视口哪儿也没到；大图页的视口（``QGraphicsView``）与标题栏
那种普通子控件才会冒到窗口。最常用的那一页恰好是唯一接不住的一页。

所以挂在 ``QApplication`` 上：那一层看得见每一个事件。**代价是每个事件都多走一趟 Python**
（实测每个事件 +0.4 µs），换来的是只有这一处记得这件事 —— 每一页各接一次会漏，而漏掉的症状是
「那一页的后侧键没反应」，界面上看不出任何理由。

**这一层只认「这一下落在哪个顶层窗口里」**：编辑元数据那个模态对话框与弹出菜单都是别的顶层，
它们开着的时候后侧键不归这一扇窗口管。判据不看焦点 —— Qt 是**先把焦点给鼠标底下的控件、再把
事件交给过滤器**的（实测顺序：``focusOut`` → 改名提交 → 过滤器），所以到这一层时焦点早就变了，
那一格由 :meth:`MainWindow._on_back_button` 自己判。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


class SideButtonFilter(QObject):
    """把落在本窗口里的后侧键按一次变成 :attr:`back_pressed`，并吃掉这一下。

    :param window: 归谁。**过滤器以它为父对象**：挂在 ``QApplication`` 上的过滤器得活到事件循环
        结束，而 Python 侧只要有一个引用（父对象就是那个引用）就不会被提前回收。
    """

    #: 用户按下了后侧键，且这一下落在本窗口内。接上去的是**「上一级」那一个动作**（窗口连的），
    #: 于是启用状态与真正干活的地方都只有一份。
    back_pressed = Signal()

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        """认出后侧键的那一次按下。**吃掉**它 —— 别让它再变成一次点空白处的点击。

        前侧键不认（ADR 0043 决策 5）：层级栈只有 push / pop，没有「前进」那一份可去的地方。
        """
        if event.type() != QEvent.Type.MouseButtonPress or not isinstance(event, QMouseEvent):
            return False
        if event.button() != Qt.MouseButton.BackButton:
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.parent():
            return False
        self.back_pressed.emit()
        return True


__all__ = ["SideButtonFilter"]
