"""``gallery.ui.layout``：拆控件只有一份实现，四个调用点共用。

这里守的是拆完之后的状态：控件**不在布局里、不再有父、也不会显示出来**。
四处（面包屑、元数据、欢迎页、日历工具条）每换一次内容都要拆一次控件，所以它们共用
:func:`clear_layout`，而不是各写一遍循环。

**关于「先藏、再摘」的顺序**：把一个可见控件 ``setParent(None)`` 会把它提升成
顶层窗口，Qt 有可能顺手把它显示出来 —— macOS 上会，面板原位弹出几个带标题栏的白色
小窗，里面是上一份内容。
这一点在 offscreen 测试平台上观察不到（两种顺序拆完都是不可见的），所以下面
断言的是**结果**；顺序的由来写在模块文档里。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

from gallery.ui.layout import clear_layout, clear_widgets


def _host_with(count: int = 3) -> tuple[QWidget, QHBoxLayout, list[QLabel]]:
    host = QWidget()
    layout = QHBoxLayout(host)
    added = [QLabel(f"第{i}段") for i in range(count)]
    for widget in added:
        layout.addWidget(widget)
    return host, layout, added


def test_clearing_removes_every_widget(qt_app: QApplication) -> None:
    host, layout, added = _host_with()

    clear_layout(layout)

    assert layout.count() == 0
    for widget in added:
        assert widget.parent() is None, "摘出控件树之后它不该还挂在这个面板下"
    host.deleteLater()


def test_a_cleared_widget_is_not_left_showing(qt_app: QApplication) -> None:
    """拆掉的控件绝不能继续显示 —— 那个「顶层小窗」的坑就落在这里。"""
    host, layout, added = _host_with(1)
    host.show()
    qt_app.processEvents()
    assert added[0].isVisible(), "前置条件没摆对：拆之前它应当是可见的"

    clear_layout(layout)
    qt_app.processEvents()

    assert not added[0].isVisible()
    # 注意别拿 ``isWindow()`` 当判据：``parent() is None`` 的控件按 Qt 的定义
    # **就是**窗口（那正是那个坑的机制）。看得见的症状只有「它被显示出来了」。
    host.deleteLater()


def test_clear_widgets_matches_clear_layout(qt_app: QApplication) -> None:
    """不经过布局的那条路，拆完的状态也得一样 —— 否则迟早有人改错一边。"""
    host, layout, added = _host_with(1)
    host.show()
    qt_app.processEvents()
    clear_layout(layout)

    loose = QWidget()
    detached = QLabel("上一份内容")
    loose_layout = QHBoxLayout(loose)
    loose_layout.addWidget(detached)
    loose.show()
    qt_app.processEvents()

    clear_widgets([detached])
    qt_app.processEvents()

    assert not detached.isVisible()
    assert not added[0].isVisible()
    loose.deleteLater()
    host.deleteLater()


def test_clearing_an_empty_layout_is_a_no_op(qt_app: QApplication) -> None:
    """空布局、以及 ``takeAt`` 拿到 ``None`` 的条目（比如拉伸），都不该抛。"""
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.addStretch(1)  # 不是控件的条目

    clear_layout(layout)
    clear_layout(layout)

    assert layout.count() == 0
    host.deleteLater()
