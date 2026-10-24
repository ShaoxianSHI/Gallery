"""调整「用外部应用打开」的次序：三页各一张可拖动的列表（ADR 0081）。

这个对话框一个文件都不碰：它只把用户拖出来的先后读成三组应用 id 交出去，存哪儿、怎么存
由「设置」那一层决定。**第一行就是按钮左半显示的那一项** —— 拖到最前就是让它变成默认。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
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


class ExternalOrderDialog(QDialog):
    """三页各一张列表，拖动调整先后，保存时把三份次序一起交出去。"""

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

        hint = QLabel("拖动调整先后；排在第一的那一项就是按钮左半显示的应用。")
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

        listing = QListWidget()
        listing.setMinimumWidth(MIN_WIDTH)
        listing.setFixedHeight(LIST_HEIGHT)
        listing.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        listing.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # **只在同一个列表里拖**：跨页拖过去没有意义，那会把两个位置的次序混成一条。
        listing.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        listing.setDefaultDropAction(Qt.DropAction.MoveAction)
        listing.setDropIndicatorShown(True)
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
            entry.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
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
    """
    fill = rgba(app_theme.text_at(0.06))
    edge = rgba(app_theme.text_at(0.18))
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#externalOrderHint {{ color: {rgba(app_theme.text_at(0.62))}; }}
    QListWidget {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {fill};
        border: 1px solid {edge};
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
