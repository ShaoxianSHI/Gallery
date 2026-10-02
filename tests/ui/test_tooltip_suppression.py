"""切图时不得冒出网格卡片的悬停提示。

那一闪是个**顶层窗口**（macOS 上白底），里面是卡片的名字、格式、大小、时间与所在文件夹。
挡住它要两道防线，各治一类竞态：

1. **数据侧**：大图打开时网格不再提供提示内容（``set_tooltips_enabled(False)``）。
   治的是「悬停定时器醒来时问网格要内容，网格答得出来」。
2. **总闸**：``ToolTipGate`` 拦下所有指向**不在屏幕上**的控件的悬停提示事件。治的是
   「事件迟到」—— Qt 自己不检查目标控件还在不在屏幕上，事件送到了就弹窗（实测：网格页
   已经换成大图页，事件送到网格视口，提示照弹，里面就是上一张卡片的元数据）。

第 2 条不依赖「每条切图路径都记得关开关」，第 1 条依赖。只留第 1 条时，只要漏掉一次
（从大图页直接切浏览方式就是一个），照片上就会闪出那张卡片的元数据。

这里的每一处等待都掐在刀刃上：悬停只等到唤醒延迟的一部分（保证定时器还挂着），
切走之后只再盯住剩下的那一部分加一点余量 —— 原来是无脑等满 1.2 秒，而定时器根本
不会响。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QHelpEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolTip, QWidget  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import BROWSE_MODE_ALBUMS  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.tooltips import ToolTipGate, install  # noqa: E402

#: Qt 离屏环境下弹出悬停提示的唤醒延迟，实测 784ms（Qt 默认那一档）。这几条测试的
#: 全部意义就是「定时器还挂着的时候切走」，绕不开这个数。
TOOLTIP_DELAY_SECONDS = 0.8

#: 切走后继续盯多久：唤醒延迟里还没走完的那部分，再加一点调度余量。
WATCH_MARGIN_SECONDS = 0.15

#: 悬停只等到唤醒延迟的这个比例，剩下的留给切走后的观察段。
HOVER_FRACTION = 0.6

#: 悬停前先把鼠标移开、让 Qt 收到「离开」事件的时间。短了 Qt 不会重启提示定时器，
#: 那就等于没重新悬停过 —— 这条不能省。
MOVE_AWAY_SECONDS = 0.2

#: 等窗口里的照片全部扫出来的时限。
READY_TIMEOUT_SECONDS = 8.0


@pytest.fixture
def window(
    qt_app: QApplication,
    wait_for: Callable[..., bool],
    tmp_path: Path,
    make_photo: object,
) -> MainWindow:
    root = tmp_path / "库"
    for index in range(6):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)  # type: ignore[operator]
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1200, 820)
    window.show()
    assert wait_for(lambda: window._model.rowCount() >= 6, timeout=READY_TIMEOUT_SECONDS), (
        "照片没能在时限内扫出来，后面几条测的都不是这条路径"
    )
    yield window  # type: ignore[misc]
    window.close()


@pytest.fixture
def hover(
    window: MainWindow,
    settle: Callable[[float], None],
    wait_for: Callable[..., bool],
) -> Callable[[float], bool]:
    """「把鼠标放到第一张卡片上停一会儿」，返回提示是否出现。"""

    def _hover(seconds: float) -> bool:
        viewport = window._grid.viewport()
        rect = window._grid.visualRect(window._model.index(0, 0))
        # 先移开再移过去，否则 Qt 不会重启提示定时器 —— 那就测不到「重新悬停」了。
        QTest.mouseMove(viewport, QPoint(4, 4))
        settle(MOVE_AWAY_SECONDS)
        QTest.mouseMove(viewport, rect.center())
        settle(seconds)
        visible = QToolTip.isVisible()
        if visible:
            QToolTip.hideText()
            settle(0.1)
        return visible

    return _hover


@pytest.fixture
def tooltip_within(wait_for: Callable[..., bool]) -> Callable[[float], bool]:
    """在给定时限内盯住悬停提示，一出现就收掉并返回 ``True``。"""

    def _within(seconds: float) -> bool:
        if not wait_for(lambda: QToolTip.isVisible(), timeout=seconds):
            return False
        QToolTip.hideText()
        return True

    return _within


def test_pending_tooltip_does_not_pop_up_after_switching(
    window: MainWindow,
    settle: Callable[[float], None],
    hover: Callable[[float], bool],
    tooltip_within: Callable[[float], bool],
) -> None:
    """悬停还没到显示时间就切图，切换过程中不得弹出提示。

    诚实说明：离屏环境下这条**自己抓不住**那个缺陷 —— 切到大图后网格页已隐藏，Qt
    根本不会再弹提示，有没有抑制结果都一样（把 bug 放回去跑，带这条与不带这条都是
    绿的，已核对）。真正兜住回归的是
    :func:`test_model_stops_offering_tooltips_while_in_detail`。这里保留它是因为它覆盖
    完整的端到端场景，也提醒别把等待掐得连这个场景都不成立了。
    """
    hover_budget = TOOLTIP_DELAY_SECONDS * HOVER_FRACTION
    watch_budget = TOOLTIP_DELAY_SECONDS - hover_budget + WATCH_MARGIN_SECONDS

    flashed = 0
    for _ in range(3):
        window.show_grid()
        settle(0.1)
        assert not hover(hover_budget), "还没到唤醒时刻就不该有提示，否则这条测的不是竞态"
        window._open_detail(window._model.index(0, 0))
        if tooltip_within(watch_budget):
            flashed += 1

    assert flashed == 0, f"切图时又闪出了 {flashed} 次悬停提示"


def test_grid_tooltip_still_works_after_returning(
    window: MainWindow,
    settle: Callable[[float], None],
    hover: Callable[[float], bool],
) -> None:
    """回到网格后悬停提示必须照常可用 —— 不能为了不闪而把功能关掉。"""
    window._open_detail(window._model.index(0, 0))
    settle(0.1)
    window.show_grid()
    settle(0.1)

    # 这条要确认的恰恰是「它真的会出现」，所以只能老老实实等过唤醒时刻 ——
    # 和上一条「确认它不出现」正好相反，这里没法提前收工。余量给得比
    # 唤醒延迟宽裕得多：宁可多等，也不能因为差几十毫秒就静悄悄 skip 掉一条
    # 唯一的正向防线（实测 1.3s 就会时灵时不灵）。
    if not hover(TOOLTIP_DELAY_SECONDS + 1.5):
        pytest.skip("离屏环境没能弹出悬停提示")


def test_model_stops_offering_tooltips_while_in_detail(
    window: MainWindow, settle: Callable[[float], None]
) -> None:
    """大图打开时模型不返回提示内容（定时器醒来问不到东西）。"""
    index = window._model.index(0, 0)

    window.show_grid()
    settle(0.1)
    assert window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), "网格里应当有提示"

    window._open_detail(window._model.index(0, 0))
    settle(0.1)
    assert not window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), (
        "大图打开时不该再提供提示内容"
    )

    window.show_grid()
    settle(0.1)
    assert window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), "回到网格应当恢复"


# ---- 迟到的事件：总闸（见 ui.tooltips） ---------------------------------------
#
# 下面几条不去掐悬停延迟，而是**直接把 ToolTip 事件递给控件** —— 离屏环境下悬停定时器
# 在切图之后根本不会再响，掐不准也测不准；直接递事件测的正是那一层：**Qt 收到事件就弹窗**。
# 弹窗与否只看一件事：那个控件当时在不在屏幕上。


def ask(widget: QWidget, position: QPoint, qt_app: QApplication) -> bool:
    """模拟「悬停定时器醒来」：把 ToolTip 事件递给这个控件，返回有没有弹出窗口。"""
    QToolTip.hideText()
    qt_app.processEvents()
    event = QHelpEvent(QEvent.Type.ToolTip, position, widget.mapToGlobal(position))
    qt_app.sendEvent(widget, event)
    qt_app.processEvents()
    shown = QToolTip.isVisible()
    QToolTip.hideText()
    return shown


@pytest.fixture
def tooltip_gate(qt_app: QApplication) -> ToolTipGate:
    """先装总闸。这条直接测它，不该指望别的测试先建过窗口。"""
    return install()


def test_tooltip_never_opens_for_a_widget_that_is_not_on_screen(
    qt_app: QApplication, tooltip_gate: ToolTipGate
) -> None:
    """指向不可见控件的悬停提示事件，不许弹出窗口（总闸的基本保证）。"""
    widget = QLabel("面板里的一行")
    widget.setToolTip("IMG_0001.jpg\nJPEG · 2.0 MB")
    assert not widget.isVisible(), "这个控件必须处在「不在屏幕上」的状态，否则测的不是这条"

    assert not ask(widget, QPoint(1, 1), qt_app), (
        "不可见的控件也弹出了提示窗口 —— 这就是照片上凭空闪出小白窗的那一下"
    )


def test_detail_page_swallows_the_late_card_tooltip(
    window: MainWindow, qt_app: QApplication, settle: Callable[[float], None]
) -> None:
    """大图页上，迟到的卡片提示事件弹不出窗口（哪怕模型此刻答得出来）。"""
    window._open_detail(window._model.index(0, 0))
    settle(0.1)
    viewport = window._grid.viewport()
    # 故意让模型照常回答：这条要证明的是**总闸**在起作用，而不是「模型恰好返回了空串」。
    # 只靠模型那一条时，任何一次漏调 set_tooltips_enabled(False) 都会让提示闪出来。
    window._model.set_tooltips_enabled(True)
    position = window._grid.visualRect(window._model.index(0, 0)).center()

    assert not ask(viewport, position, qt_app), (
        "大图页上仍然弹出了网格卡片的提示 —— 用户看到的就是照片上那个装着元数据的小白窗"
    )


def test_grid_page_tooltips_are_not_blocked(window: MainWindow, qt_app: QApplication) -> None:
    """总闸只拦「不在屏幕上」的：网格页上的提示必须照常能弹出来。"""
    window.show_grid()
    qt_app.processEvents()
    viewport = window._grid.viewport()
    position = window._grid.visualRect(window._model.index(0, 0)).center()

    assert ask(viewport, position, qt_app), (
        "网格页上的提示被总闸拦住了 —— 那是把功能关掉，不是修闪烁"
    )


def test_switching_browse_mode_from_detail_restores_grid_tooltips(
    window: MainWindow, settle: Callable[[float], None]
) -> None:
    """在大图页直接切浏览方式（视图 → 全部照片），网格的提示要还是好的。

    这一条盯的是另一头：**总闸拦住的是「不在屏幕上」的控件，所以提示内容本身必须由
    代码老老实实开关**。从大图页切浏览方式会直接换回网格页（不经过 show_grid），
    漏开开关的话提示就永久哑了。
    """
    window._open_detail(window._model.index(0, 0))
    settle(0.1)
    assert not window._model.data(window._model.index(0, 0), int(Qt.ItemDataRole.ToolTipRole))

    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    settle(0.1)

    assert window._stack.currentWidget() is window._grid_page, "切浏览方式应当回到网格页"
    assert window._model.data(window._model.index(0, 0), int(Qt.ItemDataRole.ToolTipRole)), (
        "回到网格后提示内容还是关着的，悬停就再也弹不出来了"
    )
