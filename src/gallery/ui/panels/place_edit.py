"""编辑一张照片的位置 —— 坐标与三层地名（ADR 0031）。

**这个对话框一个文件都不碰**（与 ``title_bar`` 同一个规矩）：它只负责把用户填的东西
读成一个 :class:`~gallery.core.metadata.place_edit.PlaceEdit`，真正写盘的是
``write_ops`` → :meth:`gallery.core.library.facade.Library.write_place`。按城市库查
候选走的是 ``core`` 的公开接口，也由它自己去读那份随包分发的只读文件。

## 字段顺序：国家 → 省 / 州 → 城市

**由大到小** —— 读的时候就是这么读的（先国家再省再市），而这三个框里真正用来查的是
**城市**：城市名在库里是唯一索引，省与国家只是收窄同名城市的条件。所以补全挂在「城市」
那一栏上，**而它排在最后**：用户从上面往下填，先敲国家再敲省，最后敲城市；城市一敲定，
上面那两栏**自动补全**（用户也可以只敲城市，另外两栏自己长出来）。

## 没有勾选框

编辑器里**一个勾都没有**。「同时按这个地点更新经纬度」默认永远是开的 —— 那本来就是这个
功能存在的意义，为它留一个勾等于让用户每次都要决定一遍「我要不要它联动」。
「清除位置」同样不需要：三层地名与两个坐标框**全清空**就是那个意思，而勾选框只会多出
一种要记的用法。

## 复制 / 粘贴：一张照片的地点搬到另一张

两张在同一处拍的照片，本来只能一个数字一个数字地抄。框下面那两个按钮把这件事变成一下：

- **复制** —— 按一个固定格式写进剪贴板，**人也能读**（要粘进别的地图软件时直接可用）；
- **粘贴** —— 认这个格式，认几种常见写法（半球标记、`°`、空格或逗号分隔、经度在前），
  认不出来就明说，不猜。

## 两个方向都实时回填

| 用户动了 | 立刻发生 |
|---|---|
| 城市 / 省 / 国家 | 在城市库里按名字正查 → 把那个地点的**坐标写进下面两个框** |
| 纬度 / 经度 | 按坐标反查 → 把**城市 / 省 / 国家写进上面三个框** |

「实时」是防抖 250 毫秒（比人手打字快，又不至于每敲一个字就动一次城市库）。

**回填只覆盖另一侧**，且程序自己写进去的框不再触发连锁 —— 靠一个 ``_programmatic``
标志挡住（否则「地名 → 坐标 → 地名」会自己打起来）。没查到的**一个字都不填**：查不到就
是查不到，绝不编一个地名或一个坐标出来。

**「同时按这个地点更新经纬度」不勾时，城市库里查到的坐标不会进那两个框**，而且刚才
自动填进去的会**还原成照片原本的坐标** —— 那个勾说什么就做什么，用户看得见自己关掉了什么。

界面沿用 ``ui.theme`` 那一份底色与文字色（ADR 0029 起的规矩），系统换深浅色时自己跟。
"""

from __future__ import annotations

import re
from typing import Final

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gallery.core.metadata.place_edit import PlaceEdit, PlaceState
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import Place
from gallery.core.places.resolver import PlaceResolver
from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: **少于这么几个字就不查**。
#:
#: 自带的城市库里有「苏」这样一个真实条目（安徽某个被折叠进市里的区，见
#: ``scripts/build_places_db.py`` 折叠区划那一步）。用户敲下第一个字就恰好命中它的话，
#: 屏幕上会立刻跳出「中国 · 安徽省」和一串坐标 —— 那是数据里的碎片，不是用户要填的��方。
MIN_LOOKUP_LENGTH: Final = 2

#: 打字之后多久去查一次。**250 毫秒**：比人手打字快，又不至于每敲一个字
#: 就去动一次城市库（那份表有 2 万多条）。
LOOKUP_DELAY_MS: Final = 250

#: 坐标框里显示几位小数（与写回时的精度一致）。
COORDINATE_DECIMALS: Final = 6

#: 每个输入框的最小宽度。**不是为了好看**：固定尺寸的对话框宽度由内容算出来，
#: 而「Gironde」「United States」「120.155070」这些内容算出来的宽度比能读它窄得多。
FIELD_MIN_WIDTH: Final = 220

#: 「已复制」在按钮上停留多久。
COPIED_HINT_MS: Final = 1400

#: 错误那一行留几行的位置。**固定高度是为了对话框不跳**：报错前后它都在那儿，
#: 对话框不会因为多了一行字就长高一截。
ERROR_LINES: Final = 2


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
        #: 程序自己往框里写字时为真 —— 那些 setter 会触发 ``textChanged``，
        #: 没有这道挡，「地名 → 坐标 → 地名」会自己打起来。
        self._programmatic = False
        #: 「已复制」那个提示的定时器。
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self._restore_copy_label)
        #: 上一次查过之后，两侧各自是什么 —— 只有真变了才再去查。
        self._seen_names: tuple[str, str, str] = ("", "", "")
        self._seen_coordinates: tuple[float | None, float | None] = (None, None)
        self.setWindowTitle("编辑位置")
        self.setModal(True)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build(state, photo_name)
        self._seen_names = self._names()
        self._seen_coordinates = self._coordinates()

    # ---- 装配 --------------------------------------------------------------

    def _build(self, state: PlaceState, photo_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        # **不能改变大小**：用户拖一下窗口边就该是同样大小，而不是把提示框拉成一整屏。
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        title = QLabel(f"编辑「{photo_name}」的位置" if photo_name else "编辑位置")
        title.setObjectName("placeEditTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # 定时器**先建**：下面的 ``setText`` 会触发 ``textChanged``，
        # 而那会来启动它 —— 顺序反了就是「打开对话框时崩一次」。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

        self._country = QLineEdit(state.country or "")
        self._country.setPlaceholderText("国家")
        self._country.textChanged.connect(self._on_names_changed)
        form.addRow("国家", self._country)

        self._region = QLineEdit(state.region or "")
        self._region.setPlaceholderText("省 / 州")
        self._region.textChanged.connect(self._on_names_changed)
        form.addRow("省 / 州", self._region)

        # **普通的输入框**，不是下拉：这一栏和上面两栏一样是「填一个地方」，不是
        # 「从清单里挑一个」。下拉那个朝下的小箭头会让人以为清单是封闭的 —— 而城市库
        # 里没有的名字（用户自己的门牌、景区）恰恰是最该能填进去的那些。
        # 补全仍然在：``QLineEdit.setCompleter`` 会照常弹候选，选中后由 ``_on_picked``
        # 把整条「城市 · 省州 · 国家」换回**光标处那一个城市名**。
        self._city = QLineEdit(state.city or "")
        self._city.setPlaceholderText("城市（先填城市也行）")
        self._city.textChanged.connect(self._on_names_changed)
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._city.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_picked)
        form.addRow("城市", self._city)

        coordinates = state.coordinates()
        self._latitude = QLineEdit(_format(coordinates[0] if coordinates else None))
        self._latitude.setPlaceholderText("留空表示不改动")
        self._latitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("纬度", self._latitude)
        self._longitude = QLineEdit(_format(coordinates[1] if coordinates else None))
        self._longitude.setPlaceholderText("留空表示不改动")
        self._longitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("经度", self._longitude)
        layout.addLayout(form)

        # 第一行三个键：**复制 / 粘贴 / 清空**，各占三分之一。
        # 第二行「取消 / 保存」分别与**粘贴 / 清空**对齐 —— 同一套列网格，因此宽度
        # 与上面那行一模一样，不需要两排手工对齐的推算。
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        self._copy_button = QPushButton("复制")
        self._copy_button.setToolTip("把坐标（以及三层地名）按固定格式写进剪贴板")
        self._copy_button.clicked.connect(self._on_copy)
        self._paste_button = QPushButton("粘贴")
        self._paste_button.setToolTip("从剪贴板读坐标与地名填进上面这些框")
        self._paste_button.clicked.connect(self._on_paste)
        self._clear_button = QPushButton("清空")
        self._clear_button.setToolTip("把这一栏全清空：三层地名与两个坐标")
        self._clear_button.clicked.connect(self._on_clear)

        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.clicked.connect(self._on_accept)
        save.setDefault(True)

        grid.addWidget(self._copy_button, 0, 0)
        grid.addWidget(self._paste_button, 0, 1)
        grid.addWidget(self._clear_button, 0, 2)
        grid.addWidget(cancel, 1, 1)
        grid.addWidget(save, 1, 2)
        # 三列等宽，于是「占满一行」是布局算出来的，不是估出来的。
        for column in range(3):
            grid.setColumnStretch(column, 1)
        for button in (self._copy_button, self._paste_button, self._clear_button, cancel, save):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addLayout(grid)

        self._error = QLabel()
        self._error.setObjectName("placeEditError")
        self._error.setWordWrap(True)
        self._error.setFixedHeight(self.fontMetrics().height() * ERROR_LINES)
        layout.addWidget(self._error)

        # **⌘W 关闭**（`StandardKey.Close` 在 macOS 上就是它）。不挂这一条的话，⌘W 会
        # 一路走到主窗口 —— 在一个模态对话框里关掉整个窗口，那不是用户按键时想做的事。
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)

        # 固定尺寸是按 sizeHint 定的，而 sizeHint 由内容算出来 —— 242px 宽的编辑器
        # 连「United States」都放不下。给每一栏一个下限，宽度就有一个说得过去的下限了。
        for field in (self._city, self._region, self._country, self._latitude, self._longitude):
            field.setMinimumWidth(FIELD_MIN_WIDTH)

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme()))

    # ---- 两边的实时回填 ------------------------------------------------------

    def _on_names_changed(self, *_args: object) -> None:
        if self._programmatic:
            return  # 程序自己写进去的，不当成「用户改了」
        self._timer.start(LOOKUP_DELAY_MS)

    def _on_coordinates_changed(self, *_args: object) -> None:
        if self._programmatic:
            return  # 程序自己填进去的，不当成「用户改了」
        self._last_touched = "coordinates"
        self._timer.start(LOOKUP_DELAY_MS)

    def _refresh(self) -> None:
        """防抖之后真正去查的那一步：两个方向各看一次，只查**真变了**的那一侧。"""
        names = self._names()
        if names != self._seen_names:
            self._seen_names = names
            self._fill_from_names(names)
        coordinates = self._coordinates()
        if coordinates != self._seen_coordinates:
            self._seen_coordinates = coordinates
            self._fill_from_coordinates(coordinates)

    def _fill_from_names(self, names: tuple[str, str, str]) -> None:
        """**地点 → 坐标**：城市库里查得到就把它的坐标填进下面两个框。

        顺带把省与国家补上 —— 用户完全可以只敲城市，让上面两栏自己长出来（查到的那个
        地方带着它们的准确写法，比用户手打的更不容易错）。
        """
        city, region, country = names
        if self._resolver is None or len(city) < MIN_LOOKUP_LENGTH:
            self._refresh_candidates(city)
            return
        found = self._resolver.place_named(city, region=region or None, country=country or None)
        if not found and (region or country):
            # **用户换了城市，而上面两栏还留着上一座城市的省与国家**：按那个旧范围找，
            # 当然找不到（照片原本写着「日本 · 京都府 · 京都」，敲一个「苏州」进去）。
            # 这时只按名字找一遍 —— 查到的结果本来就会把上面两栏改掉。
            found = self._resolver.place_named(city)
        self._candidates = list(found)
        if not found:
            self._refresh_candidates(city)
            return
        place = found[0]
        self._programmatic = True
        try:
            if place.city and place.city != self._city.text().strip():
                self._city.setText(place.city)
            if place.region and place.region != self._region.text().strip():
                self._region.setText(place.region)
            if place.country and place.country != self._country.text().strip():
                self._country.setText(place.country)
            self._latitude.setText(_format(place.latitude))
            self._longitude.setText(_format(place.longitude))
        finally:
            self._programmatic = False
        self._refresh_candidates(city)

    def _fill_from_coordinates(self, coordinates: tuple[float | None, float | None]) -> None:
        """**坐标 → 地点**：反查出最近的城市，把它写进上面三个框。

        **反查不到就一个字都不填**（附近 300 公里没有城市是常事，尤其是海上）——
        保留用户已经写着的地名，好过拿一个编出来的替换掉。
        """
        latitude, longitude = coordinates
        if self._resolver is None or latitude is None or longitude is None:
            return
        place = self._resolver.place_for(latitude, longitude)
        if place is None:
            return
        self._programmatic = True
        try:
            if place.city and place.city != self._city.text().strip():
                self._city.setText(place.city)
            if place.region and place.region != self._region.text().strip():
                self._region.setText(place.region)
            if place.country and place.country != self._country.text().strip():
                self._country.setText(place.country)
        finally:
            self._programmatic = False
        self._refresh_candidates(place.city)

    def _refresh_candidates(self, name: str) -> None:
        """把候选喂给补全窗口。

        **离线、不联网**（ADR 0003）：候选只来自随包分发的那份城市库。用户写「西湖 ·
        断桥」不会有候选 —— 那本来也不该有，库里没有任何一条记录能代表那个点。
        """
        text = name.strip()
        if self._resolver is None or len(text) < MIN_LOOKUP_LENGTH:
            return
        if not self._candidates:
            self._candidates = list(self._resolver.place_named(text))
        if not self._candidates:
            return
        self._completer.setModel(
            QStringListModel(
                [place_label(item.city, item.region, item.country) for item in self._candidates]
            )
        )

    def _on_picked(self, label: str) -> None:
        """用户从补全里挑了一个：把它那一层的省州与国家也填上（**只在空着时填**）。

        只在空着时填：用户自己写的省州是他的选择，补全不该替他改掉。挑中之后坐标
        照常由 ``_refresh`` 那一步填（它已经知道是哪一座城市了）。
        """
        for place in self._candidates:
            if place_label(place.city, place.region, place.country) != label:
                continue
            self._programmatic = True
            try:
                self._city.setText(place.city)
                if not self._region.text().strip() and place.region:
                    self._region.setText(place.region)
                if not self._country.text().strip():
                    self._country.setText(place.country)
            finally:
                self._programmatic = False
            self._seen_names = self._names()
            self._fill_from_names(self._names())
            return

    # ---- 复制 / 粘贴 ---------------------------------------------------------

    def _on_clear(self) -> None:
        """把这一栏全清空 —— 那就是「把位置整个删掉」。

        **不需要一个勾选框**（ADR 0031）：勾选框得先去勾一下才生效，放在五个按钮里
        反而更像一个动作，而「全清空」本来就是编辑器里唯一表达得出「全删」的形状。
        这里只清**框**，仍然要按保存才写盘。
        """
        self._error.clear()
        self._programmatic = True
        try:
            self._city.clear()
            self._region.clear()
            self._country.clear()
            self._latitude.clear()
            self._longitude.clear()
            self._completer.popup().hide()
        finally:
            self._programmatic = False
        self._refresh()

    def _on_copy(self) -> None:
        """把这个地点按固定格式写进剪贴板。

        **格式是人也能读的**：第一行 ``纬度, 经度``（要粘进别的地图软件时就用这一行），
        第二行 ``城市, 省州, 国家``（空的那一档留成空，于是 ``苏州,,中国`` 也认）。
        查得到就写坐标与地名，一样都没有就说一句，不往剪贴板里塞空字符串。
        """
        latitude, longitude = self._coordinates()
        city, region, country = self._names()
        text = _clipboard_text(latitude, longitude, city, region, country)
        if not text:
            self._error.setText("这张照片还没有可以复制的位置。")
            return
        self._error.clear()
        QGuiApplication.clipboard().setText(text)
        self._copy_button.setText("已复制")
        self._copy_timer.start(COPIED_HINT_MS)

    def _restore_copy_label(self) -> None:
        self._copy_button.setText("复制")

    def _on_paste(self) -> None:
        """从剪贴板读坐标与地名，填进上面这些框。

        **认几种常见写法**（半球标记、`°`、空格 / 逗号 / 分号分隔、经度在前），
        认不出来就明说「看不懂」而不是猜一个填进去 —— 填错的位置会被原样写进照片。
        填完照常走一遍回填：粘进来坐标就反查出地点，粘进来地名就正查出坐标。
        """
        parsed = _parse_clipboard(QGuiApplication.clipboard().text())
        if parsed is None:
            self._error.setText("剪贴板里没有可以用的位置。")
            return
        self._error.clear()
        coordinates, names = parsed
        self._programmatic = True
        try:
            if coordinates != (None, None):
                self._latitude.setText(_format(coordinates[0]))
                self._longitude.setText(_format(coordinates[1]))
            if any(names):
                self._city.setText(names[0] or "")
                self._region.setText(names[1] or "")
                self._country.setText(names[2] or "")
        finally:
            self._programmatic = False
        # **只粘到一半就补另一半；两边都粘到了就都别动。**
        #
        # 后者是「从另一张照片搬过来」的主要用法，而那张照片的坐标可能是米级的 —— 让正查
        # 拿市级坐标把它盖掉，等于用户搬了个地点，精度却丢了。
        if coordinates != (None, None) and any(names):
            self._seen_coordinates, self._seen_names = self._coordinates(), self._names()
        else:
            if coordinates != (None, None):
                self._seen_coordinates = (None, None)  # 让下面重跑这一侧的反查
            if any(names):
                self._seen_names = ("", "", "")  # 同上，让它重跑正查
        self._refresh()

    # ---- 校验与取值 --------------------------------------------------------

    def _on_accept(self) -> None:
        problem = _coordinate_problem(self._latitude.text(), self._longitude.text())
        if problem is not None:
            self._error.setText(problem)
            return
        self.accept()

    def _names(self) -> tuple[str, str, str]:
        return (
            self._city.text().strip(),
            self._region.text().strip(),
            self._country.text().strip(),
        )

    def _coordinates(self) -> tuple[float | None, float | None]:
        return (_parse(self._latitude.text()), _parse(self._longitude.text()))

    def _coordinates_emptied(self) -> bool:
        """两个坐标框都被清空了，而照片本来是有坐标的。"""
        had = self._state.coordinates() is not None
        return had and not self._latitude.text().strip() and not self._longitude.text().strip()

    def edit(self) -> PlaceEdit:
        """用户填完的那一份。**空 = 那一层不写**（由 ``core`` 判定该不该删）。"""
        city, region, country = self._names()
        return PlaceEdit(
            city=city or None,
            region=region or None,
            country=country or None,
            latitude=_parse(self._latitude.text()),
            longitude=_parse(self._longitude.text()),
            # **坐标与地名全空**就是「把位置整个清掉」—— 不需要单独的勾（ADR 0031）。
            clear_coordinates=self._coordinates_emptied(),
        )


# ---- 剪贴板格式 ----------------------------------------------------------------

#: 复制出去的格式（**人也能读** —— 要把坐标粘进别的地图软件时，第一行直接可用）::

#:     30.274150, 120.155070
#:     杭州, 浙江省, 中国
#:
#: 没有坐标就只写第二行，没有地名就只写第一行。空的��一层留成空，于是「苏州,,中国」
#: 粘回来仍然是「国家是中国、省州没写」。
#:
#: **不用制表符、不加标题行**：多一种分隔符就多一种要认的写法，而这一行的用途是
#: 「人看得懂 + 我们认得出」，不是变成一个要长期维护的交换格式。
CLIPBOARD_COORDINATE_LINE: Final = 0
CLIPBOARD_NAME_LINE: Final = 1

#: 坐标行里分隔两个数的那些字符。
_SEPARATORS: Final = re.compile(r"[\s,;、/，；]+")

#: 半球标记与度符号，解析时去掉。
_HEMISPHERE: Final = re.compile(r"[NSEWnsew北南东西°'\"″]+")


def _clipboard_text(
    latitude: float | None,
    longitude: float | None,
    city: str,
    region: str,
    country: str,
) -> str:
    """按上面的格式拼出剪贴板里那一段。两边都是空时返回空串。"""
    lines: list[str] = []
    if latitude is not None and longitude is not None:
        lines.append(f"{latitude:.{COORDINATE_DECIMALS}f}, {longitude:.{COORDINATE_DECIMALS}f}")
    if city or region or country:
        lines.append(f"{city}, {region}, {country}")
    return "\n".join(lines)


def _parse_clipboard(
    text: str,
) -> tuple[tuple[float | None, float | None], tuple[str | None, str | None, str | None]] | None:
    """剪贴板里那一段 → ``(坐标, (城市, 省州, 国家))``；认不出来返回 ``None``。

    **认得宽松，但只认一种「形状」**：一行两个十进制度（半球标记、``°``、空格或逗号
    分隔都行），后面可以跟一行 ``城市, 省州, 国家``。**经度在前也认** —— 用范围判
    （纬度不超过 90、经度不超过 180）交换一次，两数都合法就按「纬度在前」收。

    **度分秒（``30°16'27"N``）不认**：那一行会被解析成三个数，于是这一行直接不算坐标。
    把度分秒转成十进制度需要另一套解析，而我们只往照片里写十进制度 —— 猜错的坐标会
    被原样写进照片，比「看不懂」糟糕得多。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    coordinates: tuple[float | None, float | None] = (None, None)
    names: tuple[str | None, str | None, str | None] = (None, None, None)
    index = 0
    pair = _parse_coordinate_pair(lines[CLIPBOARD_COORDINATE_LINE])
    if pair is not None:
        coordinates = pair
        index = 1
    if len(lines) > index:
        line = lines[index]
        # **必须有至少两个逗号**（也就是我们自己的三档写法）。少了这个判据，一句
        # 「随便一段文字」会被当成城市名填进去，而它随后会被原样写进照片。
        parts = [part.strip() for part in line.split(",")]
        if line.count(",") >= 2 and len(parts) == 3:
            names = tuple(part or None for part in parts)  # type: ignore[assignment]
    if coordinates == (None, None) and names == (None, None, None):
        return None
    return (coordinates, names)


def _parse_coordinate_pair(line: str) -> tuple[float, float] | None:
    """一行里的两个十进制度。**不是就是 ``None``**，不做任何猜测。"""
    cleaned = _HEMISPHERE.sub(" ", line.replace("，", ","))
    parts = [part for part in _SEPARATORS.split(cleaned) if part]
    if len(parts) != 2:
        return None
    try:
        first, second = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if abs(first) > 90.0 >= abs(second):  # 经度写在了前面
        first, second = second, first
    if abs(first) > 90.0 or abs(second) > 180.0:
        return None
    return (first, second)


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


__all__ = [
    "COORDINATE_DECIMALS",
    "COPIED_HINT_MS",
    "ERROR_LINES",
    "FIELD_MIN_WIDTH",
    "LOOKUP_DELAY_MS",
    "PlaceEditDialog",
    "dialog_style",
]
