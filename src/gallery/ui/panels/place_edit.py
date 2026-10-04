"""编辑一张照片的位置 —— 坐标与三层地名（ADR 0031）。

**这个对话框一个文件都不碰**（与 ``title_bar`` 同一个规矩）：它只负责把用户填的东西
读成一个 :class:`~gallery.core.metadata.place_edit.PlaceEdit`，真正写盘的是
``write_ops`` → :meth:`gallery.core.library.facade.Library.write_place`。按城市库查
候选走的是 ``core`` 的公开接口，也由它自己去读那份随包分发的只读文件。

**三个框，一个下拉，两个勾**：

- 城市是**可编辑的下拉**：边打字边从城市库里补全（离线，不联网，见 ADR 0003），
  选中的时候顺手把省州与国家也填上 —— 用户在输入框里敲「苏州」，结果面板上写着
  「江苏省 · 苏州」，那是他要的；
- 坐标能留空。**空 = 不改**，而不是「删掉」：真的只想删坐标而保住地名时，把
  「清除位置」不勾、把两个框清空即可（那一层语义由 ``core`` 判定，理由见
  :attr:`~gallery.core.metadata.place_edit.PlaceEdit.clear_coordinates`）；
- 下面那行说明**随改动实时变**：坐标一动就说「地点会按坐标重新推断」，地名一改就说
  「坐标会按这座城市的位置写入」—— 后者是**市级精度**，这句话必须先于保存出现在屏幕上。

界面沿用 ``ui.theme`` 那一份底色与文字色（ADR 0029 起的规矩），系统换深浅色时自己跟。
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from gallery.core.metadata.place_edit import PlaceEdit, PlaceState
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import Place
from gallery.core.places.resolver import PlaceResolver
from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 打字之后多久去查一次候选。**250 毫秒**：比人手打字快，又不至于每敲一个字
#: 就去动一次城市库（那份表有 2 万多条）。
LOOKUP_DELAY_MS: Final = 250

#: 坐标框里显示几位小数（与写回时的精度一致）。
COORDINATE_DECIMALS: Final = 6


def dialog_style(app_theme: AppTheme) -> str:
    """对话框的样式。文字色全部从主题派生，与元数据面板同一份底色。"""
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#placeEditTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#placeEditHint {{ color: {rgba(app_theme.text_at(0.58))}; font-size: 11px; }}
    QLabel#placeEditError {{ color: {rgba(app_theme.error)}; font-size: 11px; }}
    QLineEdit, QComboBox {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {rgba(app_theme.text_at(0.08))};
        border: 1px solid {rgba(app_theme.text_at(0.18))};
        border-radius: 5px;
        padding: 3px 6px;
        font-size: 12px;
    }}
    QCheckBox {{ color: {rgba(app_theme.text_at(0.78))}; font-size: 12px; }}
    QPushButton {{
        color: {rgba(app_theme.text_at(0.9))};
        background: {rgba(app_theme.text_at(0.12))};
        border: none; border-radius: 6px; padding: 5px 14px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {rgba(app_theme.text_at(0.2))}; }}
    """


class PlaceEditDialog(QDialog):
    """编辑位置。:meth:`edit` 给出用户填的那一份，写盘不归这里管。"""

    def __init__(
        self,
        parent: QWidget | None,
        state: PlaceState,
        *,
        resolver: PlaceResolver | None = None,
        photo_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._resolver = resolver
        #: 补全窗口里那一排候选对应的地点（与 ``_completer`` 的模型同序）。
        self._candidates: list[Place] = []
        self.setWindowTitle("编辑位置")
        self.setModal(True)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build(state, photo_name)

    # ---- 装配 --------------------------------------------------------------

    def _build(self, state: PlaceState, photo_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel(f"编辑「{photo_name}」的位置" if photo_name else "编辑位置")
        title.setObjectName("placeEditTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        # 定时器**先建**：``setCurrentText`` 会触发 ``currentTextChanged``，而那��
        # 会去启动它 —— 顺序反了就是「打开对话框时崩一次」。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh_candidates)

        self._city = QComboBox()
        self._city.setEditable(True)
        self._city.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._city.setPlaceholderText("城市")
        self._city.currentTextChanged.connect(self._on_city_changed)
        # 补全是**弹窗**，不是把候选塞进下拉里：下拉里那几行会留在原地，用户改一个字
        # 之后它们就变成了过期的答案。
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._city.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_picked)
        form.addRow("城市", self._city)

        self._region = QLineEdit(state.region or "")
        self._region.setPlaceholderText("省 / 州")
        self._region.textChanged.connect(self._update_hint)
        form.addRow("省 / 州", self._region)

        self._country = QLineEdit(state.country or "")
        self._country.setPlaceholderText("国家")
        self._country.textChanged.connect(self._update_hint)
        form.addRow("国家", self._country)

        coordinates = state.coordinates()
        self._latitude = QLineEdit(_format(coordinates[0] if coordinates else None))
        self._latitude.setPlaceholderText("留空表示不改动")
        self._latitude.textChanged.connect(self._update_hint)
        form.addRow("纬度", self._latitude)
        self._longitude = QLineEdit(_format(coordinates[1] if coordinates else None))
        self._longitude.setPlaceholderText("留空表示不改动")
        self._longitude.textChanged.connect(self._update_hint)
        form.addRow("经度", self._longitude)
        layout.addLayout(form)

        self._sync = QCheckBox("同时按这个地点更新经纬度")
        self._sync.setChecked(True)
        self._sync.toggled.connect(self._update_hint)
        layout.addWidget(self._sync)

        self._clear = QCheckBox("清除位置（坐标与地名一起清空）")
        self._clear.toggled.connect(self._update_enabled)
        layout.addWidget(self._clear)

        self._hint = QLabel()
        self._hint.setObjectName("placeEditHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._error = QLabel()
        self._error.setObjectName("placeEditError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

        self._city.setCurrentText(state.city or "")
        self._update_hint()
        self._update_enabled()

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme()))

    # ---- 城市库补全 --------------------------------------------------------

    def _on_city_changed(self, text: str) -> None:
        self._update_hint()
        self._timer.start(LOOKUP_DELAY_MS)

    def _refresh_candidates(self) -> None:
        """按当前敲的字去城市库里找候选。

        **离线、不联网**（ADR 0003）：候选只来自随包分发的那份城市库。用户写「西湖 ·
        断桥」不会有候选 —— 那本来也不该有，库里没有任何一条记录能代表那个点。
        """
        text = self._city.currentText().strip()
        if self._resolver is None or not text:
            return
        self._candidates = list(
            self._resolver.place_named(
                text,
                region=self._region.text().strip() or None,
                country=self._country.text().strip() or None,
            )
        )
        self._completer.setModel(
            QStringListModel(
                [place_label(item.city, item.region, item.country) for item in self._candidates]
            )
        )

    def _on_picked(self, label: str) -> None:
        """用户从补全里挑了一个：把它那一层的省州与国家也填上（**只在空着时填**）。

        只在空着时填：用户自己写的省州是他的选择，补全不该替他改掉。
        """
        for place in self._candidates:
            if place_label(place.city, place.region, place.country) != label:
                continue
            self._city.setCurrentText(place.city)
            if not self._region.text().strip() and place.region:
                self._region.setText(place.region)
            if not self._country.text().strip():
                self._country.setText(place.country)
            return

    # ---- 校验与取值 --------------------------------------------------------

    def _on_accept(self) -> None:
        problem = _coordinate_problem(self._latitude.text(), self._longitude.text())
        if problem is not None:
            self._error.setText(problem)
            self._error.show()
            return
        self.accept()

    def _coordinates_emptied(self) -> bool:
        """两个坐标框都被清空了，而照片本来是有坐标的。"""
        had = self._state.coordinates() is not None
        return had and not self._latitude.text().strip() and not self._longitude.text().strip()

    def _coordinates_changed(self) -> bool:
        current = _parse(self._latitude.text()), _parse(self._longitude.text())
        original = self._state.coordinates()
        if original is None:
            return current != (None, None)
        return current != original

    def _names_changed(self) -> bool:
        return (
            self._city.currentText().strip() or None,
            self._region.text().strip() or None,
            self._country.text().strip() or None,
        ) != self._state.names()

    def _update_enabled(self) -> None:
        """勾了「清除位置」就把其余控件关掉 —— 那一下删的是全部，留下别的没意义。"""
        clearing = self._clear.isChecked()
        for widget in (self._city, self._region, self._country, self._latitude, self._longitude):
            widget.setEnabled(not clearing)
        self._sync.setEnabled(not clearing)
        self._update_hint()

    def _update_hint(self, *_args: object) -> None:
        """那行说明。**它是「保存下去会发生什么」的预告**，不是事后解释。"""
        if self._clear.isChecked():
            self._hint.setText("保存后，坐标与三层地名都会从照片里删掉。⌘Z 可以撤销。")
            return
        if self._coordinates_emptied():
            self._hint.setText("保存后，这张照片的坐标会被删掉，地点保留。⌘Z 可以撤销。")
            return
        if self._coordinates_changed():
            self._hint.setText("保存后，地点会按新坐标重新推断（离线，最近的城市）。")
            return
        if self._names_changed() and self._sync.isChecked():
            self._hint.setText("保存后，坐标会按这座城市的位置写入 —— 那是市级精度，不是拍摄点。")
            return
        self._hint.setText("不改动坐标。⌘Z 可以撤销。")

    def edit(self) -> PlaceEdit:
        """用户填完的那一份。**空 = 那一层不写**（由 ``core`` 判定该不该删）。"""
        return PlaceEdit(
            city=self._city.currentText().strip() or None,
            region=self._region.text().strip() or None,
            country=self._country.text().strip() or None,
            latitude=_parse(self._latitude.text()),
            longitude=_parse(self._longitude.text()),
            update_coordinates=self._sync.isChecked(),
            clear=self._clear.isChecked(),
            clear_coordinates=self._coordinates_emptied(),
        )


# ---- 小工具 -------------------------------------------------------------------


def _parse(text: str) -> float | None:
    """输入框里的一个数。空 / 看不懂就返回 ``None``（不是 0 —— 0 是一个真实的坐标）。"""
    cleaned = text.strip().replace("，", ",")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.{COORDINATE_DECIMALS}f}"


def _coordinate_problem(latitude: str, longitude: str) -> str | None:
    """两个框里有一处不对就说清楚是哪一处。**不弹框**：对话框里那行红字就够。"""
    for label, text, limit in (("纬度", latitude, 90.0), ("经度", longitude, 180.0)):
        if not text.strip():
            continue
        value = _parse(text)
        if value is None:
            return f"{label}不是一个数字：「{text.strip()}」。可以直接填十进制度，例如 30.27415。"
        if abs(value) > limit:
            return f"{label}超范围了（{limit}）：写的是 {value}。"
    if bool(latitude.strip()) != bool(longitude.strip()):
        return "经度和纬度要一起填：只填一个，说不出一个位置。"
    return None


__all__ = ["COORDINATE_DECIMALS", "LOOKUP_DELAY_MS", "PlaceEditDialog", "dialog_style"]
