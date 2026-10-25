"""调整「用外部应用打开」的次序：每一页各一张可拖动的列表（ADR 0081 / 0094）。

这个对话框一个文件都不碰：它只把用户拖出来的先后读成各页的应用 id 交出去，存哪儿、怎么存
由「设置」那一层决定。**第一行就是按钮左半显示的那一项** —— 拖到最前就是让它变成默认。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gallery.ui import theme
from gallery.ui.outline import opaque_over
from gallery.ui.theme import AppTheme, rgba

#: 列表里那一小张图标的边长（点）。与菜单那一档同一量级。
ICON_EDGE: Final = 16

#: 一页列表的高度（点）。六行全露出来 —— 一页最多也就六项（大图页那一组）。
LIST_HEIGHT: Final = 180

#: 对话框最小宽度（点）。太窄时中文应用名与图标挤在一起，拖起来容易抓错行。
MIN_WIDTH: Final = 340

#: 拖动时那一行离指针的高度（点）—— 让落点清楚，又不至于把列表撑开。
ROW_PADDING: Final = 4


@dataclass(frozen=True)
class OrderItem:
    """列表里的一行：应用 id、显示名、图标。"""

    id: str
    name: str
    icon: QIcon | None


@dataclass(frozen=True)
class OrderPage:
    """一页的初始内容。

    :param items: 当前次序（Open 时照它摆）。
    :param builtin: 内置次序（id）；「恢复默认」按它重排，只排这一页。
    """

    page: str
    label: str
    items: tuple[OrderItem, ...]
    builtin: tuple[str, ...]


#: 拖动时带的自定义格式。**不用 Qt 的 internal move**：那条路上「落在某一行身上」会把
#: 目标那一行整个换成拖来的那一项（`QListWidget.dropMimeData` 的覆盖语义），那个应用就从
#: 列表里消失了。这里自己起拖、自己算插入位置，落点永远在行与行之间。
DRAG_MIME: Final = "application/x-gallery-external-order"


class _OrderList(QListWidget):
    """一页的列表：拖动只改先后，**不会覆盖任何一行**。"""

    def __init__(self) -> None:
        super().__init__()
        # 拖由这一层自己起：Qt 的 internal move 落到行上会覆盖（见 :data:`DRAG_MIME`）。
        # ``DropOnly`` 是「接别人递过来的拖，不自己起 Qt 的拖」—— 起拖在 ``mouseMoveEvent``。
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDragEnabled(False)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self._press_row = -1
        self._press_pos = QPoint()
        self._drag_row = -1
        self._drop_row: int | None = None

    # ---- 拖 ---------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_row = self.row(self.itemAt(event.position().toPoint()))
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._press_row >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            offset = event.position().toPoint() - self._press_pos
            if offset.manhattanLength() >= QApplication.startDragDistance():
                row, self._press_row = self._press_row, -1
                self._start_drag(row)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self._press_row = -1
        super().mouseReleaseEvent(event)

    def _start_drag(self, row: int) -> None:
        item = self.item(row)
        if item is None:
            return
        drag = QDrag(self)
        payload = QMimeData()
        payload.setData(DRAG_MIME, b"1")
        drag.setMimeData(payload)
        pixmap = self.viewport().grab(self.visualItemRect(item))
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        self._drag_row = row
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_row = -1
        self._drop_row = None
        self.viewport().update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._accepts(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if not self._accepts(event):
            event.ignore()
            return
        self._drop_row = self._insertion_row(event.position().toPoint())
        self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self._drop_row = None
        self.viewport().update()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if not self._accepts(event):
            event.ignore()
            return
        target = self._insertion_row(event.position().toPoint())
        if self._drag_row >= 0:
            self.move_row(self._drag_row, target)
        self._drop_row = None
        self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def _accepts(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        return event.source() is self and event.mimeData().hasFormat(DRAG_MIME)

    # ---- 行与落点 ----------------------------------------------------------

    def move_row(self, source: int, target: int) -> None:
        """把第 ``source`` 行搬到第 ``target`` 个插入位（``0..count()``，含两端）。

        **一次 take + insert**：搬的是那一项本身，不经过 mime 编解码，于是 id、图标、名字
        都原样跟着走。``target`` 说的是「插到它前面」，因此在被搬的那一行之后时要先减一。
        """
        if not (0 <= source < self.count()) or not (0 <= target <= self.count()):
            return
        item = self.takeItem(source)
        if item is None:
            return
        if target > source:
            target -= 1
        self.insertItem(target, item)
        self.setCurrentItem(item)

    def _insertion_row(self, position: QPoint) -> int:
        """落点对应插到第几行之前；**落在某一行身上看上下半**，落在空白处就是末尾。"""
        index = self.indexAt(position)
        if not index.isValid():
            return self.count()
        rect = self.visualItemRect(self.item(index.row()))
        return index.row() + (1 if position.y() > rect.center().y() else 0)

    def _indicator_y(self, row: int) -> int:
        if row < self.count():
            return max(self.visualItemRect(self.item(row)).top(), 1)
        last = self.count() - 1
        if last < 0:
            return 1
        return min(self.visualItemRect(self.item(last)).bottom(), self.viewport().height() - 2)

    # ---- 画那条插入线 ------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().paintEvent(event)
        if self._drop_row is None:
            return
        painter = QPainter(self.viewport())
        try:
            # 用正文色而不是高亮色：插入线常常就落在被选中那一行的边上，两样都是同一档蓝时
            # 那条线等于看不见。
            line = self.palette().color(QPalette.ColorRole.WindowText)
            line.setAlphaF(0.75)
            painter.setPen(QPen(line, 2))
            y = self._indicator_y(self._drop_row)
            painter.drawLine(3, y, self.viewport().width() - 4, y)
        finally:
            painter.end()


class ExternalOrderDialog(QDialog):
    """四页各一张列表，拖动调整先后，保存时把各页的次序一起交出去。"""

    def __init__(self, parent: QWidget | None, pages: Sequence[OrderPage]) -> None:
        super().__init__(parent)
        self._pages = tuple(pages)
        self._lists: dict[str, QListWidget] = {}
        self._reset_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("外部应用顺序")
        self.setModal(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build()

    # ---- 对外 --------------------------------------------------------------

    def orders(self) -> dict[str, tuple[str, ...]]:
        """每一页按列表里从上到下的顺序交出来的应用 id。"""
        return {
            page.page: tuple(
                self._lists[page.page].item(row).data(Qt.ItemDataRole.UserRole)  # type: ignore[arg-type]
                for row in range(self._lists[page.page].count())
            )
            for page in self._pages
        }

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("拖动调整先后；排在第一项的应用显示在按钮左半。")
        hint.setObjectName("externalOrderHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        tabs = QTabWidget()
        tabs.setObjectName("externalOrderTabs")
        for page in self._pages:
            tabs.addTab(self._build_page(page), page.label)
        self._tabs = tabs
        layout.addWidget(tabs)

        layout.addLayout(self._build_buttons())

        # ⌘W 关闭：不挂这一条的话它会一路走到主窗口，在模态对话框里关掉整个窗口。
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)

    def _build_page(self, page: OrderPage) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        listing = _OrderList()
        listing.setMinimumWidth(MIN_WIDTH)
        listing.setFixedHeight(LIST_HEIGHT)
        listing.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        listing.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._fill(listing, page.items)
        self._lists[page.page] = listing
        layout.addWidget(listing)

        reset = QPushButton("恢复这一页的默认顺序")
        reset.setObjectName("externalOrderReset")
        reset.clicked.connect(
            lambda _checked=False, target=page.page: self._restore_default(target)
        )
        self._reset_buttons[page.page] = reset
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)
        return widget

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    # ---- 列表内容 ----------------------------------------------------------

    def _fill(self, listing: QListWidget, items: Sequence[OrderItem]) -> None:
        listing.clear()
        for item in items:
            entry = QListWidgetItem(item.name)
            if item.icon is not None:
                entry.setIcon(item.icon)
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            listing.addItem(entry)

    def _restore_default(self, page: str) -> None:
        """按内置次序重排这一页；**只动这一页**，另外两页拖过的位置保留。

        内置次序里没有的项（后来装的应用）跟在后面，按它此刻的位置。
        """
        source = next(one for one in self._pages if one.page == page)
        by_id = {item.id: item for item in source.items}
        listing = self._lists[page]
        current = [
            listing.item(row).data(Qt.ItemDataRole.UserRole) for row in range(listing.count())
        ]
        ordered = [by_id[entry_id] for entry_id in source.builtin if entry_id in by_id]
        ordered += [by_id[entry_id] for entry_id in current if entry_id not in source.builtin]
        self._fill(listing, ordered)

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))


def dialog_style(app_theme: AppTheme, palette: QPalette) -> str:
    """对话框的外观：与元数据 / 事件那两个对话框同一份底色，列表那一圈用同一档边框。

    选中那一行取**调色板里的高亮与高亮文字**：深浅两套下只有这一对保证有对比度。
    列表那一圈是**预先合成过的不透明色**：半透明的圆角边框会被 Qt 描成八段（ADR 0035）。
    """
    surface = opaque_over(app_theme.text_at(0.06), app_theme.background)
    fill = rgba(surface)
    edge = opaque_over(app_theme.text_at(0.18), surface)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#externalOrderHint {{ color: {rgba(app_theme.text_at(0.62))}; }}
    QListWidget {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {fill};
        border: 1px solid {rgba(edge)};
        border-radius: 6px;
        padding: {ROW_PADDING}px;
        font-size: 12px;
    }}
    QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
    QListWidget::item:selected {{
        background: {rgba(highlight)};
        color: {rgba(highlighted_text)};
    }}
    QPushButton {{
        color: {rgba(app_theme.text_at(0.9))};
        background: {rgba(app_theme.text_at(0.12))};
        border: none; border-radius: 6px; padding: 5px 8px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {rgba(app_theme.text_at(0.2))}; }}
    QPushButton#externalOrderReset {{ font-size: 11px; }}
    """


__all__ = [
    "ICON_EDGE",
    "LIST_HEIGHT",
    "MIN_WIDTH",
    "ExternalOrderDialog",
    "OrderItem",
    "OrderPage",
    "dialog_style",
]
