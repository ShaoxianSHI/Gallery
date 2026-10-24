"""「外部应用顺序」对话框：三页各一张可拖动的列表（ADR 0081）。

这里锁定三件事：列表用的是**自己算插入位置**的拖动（Qt 的 internal move 落到某一行身上会
把那行覆盖掉），**任何一次拖动都不会让某个应用消失**，以及交出去的三份次序就是行序。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPoint, QPointF  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

from gallery.ui.dialogs.external_order import (  # noqa: E402
    DRAG_MIME,
    ExternalOrderDialog,
    OrderItem,
    OrderPage,
)

PREVIEW = OrderItem(id="preview", name="预览", icon=None)
IMAGEGLASS = OrderItem(id="imageglass", name="ImageGlass", icon=None)
CALENDAR = OrderItem(id="apple_calendar", name="日历", icon=None)
GOOGLE = OrderItem(id="google_calendar", name="Google 日历", icon=None)


def _pages() -> tuple[OrderPage, ...]:
    return (
        OrderPage(
            page="detail",
            label="图片",
            items=(PREVIEW, IMAGEGLASS),
            builtin=("preview", "imageglass"),
        ),
        OrderPage(
            page="calendar",
            label="日历",
            items=(CALENDAR, GOOGLE),
            builtin=("apple_calendar", "google_calendar"),
        ),
    )


@pytest.fixture
def dialog(qt_app: QApplication) -> ExternalOrderDialog:
    return ExternalOrderDialog(None, _pages())


def _texts(dialog: ExternalOrderDialog, page: str) -> list[str]:
    listing = dialog._lists[page]
    return [listing.item(row).text() for row in range(listing.count())]


def test_each_page_gets_its_own_tab(dialog: ExternalOrderDialog) -> None:
    tabs = dialog._tabs

    assert [tabs.tabText(row) for row in range(tabs.count())] == ["图片", "日历"]
    assert _texts(dialog, "detail") == ["预览", "ImageGlass"]
    assert _texts(dialog, "calendar") == ["日历", "Google 日历"]


def test_the_list_drives_its_own_drag(dialog: ExternalOrderDialog) -> None:
    """**不走 Qt 的 internal move**：那条路落到某一行身上会覆盖它（见模块的 DRAG_MIME）。"""
    listing = dialog._lists["detail"]

    assert listing.dragDropMode() == QAbstractItemView.DragDropMode.DropOnly
    assert listing.acceptDrops()
    assert listing.viewport().acceptDrops()


def test_dropping_on_an_item_means_before_or_after_its_middle(
    dialog: ExternalOrderDialog,
) -> None:
    listing = dialog._lists["calendar"]
    rect = listing.visualItemRect(listing.item(1))
    x = rect.center().x()

    assert listing._insertion_row(QPoint(x, rect.top() + 1)) == 1
    assert listing._insertion_row(QPoint(x, rect.bottom() - 1)) == 2


def test_dropping_below_the_last_row_appends(dialog: ExternalOrderDialog) -> None:
    listing = dialog._lists["calendar"]

    assert listing._insertion_row(QPoint(10, listing.viewport().height() - 2)) == 2


def test_any_move_keeps_every_app_exactly_once(qt_app: QApplication) -> None:
    """**最容易再犯的那一条**：覆盖语义一回来，被压住的那一项就从列表里消失了。"""
    items = (
        OrderItem(id="a", name="A", icon=None),
        OrderItem(id="b", name="B", icon=None),
        OrderItem(id="c", name="C", icon=None),
    )
    page = OrderPage(page="x", label="图片", items=items, builtin=("a", "b", "c"))

    for source in range(3):
        for target in range(4):
            dialog = ExternalOrderDialog(None, (page,))
            dialog._lists["x"].move_row(source, target)
            ids = dialog.orders()["x"]
            assert sorted(ids) == ["a", "b", "c"], (source, target, ids)
            dialog.close()


def test_moving_a_row_down_lands_after_the_one_it_was_dropped_on(qt_app: QApplication) -> None:
    items = (
        OrderItem(id="a", name="A", icon=None),
        OrderItem(id="b", name="B", icon=None),
        OrderItem(id="c", name="C", icon=None),
    )
    page = OrderPage(page="x", label="图片", items=items, builtin=("a", "b", "c"))
    dialog = ExternalOrderDialog(None, (page,))

    dialog._lists["x"].move_row(0, 2)

    assert dialog.orders()["x"] == ("b", "a", "c")


class _FakeDrop:
    """只带 ``dropEvent`` 真正用到的那几样 —— 不必让 Qt 起一次真的拖。"""

    def __init__(self, position: QPointF, source: object, mime: QMimeData) -> None:
        self._position = position
        self._source = source
        self._mime = mime
        self.accepted = False

    def position(self) -> QPointF:
        return self._position

    def source(self) -> object:
        return self._source

    def mimeData(self) -> QMimeData:  # noqa: N802 - Qt 的名字
        return self._mime

    def setDropAction(self, action: object) -> None:  # noqa: N802 - Qt 的名字
        del action

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


def _drop_event(listing: object, position: QPoint) -> _FakeDrop:
    mime = QMimeData()
    mime.setData(DRAG_MIME, b"1")
    return _FakeDrop(QPointF(position), listing, mime)


def test_dropping_onto_an_item_moves_it_instead_of_replacing_it(qt_app: QApplication) -> None:
    """用户报的那一下：把第一项拖到第二项身上，**第二项不能消失**。"""
    items = (
        OrderItem(id="a", name="A", icon=None),
        OrderItem(id="b", name="B", icon=None),
        OrderItem(id="c", name="C", icon=None),
    )
    page = OrderPage(page="x", label="图片", items=items, builtin=("a", "b", "c"))
    dialog = ExternalOrderDialog(None, (page,))
    listing = dialog._lists["x"]
    listing._drag_row = 0
    rect = listing.visualItemRect(listing.item(1))

    listing.dropEvent(_drop_event(listing, QPoint(rect.center().x(), rect.bottom() - 1)))  # type: ignore[arg-type]

    assert dialog.orders()["x"] == ("b", "a", "c")
    assert sorted(dialog.orders()["x"]) == ["a", "b", "c"]


def test_the_orders_are_the_row_order(dialog: ExternalOrderDialog) -> None:
    dialog._lists["calendar"].move_row(0, 2)

    assert dialog.orders() == {
        "detail": ("preview", "imageglass"),
        "calendar": ("google_calendar", "apple_calendar"),
    }


def test_resetting_one_page_leaves_the_other_alone(dialog: ExternalOrderDialog) -> None:
    dialog._lists["calendar"].move_row(1, 0)

    dialog._reset_buttons["calendar"].click()

    assert _texts(dialog, "calendar") == ["日历", "Google 日历"]
    assert _texts(dialog, "detail") == ["预览", "ImageGlass"]


def test_reset_keeps_the_apps_that_are_not_in_the_builtin_order(
    qt_app: QApplication,
) -> None:
    """后来装的应用不在内置次序里 —— 恢复默认不该把它从列表里抹掉。"""
    page = OrderPage(
        page="detail",
        label="图片",
        items=(PREVIEW, IMAGEGLASS),
        builtin=("preview",),
    )
    dialog = ExternalOrderDialog(None, (page,))

    dialog._reset_buttons["detail"].click()

    assert _texts(dialog, "detail") == ["预览", "ImageGlass"]
    assert dialog.orders() == {"detail": ("preview", "imageglass")}
