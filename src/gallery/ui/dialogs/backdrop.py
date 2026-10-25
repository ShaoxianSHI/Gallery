"""背景图的三个对话框：生成、给照片起名、管理已保存的那几张。

**三个都不碰文件**：生成只交出配方与名字，照片那个只交出名字，管理的那个只交出一份
「改了哪些名字、删了哪几张」的计划。写缓存与写清单一律由 ``ui/window/settings_flow.py``
调 ``core.backdrops`` 完成。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QInputDialog,
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

#: 预览区的大小（点）：16:10，够看清晕染的走向，又不至于把对话框撑满屏。
PREVIEW_SIZE = QSize(720, 450)

MIN_WIDTH = 640

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
        self.setWindowTitle("管理背景")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        self._list = QListWidget()
        self._list.setObjectName("backdropList")
        self._fill()
        layout.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        rename = QPushButton("重命名…")
        rename.clicked.connect(self._rename)
        remove = QPushButton("删除")
        remove.clicked.connect(self._delete)
        buttons.addWidget(rename)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        done = QPushButton("完成")
        done.setObjectName("saveButton")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))

    def renames(self) -> dict[str, str]:
        """``{id: 新名字}``，只含真的改过、也还在的那几条。"""
        return {
            item_id: name
            for item_id, name in self._names.items()
            if item_id not in self._removed and name != self._original.get(item_id)
        }

    def removed(self) -> tuple[str, ...]:
        return tuple(self._removed)

    def _fill(self) -> None:
        self._list.clear()
        for item_id, name in self._names.items():
            if item_id in self._removed:
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, item_id)
            self._list.addItem(item)

    def _selected(self) -> QListWidgetItem | None:
        return self._list.currentItem() or (self._list.item(0) if self._list.count() else None)

    def _rename(self) -> None:
        item = self._selected()
        if item is None:
            return
        item_id = str(item.data(Qt.ItemDataRole.UserRole))
        current = self._names[item_id]
        text, accepted = _ask_name(self, current)
        if not accepted or not text:
            return
        self._names[item_id] = text[:MAX_NAME_LENGTH]
        item.setText(self._names[item_id])

    def _delete(self) -> None:
        item = self._selected()
        if item is None:
            return
        self._removed.append(str(item.data(Qt.ItemDataRole.UserRole)))
        self._list.takeItem(self._list.row(item))


def _ask_name(parent: QWidget, current: str) -> tuple[str, bool]:
    text, accepted = QInputDialog.getText(parent, "重命名背景", "名称", text=current)
    return text.strip(), accepted


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
    "MIN_WIDTH",
    "PREVIEW_SIZE",
    "BackdropGenerateDialog",
    "BackdropManageDialog",
    "BackdropNameDialog",
]
