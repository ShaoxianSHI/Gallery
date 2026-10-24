"""「外部应用顺序」对话框：三页各一张可拖动的列表（ADR 0081）。

这里只钉两件事：**列表真的能拖**（不是一组只能点的菜单项），以及交出去的那三份次序就是
列表中从上到下的顺序。拖动手势本身归 Qt，不去模拟。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

from gallery.ui.dialogs.external_order import (  # noqa: E402
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


def test_a_list_only_drags_inside_itself(dialog: ExternalOrderDialog) -> None:
    """跨页拖过去没有意义：那会把两个位置的次序混成一条。"""
    listing = dialog._lists["detail"]

    assert listing.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert listing.defaultDropAction() == Qt.DropAction.MoveAction


def test_the_orders_are_the_row_order(dialog: ExternalOrderDialog) -> None:
    listing = dialog._lists["calendar"]
    moved = listing.takeItem(0)
    listing.insertItem(1, moved)

    assert dialog.orders() == {
        "detail": ("preview", "imageglass"),
        "calendar": ("google_calendar", "apple_calendar"),
    }


def test_resetting_one_page_leaves_the_other_alone(dialog: ExternalOrderDialog) -> None:
    listing = dialog._lists["calendar"]
    listing.insertItem(0, listing.takeItem(1))

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
