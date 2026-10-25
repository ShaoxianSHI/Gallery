"""背景图的三个对话框：生成、给照片起名、管理已保存的那几张。

**三个都不碰文件**：生成只交出配方与名字，照片那个只交出名字，管理的那个只交出一份
「改了哪些名字、删了哪几张」的计划。写缓存与写清单一律由 ``ui/window/settings_flow.py``
调 ``core.backdrops`` 完成。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QColorDialog,
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

from gallery.core.backdrops import (
    MAX_NAME_LENGTH,
    PALETTES,
    Palette,
    Recipe,
    name_suggestion,
    palette_from_color,
    palette_from_colors,
)
from gallery.core.backdrops.generate import colorize, render_field
from gallery.ui import imaging, theme
from gallery.ui.dialogs.appearance import dialog_style
from gallery.ui.layout import clear_layout
from gallery.ui.outline import opaque_over
from gallery.ui.theme import AppTheme, rgba

#: 预览区的大小（点）：16:10，够看清晕染的走向，又不至于把对话框撑满屏。
PREVIEW_SIZE = QSize(720, 450)

MIN_WIDTH = 640

#: 「管理背景」的宽度与列表高度（点）：宽到一条中文名字不至于被挤成省略号，列表露出五行。
MANAGE_MIN_WIDTH = 460
MANAGE_LIST_HEIGHT = 200

#: 色板按钮里那块小色块的尺寸。
CHIP_SIZE = QSize(34, 22)

#: 「+」最多能新增几套配色（八套预设之外）。再多那一行就放不下，也没有必要 ——
#: 编辑已有配色不受这个限制，它不占新位置。
MAX_ADDED_PALETTES = 3

#: 编辑颜色那个对话框的宽度、色块高度，以及一套配色允许的颜色个数。
PALETTE_MIN_WIDTH = 340
SWATCH_HEIGHT = 24
MIN_PALETTE_COLOURS = 2
MAX_PALETTE_COLOURS = 8


class _PaletteChip(QPushButton):
    """色板里的一格：单击选中，双击打开编辑颜色的对话框。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.double_clicked.emit()
        event.accept()


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
        #: 这一轮能挑的配色：八套预设，加上「+」新增的（改过的预设也在这里，原地替换）。
        self._palettes: list[Palette] = list(PALETTES)
        self._buttons: list[_PaletteChip] = []
        self._added_palettes = 0
        self._add_chip: QPushButton | None = None
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self.setWindowTitle("生成水墨背景")
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

        hint = QLabel("选择配色；双击色块可以编辑它的颜色。")
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
        self._palette_row = row
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, palette in enumerate(self._palettes):
            chip = self._new_chip(index, palette)
            row.addWidget(chip)
        self._add_chip = QPushButton("+")
        self._add_chip.setObjectName("backdropAdd")
        self._add_chip.setFixedSize(CHIP_SIZE)
        self._add_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._add_chip.setToolTip("新增一套配色")
        self._add_chip.clicked.connect(self._add_palette)
        row.addWidget(self._add_chip)
        row.addStretch(1)
        return row

    def _new_chip(self, index: int, palette: Palette) -> _PaletteChip:
        """造一格并接上两件事：单击选中，双击编辑它的颜色。"""
        chip = _PaletteChip()
        chip.setObjectName("backdropChip")
        chip.setCheckable(True)
        chip.setFixedSize(CHIP_SIZE)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chip.clicked.connect(lambda _checked=False, item=index: self._choose(self._palettes[item]))
        chip.double_clicked.connect(lambda item=index: self._edit_palette(item))
        chip.setChecked(palette is self._palette)
        self._sync_chip(index, chip)
        self._group.addButton(chip)
        self._buttons.append(chip)
        return chip

    def _sync_chip(self, index: int, chip: _PaletteChip) -> None:
        palette = self._palettes[index]
        chip.setToolTip(f"{palette.title}（双击可编辑颜色）")
        chip.setStyleSheet(_chip_style([color for _, color in palette.colors]))

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
        for index, candidate in enumerate(self._palettes):
            self._buttons[index].setChecked(candidate is palette)

    def _edit_palette(self, index: int) -> None:
        """双击一格：编辑**这一套**配色的颜色（增删或换色），确定之后就替换掉它。

        预设改完也留在这个位置上 —— 用户改的是他双击的那一格，不是「另存一份」。
        """
        palette = self._palettes[index]
        colours = [color for _, color in palette.colors]
        dialog = BackdropPaletteDialog(self, title=f"编辑颜色 · {palette.title}", colours=colours)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        edited = dialog.colours()
        if edited == tuple(colours):
            return
        was_selected = palette is self._palette
        updated = palette_from_colors(edited, title=palette.title)
        self._palettes[index] = updated
        self._sync_chip(index, self._buttons[index])
        if was_selected:
            self._choose(updated)

    def _add_palette(self) -> None:
        """「+」：**新增一套配色**（不是给某一套加颜色 —— 那是双击色块）。"""
        if self._added_palettes >= MAX_ADDED_PALETTES:
            return
        starter = palette_from_color("#8ea8bf")
        dialog = BackdropPaletteDialog(
            self,
            title="新增配色",
            colours=[color for _, color in starter.colors],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        palette = palette_from_colors(dialog.colours(), title="自定义")
        self._palettes.append(palette)
        chip = self._new_chip(len(self._palettes) - 1, palette)
        self._palette_row.insertWidget(self._palette_row.indexOf(self._add_chip), chip)
        self._added_palettes += 1
        self._sync_add_chip()
        self._choose(palette)

    def _sync_add_chip(self) -> None:
        """到了上限就按不动：「+」只有一个意思（新增一套），不替用户换掉哪一套。"""
        if self._add_chip is None:
            return
        at_limit = self._added_palettes >= MAX_ADDED_PALETTES
        self._add_chip.setEnabled(not at_limit)
        self._add_chip.setToolTip(
            f"最多新增 {MAX_ADDED_PALETTES} 套配色" if at_limit else "新增一套配色"
        )

    def _reroll(self) -> None:
        self._seed += 1
        self._recipe = self._palette.recipe(self._seed)
        self._refresh(palette_changed=True)

    def _finish(self, outcome: str) -> None:
        if not self.name():
            self._error.setText("请为这张背景填写名称。")
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
            self._name_field.setText(name_suggestion(self._recipe, fallback=self._palette.title))

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
        if self._add_chip is not None:
            self._add_chip.setStyleSheet(_add_chip_style(app_theme))


class BackdropPaletteDialog(QDialog):
    """一套配色的颜色清单：逐行一个色块，可以换色、增删（ADR 0103）。

    **只交出颜色**：存哪儿、怎么变成停点归调用方（``BackdropGenerateDialog``）。
    """

    def __init__(self, parent: QWidget | None, *, title: str, colours: Sequence[str]) -> None:
        super().__init__(parent)
        self._colours = [color.lower() for color in colours]
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(PALETTE_MIN_WIDTH)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._build()
        self._apply_theme()

    def colours(self) -> tuple[str, ...]:
        return tuple(self._colours)

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("从上到下就是从浅到深的次序；至少两个颜色，点色块可以换色。")
        hint.setObjectName("paletteHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(6)
        layout.addLayout(self._rows_box)

        self._add_button = QPushButton("添加颜色…")
        self._add_button.setObjectName("paletteAdd")
        self._add_button.clicked.connect(self._add_colour)
        layout.addWidget(self._add_button, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(self._build_buttons())
        self._rebuild_rows()

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(save)
        return row

    def _rebuild_rows(self) -> None:
        clear_layout(self._rows_box)
        for index, colour in enumerate(self._colours):
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            swatch = QPushButton()
            swatch.setObjectName("paletteSwatch")
            swatch.setFixedHeight(SWATCH_HEIGHT)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(f"{colour}（点击换色）")
            swatch.setStyleSheet(_chip_style([colour]))
            swatch.clicked.connect(lambda _checked=False, item=index: self._change_colour(item))
            row.addWidget(swatch, 1)
            remove = QPushButton("移除")
            remove.setObjectName("paletteRemove")
            remove.setEnabled(len(self._colours) > MIN_PALETTE_COLOURS)
            remove.clicked.connect(lambda _checked=False, item=index: self._remove_colour(item))
            row.addWidget(remove)
            self._rows_box.addWidget(holder)
        self._add_button.setEnabled(len(self._colours) < MAX_PALETTE_COLOURS)

    # ---- 增删改 ------------------------------------------------------------

    def _change_colour(self, index: int) -> None:
        chosen = QColorDialog.getColor(QColor(self._colours[index]), self, "配色里的颜色")
        if not chosen.isValid():
            return
        self._colours[index] = chosen.name()
        self._rebuild_rows()

    def _add_colour(self) -> None:
        if len(self._colours) >= MAX_PALETTE_COLOURS:
            return
        chosen = QColorDialog.getColor(QColor(self._colours[-1]), self, "加一个颜色")
        if not chosen.isValid():
            return
        self._colours.append(chosen.name())
        self._rebuild_rows()

    def _remove_colour(self, index: int) -> None:
        if len(self._colours) <= MIN_PALETTE_COLOURS:
            return
        del self._colours[index]
        self._rebuild_rows()

    def _apply_theme(self) -> None:
        app_theme = theme.current_theme()
        self.setStyleSheet(
            dialog_style(app_theme, self.palette())
            + f"QLabel#paletteHint {{ color: {rgba(app_theme.text_at(0.62))}; }}"
        )


class BackdropNameDialog(QDialog):
    """给一张照片背景起名，并决定给哪一档用。"""

    def __init__(self, parent: QWidget | None, *, default_name: str) -> None:
        super().__init__(parent)
        self._outcome = ""
        self.setWindowTitle("使用照片背景")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("照片将复制一份到 Gallery 缓存，原文件不变。")
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
            "改名与删除只作用于 Gallery 缓存中的副本，原照片不变；"
            "点「完成」后生效，取消则不保存任何改动。"
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
        placeholder = QListWidgetItem("暂无已保存的背景图")
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
    """列表与行内输入框也是这一套底色：默认的列表在深色下像贴上去的一张白纸。

    列表那一圈是**预先合成过的不透明色**：半透明的圆角边框会被 Qt 描成八段（ADR 0035）。
    """
    surface = opaque_over(app_theme.text_at(0.06), app_theme.background)
    fill = rgba(surface)
    edge = opaque_over(app_theme.text_at(0.18), surface)
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
        border: 1px solid {rgba(edge)};
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


def _chip_style(colors: Sequence[str]) -> str:
    """色板小方块：一个颜色是纯色，多个是从左到右的渐变；勾选时描一圈。

    **边框颜色预先与底色合成成不透明的**：半透明的圆角边框会被 Qt 描成八段，
    接缝处调两遍就叠成更深的点（ADR 0035）。渐变时按平均色合成 —— 一圈 1 点宽的
    边框跨不出那么大的色差。
    """
    background = _chip_gradient(colors) if len(colors) > 1 else colors[0]
    base = _average_color(colors)
    edge = opaque_over(QColor(0, 0, 0, 30), base)
    checked = opaque_over(QColor(0, 0, 0, 140), base)
    return (
        f"QPushButton {{ background: {background}; border: 1px solid {edge.name()};"
        f" border-radius: 5px; }}"
        f"QPushButton:checked {{ border: 2px solid {checked.name()}; }}"
    )


def _chip_gradient(colors: Sequence[str]) -> str:
    """色块的渐变底：Qt 样式表的 ``qlineargradient``，停点按颜色个数均分。"""
    step = 1.0 / (len(colors) - 1)
    stops = ", ".join(f"stop:{index * step:.3f} {color}" for index, color in enumerate(colors))
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops})"


def _average_color(colors: Sequence[str]) -> QColor:
    """这几个颜色的平均色：渐变时拿它算边框那一档（边框比色块窄得多）。"""
    samples = [QColor(color) for color in colors]
    count = len(samples)
    return QColor(
        round(sum(sample.red() for sample in samples) / count),
        round(sum(sample.green() for sample in samples) / count),
        round(sum(sample.blue() for sample in samples) / count),
    )


def _add_chip_style(app_theme: AppTheme) -> str:
    """「+」那一格：不填颜色，只描一圈 —— 与「已选中的配色」不是同一种东西。

    底色是透明的，所以边框要按**对话框那层底色**合成成不透明的（ADR 0035）。
    """
    edge = opaque_over(app_theme.text_at(0.35), app_theme.background)
    ink = rgba(app_theme.text_at(0.72))
    hover = rgba(app_theme.text_at(0.08))
    return (
        f"QPushButton {{ background: transparent; border: 1px solid {edge.name()};"
        f" border-radius: 5px; color: {ink}; font-size: 15px; padding: 0; }}"
        f"QPushButton:hover {{ background: {hover}; }}"
    )


__all__ = [
    "MANAGE_LIST_HEIGHT",
    "MANAGE_MIN_WIDTH",
    "MAX_ADDED_PALETTES",
    "MAX_PALETTE_COLOURS",
    "MIN_PALETTE_COLOURS",
    "MIN_WIDTH",
    "PALETTE_MIN_WIDTH",
    "PREVIEW_SIZE",
    "SWATCH_HEIGHT",
    "BackdropGenerateDialog",
    "BackdropManageDialog",
    "BackdropNameDialog",
    "BackdropPaletteDialog",
]
