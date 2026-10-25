"""背景图的三个对话框：生成、给照片起名、管理已保存的那几张。

**三个都不碰文件**：生成只交出配方与名字，照片那个只交出名字，管理的那个只交出一份
「改了哪些名字、删了哪几张」的计划。写缓存与写清单一律由 ``ui/window/settings_flow.py``
调 ``core.backdrops`` 完成。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gallery.core.backdrops import MAX_NAME_LENGTH, PALETTES, Palette, Recipe, name_suggestion
from gallery.core.backdrops.generate import colorize, render_field
from gallery.ui import imaging, theme
from gallery.ui.dialogs.appearance import dialog_style
from gallery.ui.theme import AppTheme, rgba

#: 预览区的大小（点）：16:10，够看清晕染的走向，又不至于把对话框撑满屏。
PREVIEW_SIZE = QSize(720, 450)

MIN_WIDTH = 640

#: 「管理背景」的宽度与列表高度（点）：宽到一条中文名字不至于被挤成省略号，列表露出五行。
MANAGE_MIN_WIDTH = 460
MANAGE_LIST_HEIGHT = 200

#: 色板按钮里那块小色块的尺寸。
CHIP_SIZE = QSize(34, 22)


class BackdropGenerateDialog(QDialog):
    """挑一套配色、随机生成一张，然后决定存不存、给哪一档用。

    **生成是同步的**（一次约半秒）：放进后台线程要处理取消与过期结果，而这里一次只有一个
    用户动作。等待期间光标转圈，预览区保留上一张。
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        seed: int,
        palette: Palette | None = None,
    ) -> None:
        super().__init__(parent)
        self._seed = seed
        self._palette = palette or PALETTES[0]
        self._recipe: Recipe = self._palette.recipe(seed)
        self._outcome = ""
        self._swatches: dict[str, QPushButton] = {}
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self.setWindowTitle("生成背景")
        self.setModal(True)
        self.setMinimumWidth(MIN_WIDTH)
        self._build()
        self._apply_theme()
        self._refresh()

    # ---- 对外 --------------------------------------------------------------

    def outcome(self) -> str:
        return self._outcome

    def recipe(self) -> Recipe:
        return self._recipe

    def name(self) -> str:
        return self._name_field.text().strip()

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        self._preview = QLabel()
        self._preview.setObjectName("backdropPreview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setFixedSize(PREVIEW_SIZE)
        layout.addWidget(self._preview, 0, Qt.AlignmentFlag.AlignHCenter)

        hint = QLabel("挑一套配色，换一张换一个种子；满意了再保存。")
        hint.setObjectName("backdropHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(self._build_palettes())

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel("名称"))
        self._name_field = QLineEdit(name_suggestion(self._recipe))
        self._name_field.setMaxLength(MAX_NAME_LENGTH)
        self._name_field.setObjectName("backdropName")
        row.addWidget(self._name_field, 1)
        layout.addLayout(row)

        self._error = QLabel("")
        self._error.setObjectName("backdropError")
        self._error.hide()
        layout.addWidget(self._error)

        layout.addLayout(self._build_buttons())

    def _build_palettes(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for palette in PALETTES:
            swatch = QPushButton()
            swatch.setObjectName("backdropChip")
            swatch.setCheckable(True)
            swatch.setFixedSize(CHIP_SIZE)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(palette.title)
            swatch.setStyleSheet(_chip_style(_chip_color(palette)))
            swatch.setChecked(palette.name == self._palette.name)
            swatch.clicked.connect(lambda _checked=False, item=palette: self._choose(item))
            group.addButton(swatch)
            row.addWidget(swatch)
            self._swatches[palette.name] = swatch
        row.addStretch(1)
        return row

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        reroll = QPushButton("换一张")
        reroll.setObjectName("backdropReroll")
        reroll.clicked.connect(self._reroll)
        row.addWidget(reroll)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("cancelButton")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.clicked.connect(lambda: self._finish("save"))
        row.addWidget(save)
        for outcome, label in (("light", "设为浅色"), ("dark", "设为深色")):
            button = QPushButton(label)
            button.setObjectName(f"backdrop{outcome.title()}")
            button.clicked.connect(lambda _checked=False, value=outcome: self._finish(value))
            row.addWidget(button)
        return row

    # ---- 交互 --------------------------------------------------------------

    def _choose(self, palette: Palette) -> None:
        """换配色。**同一个种子**，所以换回来还是同一幅画（浓度场有缓存）。"""
        self._palette = palette
        self._recipe = palette.recipe(self._seed)
        self._refresh(palette_changed=True)

    def _reroll(self) -> None:
        self._seed += 1
        self._recipe = self._palette.recipe(self._seed)
        self._refresh(palette_changed=True)

    def _finish(self, outcome: str) -> None:
        if not self.name():
            self._error.setText("给这张背景起个名字吧。")
            self._error.show()
            self._name_field.setFocus()
            return
        self._outcome = outcome
        self.accept()

    def _refresh(self, *, palette_changed: bool = False) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            field = render_field(self._recipe)
            raster = colorize(self._recipe, field)
        finally:
            QApplication.restoreOverrideCursor()
        self._show_pixmap(QPixmap.fromImage(imaging.to_qimage(raster)))
        if palette_changed:
            self._name_field.setText(name_suggestion(self._recipe))

    def _show_pixmap(self, pixmap: QPixmap) -> None:
        self._preview.setPixmap(
            pixmap.scaled(
                PREVIEW_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        app_theme = theme.current_theme()
        self.setStyleSheet(
            dialog_style(app_theme, self.palette())
            + f"QLabel#backdropError {{ color: {theme.rgba(app_theme.error)}; }}"
            + f"QLabel#backdropHint {{ color: {theme.rgba(app_theme.text_at(0.62))}; }}"
        )
        self._preview.setStyleSheet(
            f"QLabel#backdropPreview {{ background: {theme.rgba(app_theme.text_at(0.05))};"
            f" border-radius: 6px; }}"
        )


class BackdropNameDialog(QDialog):
    """给一张照片背景起名，并决定给哪一档用。"""

    def __init__(self, parent: QWidget | None, *, default_name: str) -> None:
        super().__init__(parent)
        self._outcome = ""
        self.setWindowTitle("用照片做背景")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("照片会被复制一份到 Gallery 的缓存里，原文件不动。")
        hint.setObjectName("backdropHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel("名称"))
        self._name_field = QLineEdit(default_name[:MAX_NAME_LENGTH])
        self._name_field.setMaxLength(MAX_NAME_LENGTH)
        row.addWidget(self._name_field, 1)
        layout.addLayout(row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("仅保存")
        save.setObjectName("saveButton")
        save.clicked.connect(lambda: self._finish("save"))
        buttons.addWidget(save)
        for outcome, label in (("light", "设为浅色"), ("dark", "设为深色")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=outcome: self._finish(value))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))

    def outcome(self) -> str:
        return self._outcome

    def name(self) -> str:
        return self._name_field.text().strip()

    def _finish(self, outcome: str) -> None:
        if not self.name():
            return
        self._outcome = outcome
        self.accept()


class BackdropManageDialog(QDialog):
    """列出现有的背景：改名、删除。**改的是本地一份计划**，确认后由调用方一次落实。"""

    def __init__(self, parent: QWidget | None, items: Sequence[tuple[str, str]]) -> None:
        super().__init__(parent)
        self._names: dict[str, str] = dict(items)
        self._original: dict[str, str] = dict(items)
        self._removed: list[str] = []
        self._filling = False
        self.setWindowTitle("管理背景")
        self.setModal(True)
        self.setMinimumWidth(MANAGE_MIN_WIDTH)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._build()
        self._apply_theme()

    def renames(self) -> dict[str, str]:
        """``{id: 新名字}``，只含真的改过、也还在的那几条。"""
        return {
            item_id: name
            for item_id, name in self._names.items()
            if item_id not in self._removed and name != self._original.get(item_id)
        }

    def removed(self) -> tuple[str, ...]:
        return tuple(self._removed)

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel(
            "改名与删除只作用于 Gallery 缓存里的那一份，原照片不动；"
            "点「完成」才生效，取消则什么都不改。"
        )
        hint.setObjectName("backdropHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 按钮先建：列表一填就会问它们能不能按（`currentItemChanged`）。
        buttons = self._build_buttons()
        self._list = QListWidget()
        self._list.setObjectName("backdropList")
        self._list.setMinimumHeight(MANAGE_LIST_HEIGHT)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self._fill()
        layout.addWidget(self._list, 1)

        layout.addLayout(buttons)

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)
        rename = QPushButton("重命名…")
        rename.setObjectName("backdropRename")
        rename.clicked.connect(self._rename)
        remove = QPushButton("删除")
        remove.setObjectName("backdropRemove")
        remove.clicked.connect(self._delete)
        row.addWidget(rename)
        row.addWidget(remove)
        row.addStretch(1)
        done = QPushButton("完成")
        done.setObjectName("saveButton")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        row.addWidget(done)
        self._rename_button = rename
        self._remove_button = remove
        return row

    # ---- 列表内容 ----------------------------------------------------------

    def _fill(self) -> None:
        self._filling = True
        try:
            self._list.clear()
            for item_id, name in self._names.items():
                if item_id not in self._removed:
                    self._list.addItem(_row(item_id, name))
            self._list.setCurrentRow(0)
        finally:
            self._filling = False
        self._ensure_empty_state()
        self._sync_buttons()

    def _ensure_empty_state(self) -> None:
        """删光之后留一行说明，而不是一个空白的框。"""
        if self._list.count():
            return
        placeholder = QListWidgetItem("还没有保存过背景图")
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(placeholder)

    def _selected(self) -> QListWidgetItem | None:
        current = self._list.currentItem()
        if current is not None and _item_id(current) is not None:
            return current
        for row in range(self._list.count()):
            item = self._list.item(row)
            if _item_id(item) is not None:
                return item
        return None

    def _sync_buttons(self) -> None:
        has_row = self._selected() is not None
        self._rename_button.setEnabled(has_row)
        self._remove_button.setEnabled(has_row)

    # ---- 改名与删除 --------------------------------------------------------

    def _rename(self) -> None:
        """在那一行上就地改：再弹一个系统输入框，样式立刻又和这一个不是一套。"""
        item = self._selected()
        if item is None:
            return
        self._list.setCurrentItem(item)
        self._list.editItem(item)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """空名字退回上一个，超长的按同一个上限截断。"""
        if self._filling:
            return
        item_id = _item_id(item)
        if item_id is None:
            return
        name = item.text().strip()[:MAX_NAME_LENGTH] or self._names.get(item_id, "")
        if name != item.text():
            self._list.blockSignals(True)
            try:
                item.setText(name)
            finally:
                self._list.blockSignals(False)
        self._names[item_id] = name

    def _delete(self) -> None:
        item = self._selected()
        if item is None:
            return
        item_id = _item_id(item)
        if item_id is None:
            return
        self._removed.append(item_id)
        self._list.takeItem(self._list.row(item))
        self._ensure_empty_state()
        self._sync_buttons()

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        self.setStyleSheet(_manage_style(theme.current_theme(), self.palette()))


def _row(item_id: str, name: str) -> QListWidgetItem:
    item = QListWidgetItem(name)
    item.setData(Qt.ItemDataRole.UserRole, item_id)
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
    return item


def _item_id(item: QListWidgetItem) -> str | None:
    """列表行的 id；占位那一行没有 id（它不是一张背景）。"""
    value = item.data(Qt.ItemDataRole.UserRole)
    return str(value) if value else None


def _manage_style(app_theme: AppTheme, palette: QPalette) -> str:
    """列表与行内输入框也是这一套底色：默认的列表在深色下像贴上去的一张白纸。"""
    fill = rgba(app_theme.text_at(0.06))
    edge = rgba(app_theme.text_at(0.18))
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    danger_tint = QColor(app_theme.error)
    danger_tint.setAlphaF(0.16)
    return (
        dialog_style(app_theme, palette)
        + f"""
    QListWidget#backdropList {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {fill};
        border: 1px solid {edge};
        border-radius: 6px;
        padding: 4px;
        font-size: 12px;
    }}
    QListWidget#backdropList::item {{ padding: 6px 8px; border-radius: 4px; }}
    QListWidget#backdropList::item:selected {{
        background: {rgba(highlight)};
        color: {rgba(highlighted_text)};
    }}
    QLineEdit {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {fill};
        border: 1px solid {rgba(highlight)};
        border-radius: 4px;
        padding: 3px 6px;
        font-size: 12px;
    }}
    QPushButton#backdropRemove {{ color: {rgba(app_theme.error)}; }}
    QPushButton#backdropRemove:hover {{ background: {rgba(danger_tint)}; }}
    """
    )


def _chip_color(palette: Palette) -> str:
    """小方块用的那一档颜色：取倒数第二个停点，够浓又不至于顶到最深的一头。"""
    return palette.colors[-2][1] if len(palette.colors) > 1 else palette.colors[0][1]


def _chip_style(color: str) -> str:
    """色板小方块：底就是那套配色的末端颜色，勾选时描一圈。"""
    border = "2px solid rgba(0, 0, 0, 0.55)"
    return (
        f"QPushButton {{ background: {color}; border: 1px solid rgba(0,0,0,0.12);"
        f" border-radius: 5px; }}"
        f"QPushButton:checked {{ border: {border}; }}"
    )


__all__ = [
    "MANAGE_LIST_HEIGHT",
    "MANAGE_MIN_WIDTH",
    "MIN_WIDTH",
    "PREVIEW_SIZE",
    "BackdropGenerateDialog",
    "BackdropManageDialog",
    "BackdropNameDialog",
]
