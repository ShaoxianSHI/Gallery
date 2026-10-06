"""编辑一张照片的元数据 —— 拍摄时间与位置（ADR 0030 / 0031 / 0032 / 0039）。

这个对话框一个文件都不碰：它只把用户填的那一份读成 ``MetadataEdit``，写盘走 ``write_ops``。
``DateTimeOriginal`` 是墙上时间，时区在单独的 ``OffsetTimeOriginal`` 里而相机多半不写它 ——
留空就是「不记这一项」，绝不按本机时区替用户补一个。地名由大到小填、真正查的是城市。

**跨层填充由两个按钮手动触发**（ADR 0039）：「地点 → 经纬度」与「经纬度 → 地点」。
实时回填（ADR 0031 决策 7）做不到「对话框显示的就是将被写入的那一份」：城市库里有些城市的
`region` 是空的，反查不到省，而框里还留着上一座城市的省 —— 显示与写下去的成了两份。
剪贴板格式见 :func:`_clipboard_text`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gallery.core.metadata.capture_time import CaptureTimeEdit
from gallery.core.metadata.exif import normalize_capture_time, normalize_offset
from gallery.core.metadata.metadata_edit import MetadataEdit, MetadataState
from gallery.core.metadata.place_edit import PlaceEdit
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import Place
from gallery.core.places.resolver import PlaceResolver
from gallery.ui import theme
from gallery.ui.outline import opaque_over
from gallery.ui.theme import AppTheme, rgba

#: 少于这么几个字就不查：城市库里有「苏」这样一个真实条目，用户敲下第一个字就恰好命中
#: 它的话，屏幕上会立刻跳出「中国 · 安徽省」和一串坐标。
MIN_LOOKUP_LENGTH: Final = 2

#: 打字之后多久去查一次：比人手打字快，又不至于每敲一个字就查一遍两万多条的城市库。
LOOKUP_DELAY_MS: Final = 250

COORDINATE_DECIMALS: Final = 6

#: 每个输入框的最小宽度：对话框宽度由内容算出来，而「Gironde」这类内容算出来的宽度比能读
#: 它窄得多；九个键（4 个字的那几个）也靠它才摆得下。
FIELD_MIN_WIDTH: Final = 220

#: 输入框那一圈的圆角半径（点）—— 与搜索胶囊、改名输入框同一档手感。
FIELD_RADIUS: Final = 5

COPIED_HINT_MS: Final = 1400

#: 错误那一行留几行的位置。固定高度是为了对话框不跳：报错前后它都在那儿。
ERROR_LINES: Final = 2


def dialog_style(app_theme: AppTheme) -> str:
    """对话框的样式。文字色全部从主题派生，与元数据面板同一份底色。

    输入框那圈边框与它自己的底色**先合成成不透明的**（理由见 :mod:`gallery.ui.outline`）：
    Qt 把样式表那一圈描成八段，半透明的颜色在接缝处被调两遍就深一点。
    """
    fill = opaque_over(app_theme.text_at(0.08), app_theme.background)
    edge = opaque_over(app_theme.text_at(0.18), fill)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#metadataEditTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#metadataEditError {{ color: {rgba(app_theme.error)}; font-size: 11px; }}
    QLineEdit, QComboBox {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {rgba(fill)};
        border: 1px solid {rgba(edge)};
        border-radius: {FIELD_RADIUS}px;
        padding: 3px 6px;
        font-size: 12px;
    }}
    QPushButton {{
        color: {rgba(app_theme.text_at(0.9))};
        background: {rgba(app_theme.text_at(0.12))};
        border: none; border-radius: 6px; padding: 5px 8px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {rgba(app_theme.text_at(0.2))}; }}
    """


class MetadataEditDialog(QDialog):
    """编辑元数据。:meth:`edit` 给出用户填的那一份，写盘不归这里管。"""

    def __init__(
        self,
        parent: QWidget | None,
        state: MetadataState,
        *,
        resolver: PlaceResolver | None = None,
        photo_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._resolver = resolver
        self._candidates: list[Place] = []
        #: 程序自己往框里写字时为真：那些 setter 会触发 ``textChanged``，
        #: 没有这道挡，「地名 → 坐标 → 地名」会自己打起来。
        self._programmatic = False
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self._restore_copy_labels)
        #: 上一次查过之后，两侧各自是什么 —— 只有真变了才再去查。
        self._seen_names: tuple[str, str, str] = ("", "", "")
        self._seen_coordinates: tuple[float | None, float | None] = (None, None)
        self.setWindowTitle("编辑元数据")
        self.setModal(True)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build(state, photo_name)
        self._seen_names = self._names()
        self._seen_coordinates = self._coordinates()

    def _build(self, state: MetadataState, photo_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        # 不能改变大小：用户拖一下窗口边就该是同样大小。
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        title = QLabel(f"编辑「{photo_name}」的元数据" if photo_name else "编辑元数据")
        title.setObjectName("metadataEditTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # 定时器先建：下面的 ``setText`` 会触发 ``textChanged`` 并来启动它，顺序反了就崩。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

        # ---- 拍摄时间（ADR 0032）：最上面两栏 ------------------------------
        # 下面不摆小字提示：标签已经写着「拍摄时间」「时区」，改自己的照片不需要被先教育一遍。
        self._taken_at = QLineEdit(_display_time(state.capture.taken_at))
        form.addRow("拍摄时间", self._taken_at)
        self._offset = QLineEdit(state.capture.offset or "")
        form.addRow("时区", self._offset)

        self._country = QLineEdit(state.place.country or "")
        self._country.setObjectName("country")
        self._country.textChanged.connect(self._on_names_changed)
        form.addRow("国家", self._country)

        self._region = QLineEdit(state.place.region or "")
        self._region.setObjectName("region")
        self._region.textChanged.connect(self._on_names_changed)
        form.addRow("省 / 州", self._region)

        # 普通的输入框而不是下拉：城市库里没有的名字（门牌、景区）恰恰是最该能填进去的。
        self._city = QLineEdit(state.place.city or "")
        self._city.setObjectName("city")
        self._city.textChanged.connect(self._on_names_changed)
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        #: 候选的模型建一次就够（理由见 :meth:`_refresh_candidates`）。
        self._candidate_model = QStringListModel(self)
        self._city.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_picked)
        form.addRow("城市", self._city)

        coordinates = state.place.coordinates()
        self._latitude = QLineEdit(_format(coordinates[0] if coordinates else None))
        self._latitude.setObjectName("latitude")
        self._latitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("纬度", self._latitude)
        self._longitude = QLineEdit(_format(coordinates[1] if coordinates else None))
        self._longitude.setObjectName("longitude")
        self._longitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("经度", self._longitude)
        layout.addLayout(form)

        # 报错那一行在按钮上面：它说的是上面哪个框不对，放在那儿才找得回去。
        self._error = QLabel()
        self._error.setObjectName("metadataEditError")
        self._error.setWordWrap(True)
        self._error.setFixedHeight(self.fontMetrics().height() * ERROR_LINES)
        layout.addWidget(self._error)

        layout.addLayout(self._build_buttons())

        # ⌘W 关闭：不挂这一条的话它会一路走到主窗口，在模态对话框里关掉整个窗口。
        # 显式写出来而不写 ``StandardKey.Close`` —— Qt 在 macOS 上把后者解析成 ⌘F4。
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)

        # 固定尺寸按 sizeHint 定，而 242px 宽的编辑器连「United States」都放不下。
        for field in (
            self._taken_at,
            self._offset,
            self._city,
            self._region,
            self._country,
            self._latitude,
            self._longitude,
        ):
            field.setMinimumWidth(FIELD_MIN_WIDTH)

    def _build_buttons(self) -> QLayout:
        """上面一行两个方向的按钮，下面九宫格。按行读：搬、搬、动作。

        第一行那两个是 ADR 0039 加的：跨层填充只发生在用户按下的那一刻。
        **它们在独立的布局里，不进九宫格的列网格** —— 「地点 → 经纬度」这个标签比
        「复制」宽得多，混进同一套列里会把第 0 列撑宽，而九宫格那三列等宽是它的性质
        （也正是下面那条用例量的东西）。
        """
        column = QVBoxLayout()
        column.setContentsMargins(0, 2, 0, 0)
        column.setSpacing(6)
        column.addLayout(self._build_direction_buttons())
        column.addLayout(self._build_action_grid())
        return column

    def _build_direction_buttons(self) -> QLayout:
        """那两个方向的键，各占一半宽。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._names_to_coordinates_button = QPushButton("地点 → 经纬度")
        self._names_to_coordinates_button.setToolTip(
            "按城市框里的名字，从城市库查出这座市的省、国家与坐标，填进其余几栏"
            "（库里没有的那一层会清空）。"
        )
        self._names_to_coordinates_button.clicked.connect(self._on_names_to_coordinates)
        self._coordinates_to_names_button = QPushButton("经纬度 → 地点")
        self._coordinates_to_names_button.setToolTip(
            "按下面两个坐标框里的位置，从城市库查出最近的城市，填进上面三个地名框"
            "（城市库里没有的那一层会清空，例如直辖市没有省）。"
        )
        self._coordinates_to_names_button.clicked.connect(self._on_coordinates_to_names)

        for button in (self._names_to_coordinates_button, self._coordinates_to_names_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(button)
        return row

    def _build_action_grid(self) -> QGridLayout:
        """九个键，3×3 等宽。按行读：搬、搬、动作。"""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        self._copy_button = QPushButton("复制")
        self._copy_button.clicked.connect(self._on_copy)
        self._copy_time_button = QPushButton("复制时间")
        self._copy_time_button.clicked.connect(self._on_copy_time)
        self._copy_place_button = QPushButton("复制位置")
        self._copy_place_button.clicked.connect(self._on_copy_place)

        self._paste_button = QPushButton("粘贴")
        self._paste_button.clicked.connect(self._on_paste)
        self._paste_time_button = QPushButton("粘贴时间")
        self._paste_time_button.clicked.connect(self._on_paste_time)
        self._paste_place_button = QPushButton("粘贴位置")
        self._paste_place_button.clicked.connect(self._on_paste_place)

        # 第三行三个动作：清空只清位置，拍摄时间不该被它顺手删掉。
        self._clear_button = QPushButton("清空位置")
        self._clear_button.clicked.connect(self._on_clear)

        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.clicked.connect(self._on_accept)
        save.setDefault(True)

        buttons = (
            (self._copy_button, self._copy_time_button, self._copy_place_button),
            (self._paste_button, self._paste_time_button, self._paste_place_button),
            (self._clear_button, cancel, save),
        )
        for row, line in enumerate(buttons):
            for column, button in enumerate(line):
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                grid.addWidget(button, row, column)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        return grid

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme()))

    def _on_names_changed(self, *_args: object) -> None:
        if self._programmatic:
            return  # 程序自己写进去的，不当成「用户改了」
        self._timer.start(LOOKUP_DELAY_MS)

    def _on_coordinates_changed(self, *_args: object) -> None:
        if self._programmatic:
            return  # 程序自己填进去的，不当成「用户改了」
        self._timer.start(LOOKUP_DELAY_MS)

    def _refresh(self) -> None:
        """防抖之后真正要做的那一步：**只刷候选列表**。

        跨层填充已经不在这里了（ADR 0039）：这一步顺手改对面那三个框，就会让「框里显示的」与
        「真正写下去的」成为两份东西。补全还要留着 —— 那是候选，不是写盘。
        """
        names = self._names()
        if names != self._seen_names:
            self._seen_names = names
            self._refresh_candidates(names[0])

    def _on_names_to_coordinates(self) -> None:
        """「地点 → 经纬度」：按城市框里的名字把**这个地点的其余各层**都查出来填上。

        填的是省 / 国家与两个坐标框 —— 城市是输入，不动。省与国家用库里那一带的准确写法
        补上（ADR 0031 决策 7「只敲城市就行」），**库里本来就没有的那一层照清空**
        （ADR 0039 决策 2：这个对话框显示的就是将被写入的那一份）。

        城市是**输入**所以不动；省 / 国家与坐标是**结论**，按库里的写法改写，
        用户亲手敲过的那一个不动（决策 3）。

        查不到就是查不到：城市库里没有这个名字（门牌、景区）就**一个字都不改**，
        并如实说明，绝不配一个看起来精确、实际错得离谱的值（ADR 0031 决策 3）。
        """
        self._error.clear()
        city, region, country = self._names()
        if self._resolver is None or len(city) < MIN_LOOKUP_LENGTH:
            self._error.setText("先把城市填上，城市库是按市名查的。")
            return
        found = self._resolver.place_named(city, region=region or None, country=country or None)
        if not found and (region or country):
            # 上面两栏还留着上一座城市的省与国家（照片原本写着「日本 · 京都府 · 京都」，
            # 敲一个「苏州」进去），按那个过期范围查当然查不到。
            found = self._resolver.place_named(city)
        if not found:
            self._error.setText(f"城市库里没有「{city}」，省与国家、坐标都保持原样。")
            return
        place = found[0]
        self._programmatic = True
        try:
            self._region.setText(place.region or "")
            self._country.setText(place.country or "")
            self._latitude.setText(_format(place.latitude))
            self._longitude.setText(_format(place.longitude))
        finally:
            self._programmatic = False
        self._seen_names, self._seen_coordinates = self._names(), self._coordinates()
        self._refresh_candidates(city)

    def _on_coordinates_to_names(self) -> None:
        """「经纬度 → 地点」：按两个坐标框里的位置查出最近的城市，填进三个地名框。

        **逐层写入，包括城市库里本来就没有的那一层**（ADR 0039 决策 2）。城市库里
        北京、上海、天津、重庆、香港、澳门这 151 条的 ``region`` 是空的，于是反查到上海
        就该把「省 / 州」清空 —— 留着上一座城市的省既错又看不懂。这个对话框显示的就是
        将被写入的那一份，所以空必须显示成空。

        **三个框一律按城市库的结果覆盖**，不因为是用户亲手敲的就手下留情：他按的是
        「以城市库为准」这个按钮，保留一半才是另一种更坏的答案 —— 界面上那一半是旧的，
        而保存时会一起被覆盖。

        附近 300 公里没有城市（海上很常见）就**一个字都不改**，并如实说明 ——
        该由用户判断是地名错了还是坐标错了（ADR 0031 决策 4）。
        """
        self._error.clear()
        latitude, longitude = self._coordinates()
        if self._resolver is None or latitude is None or longitude is None:
            self._error.setText("先把经纬度填上，两个都要。")
            return
        place = self._resolver.place_for(latitude, longitude)
        if place is None:
            self._error.setText("附近没有城市记录，原来的地名没有改动。")
            return
        self._programmatic = True
        try:
            self._city.setText(place.city or "")
            self._region.setText(place.region or "")
            self._country.setText(place.country or "")
        finally:
            self._programmatic = False
        self._seen_names = self._names()
        self._refresh_candidates(self._names()[0])

    def _refresh_candidates(self, name: str) -> None:
        """把候选喂给补全窗口；离线、不联网（ADR 0003）：候选只来自随包那份城市库。

        模型只建一次 —— 这一格每 250 毫秒就走一遍，而每次新建 ``QStringListModel`` 都要
        重新接一遍 ``QCompleter`` 的内部连接。
        """
        text = name.strip()
        if self._resolver is None or len(text) < MIN_LOOKUP_LENGTH:
            return
        if not self._candidates:
            self._candidates = list(self._resolver.place_named(text))
        if not self._candidates:
            return
        if self._completer.model() is not self._candidate_model:
            self._completer.setModel(self._candidate_model)
        self._candidate_model.setStringList(
            [place_label(item.city, item.region, item.country) for item in self._candidates]
        )

    def _on_picked(self, label: str) -> None:
        """用户从补全里挑了一个：把它那一层的省州与国家也填上。

        只在空着时填 —— 用户自己写的省州是他的选择（ADR 0039 决策 5：补全是补全，
        不是覆盖）。**不再顺手反查坐标**：跨层填充只发生在那两个按钮上。
        """
        for place in self._candidates:
            if place_label(place.city, place.region, place.country) != label:
                continue
            self._programmatic = True
            try:
                self._city.setText(place.city)
                if not self._region.text().strip() and place.region:
                    self._region.setText(place.region)
                if not self._country.text().strip() and place.country:
                    self._country.setText(place.country)
            finally:
                self._programmatic = False
            self._seen_names = self._names()
            return

    def _on_clear(self) -> None:
        """把位置那一栏全清空 —— 那就是「把位置整个删掉」。

        不需要一个勾选框（ADR 0031）；这里只清框，仍然要按保存才写盘。拍摄时间那两栏不动。
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
        self._copy_all(self._copy_button)

    def _on_copy_time(self) -> None:
        self._copy_all(self._copy_time_button, time_only=True)

    def _on_copy_place(self) -> None:
        self._copy_all(self._copy_place_button, place_only=True)

    def _copy_all(
        self,
        button: QPushButton,
        *,
        time_only: bool = False,
        place_only: bool = False,
    ) -> None:
        """把这个对话框里此刻的字段按固定格式写进剪贴板。

        格式是人也能读的（时间一行、坐标一行、地名一行），粘进别的地图软件时坐标那一行直接
        可用。三段里没填的就不写那一行 —— 空字符串粘回来时得分辨「没有」还是「空着」。
        """
        text = _clipboard_text(
            self._capture(),
            self._coordinates(),
            self._names(),
            time_only=time_only,
            place_only=place_only,
        )
        if not text:
            self._error.setText("这张照片还没有可以复制的内容。")
            return
        self._error.clear()
        QGuiApplication.clipboard().setText(text)
        button.setText("已复制")
        self._copy_timer.start(COPIED_HINT_MS)

    def _restore_copy_labels(self) -> None:
        self._copy_button.setText("复制")
        self._copy_time_button.setText("复制时间")
        self._copy_place_button.setText("复制位置")

    def _on_paste(self) -> None:
        self._paste(time_only=False, place_only=False)

    def _on_paste_time(self) -> None:
        self._paste(time_only=True, place_only=False)

    def _on_paste_place(self) -> None:
        self._paste(time_only=False, place_only=True)

    def _paste(self, *, time_only: bool, place_only: bool) -> None:
        """从剪贴板读，填进对应的那几栏。

        认不出来就明说「看不懂」而不是猜一个填进去 —— 填错的时间或位置会被原样写进照片。
        """
        parsed = _parse_clipboard(QGuiApplication.clipboard().text())
        if parsed is None:
            self._error.setText("剪贴板里没有可以用的内容。")
            return
        if not self._apply_parsed(parsed, time_only=time_only, place_only=place_only):
            return
        self._error.clear()
        self._fill_in_place()

    def _apply_parsed(self, parsed: ClipboardContent, *, time_only: bool, place_only: bool) -> bool:
        """把认出来的那几段写进框里；要的那一段认不出就整体不填（半截的状态最难收拾）。

        返回 ``False`` 表示剪贴板里没有用户要的那一段，原因已经说给用户听了。
        """
        wants_time = not place_only
        wants_place = not time_only
        if wants_time and not wants_place and parsed.capture is None:
            self._error.setText("剪贴板里没有可以用的拍摄时间。")
            return False
        if wants_place and not wants_time and parsed.coordinates is None and not parsed.names:
            self._error.setText("剪贴板里没有可以用的位置。")
            return False
        self._programmatic = True
        try:
            if wants_time and parsed.capture is not None:
                taken_at, offset = _split_capture(parsed.capture)
                self._taken_at.setText(_display_time(taken_at))
                self._offset.setText(offset or "")
            if wants_place:
                if parsed.coordinates is not None:
                    self._latitude.setText(_format(parsed.coordinates[0]))
                    self._longitude.setText(_format(parsed.coordinates[1]))
                if parsed.names and any(parsed.names):
                    self._city.setText(parsed.names[0] or "")
                    self._region.setText(parsed.names[1] or "")
                    self._country.setText(parsed.names[2] or "")
        finally:
            self._programmatic = False
        return True

    def _fill_in_place(self) -> None:
        """粘完之后刷一次候选列表。

        原来这里还会重跑一次跨层回填（「粘到一半补另一半」），那套已经去掉了
        （ADR 0039）：跨层填充只发生在用户按下的那两个按钮上，而按钮改的就是框本身，
        粘贴进来的坐标与地名已经是用户要的那一份，不该再被反查覆盖一遍。
        """
        self._seen_coordinates = self._coordinates()
        self._seen_names = self._names()
        self._refresh()

    def _on_accept(self) -> None:
        """先说清哪一处不对，不关对话框 —— 关掉了用户填的那一份就白填了。"""
        for problem in self._problems():
            if problem is not None:
                self._error.setText(problem)
                return
        self.accept()

    def _problems(self) -> tuple[str | None, str | None, str | None]:
        """拍摄时间、时区、坐标，各说一处不对的地方（``None`` 是没毛病）；按从上到下的顺序。"""
        return (
            _capture_time_problem(self._taken_at.text()),
            _offset_problem(self._offset.text()),
            _coordinate_problem(self._latitude.text(), self._longitude.text()),
        )

    def _names(self) -> tuple[str, str, str]:
        return (
            self._city.text().strip(),
            self._region.text().strip(),
            self._country.text().strip(),
        )

    def _coordinates(self) -> tuple[float | None, float | None]:
        return (_parse(self._latitude.text()), _parse(self._longitude.text()))

    def _capture(self) -> str | None:
        """两个框里的拍摄时间，归一成 ``2024:05:16 20:52:02 +08:00`` 那样的一串。

        看不懂就返回 ``None``；按保存时有 :meth:`_problems` 挡着，错的值写不进去。
        """
        text = self._taken_at.text().strip()
        if not text:
            return None
        taken_at = normalize_capture_time(text)
        if taken_at is None:
            return None
        offset = normalize_offset(self._offset.text().strip() or None)
        return f"{taken_at} {offset}" if offset else taken_at

    def _coordinates_emptied(self) -> bool:
        """两个坐标框都被清空了，而照片本来是有坐标的。"""
        had = self._state.place.coordinates() is not None
        return had and not self._latitude.text().strip() and not self._longitude.text().strip()

    def edit(self) -> MetadataEdit:
        """用户填完的那一份。空 = 照片里不记这一项（由 ``core`` 判定该不该删）。"""
        city, region, country = self._names()
        return MetadataEdit(
            capture=CaptureTimeEdit(
                taken_at=self._taken_at.text().strip() or None,
                offset=self._offset.text().strip() or None,
            ),
            place=PlaceEdit(
                city=city or None,
                region=region or None,
                country=country or None,
                latitude=_parse(self._latitude.text()),
                longitude=_parse(self._longitude.text()),
                # 坐标与地名全空就是「把位置整个清掉」—— 不需要单独的勾（ADR 0031）。
                clear_coordinates=self._coordinates_emptied(),
            ),
        )


@dataclass(frozen=True, slots=True)
class ClipboardContent:
    """剪贴板里认出来的那几段。认不出的段就是 ``None``，绝不猜。"""

    capture: str | None = None
    coordinates: tuple[float, float] | None = None
    names: tuple[str | None, str | None, str | None] | None = None

    @property
    def is_empty(self) -> bool:
        return self.capture is None and self.coordinates is None and not self.names


def _clipboard_text(
    capture: str | None,
    coordinates: tuple[float | None, float | None],
    names: tuple[str, str, str],
    *,
    time_only: bool = False,
    place_only: bool = False,
) -> str:
    """按固定格式拼出剪贴板里那几行，没填的段就没有那一行::

    2024-05-16 20:52:02 +08:00
    30.274150, 120.155070
    杭州, 浙江省, 中国
    """
    city, region, country = names
    lines: list[str] = []
    if not place_only and capture:
        lines.append(_display_capture(capture))
    if not time_only:
        if coordinates[0] is not None and coordinates[1] is not None:
            lines.append(
                f"{coordinates[0]:.{COORDINATE_DECIMALS}f}, "
                f"{coordinates[1]:.{COORDINATE_DECIMALS}f}"
            )
        if city or region or country:
            lines.append(f"{city}, {region}, {country}")
    return "\n".join(lines)


def _parse_clipboard(text: str) -> ClipboardContent | None:
    """剪贴板里那几行 → 认出来的那几段；一段都认不出就返回 ``None``。

    按形状认行，不按行号认：一行两个十进制度（半球标记、``°``、空格或逗号分隔都行）、
    一行 ``城市, 省州, 国家``、一行看得懂的拍摄时间。经度在前也认。度分秒
    （``30°16'27"N``）不认 —— 转换需要另一套解析，而猜错的坐标会被原样写进照片。
    """
    capture: str | None = None
    coordinates: tuple[float, float] | None = None
    names: tuple[str | None, str | None, str | None] | None = None
    for line in (item.strip() for item in text.splitlines()):
        if not line:
            continue
        if capture is None and (stamp := _normalize_capture_line(line)):
            capture = stamp
            continue
        if coordinates is None and (pair := _parse_coordinate_pair(line)):
            coordinates = pair
            continue
        if names is None and line.count(",") >= 2:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 3:
                names = (parts[0] or None, parts[1] or None, parts[2] or None)
    content = ClipboardContent(capture=capture, coordinates=coordinates, names=names)
    return None if content.is_empty else content


def _parse_coordinate_pair(line: str) -> tuple[float, float] | None:
    """一行里的两个十进制度。不是就是 ``None``，不做任何猜测。"""
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


def _split_capture(text: str) -> tuple[str, str | None]:
    """``2024:05:16 20:52:02 +08:00`` → ``(归一后的时间, 归一后的偏移)``。

    不是时间就返回 ``("", None)``。日期与时间之间是空格，所以按空白切开来是三个 token ——
    按两段切的话 ``DateTimeOriginal`` 的一半永远认不出来。偏移看不懂就当没有。
    """
    parts = text.split()
    if len(parts) < 2:
        return ("", None)
    taken_at = normalize_capture_time(f"{parts[0]} {parts[1]}")
    if taken_at is None:
        return ("", None)
    offset = normalize_offset(parts[2]) if len(parts) > 2 else None
    return (taken_at, offset)


def _normalize_capture_line(text: str) -> str | None:
    """一整行 → ``2024:05:16 20:52:02 +08:00``（都已归一）；不是时间就 ``None``。

    必须连偏移一起收：只留下钟点的话粘回去时 ``+08:00`` 就没了，而它是「这张照片属于哪个
    时区」那一半（ADR 0032）。
    """
    taken_at, offset = _split_capture(text)
    if not taken_at:
        return None
    return f"{taken_at} {offset}" if offset else taken_at


def _dashed(taken_at: str | None) -> str:
    """EXIF 的冒号写法 → 界面那种连字符写法；只换前两个冒号（第三个在「时:分:秒」中间）。"""
    return taken_at.replace(":", "-", 2) if taken_at else ""


def _display_capture(text: str) -> str:
    """归一后的时间 → 剪贴板与面板那种写法：``2024-05-16 20:52:02 +08:00``。"""
    taken_at, offset = _split_capture(text)
    dashed = _dashed(taken_at)
    return f"{dashed} {offset}" if dashed and offset else dashed


def _display_time(taken_at: str | None) -> str:
    """输入框里那个写法（连字符；EXIF 的冒号只存在于文件里）。"""
    return _dashed(taken_at)


_SEPARATORS: Final = re.compile(r"[\s,;、/，；]+")

_HEMISPHERE: Final = re.compile(r"[NSEWnsew北南东西°'\"″]+")


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


def _capture_time_problem(text: str) -> str | None:
    """拍摄时间那一栏有一处不对就说清楚。**不弹框**：对话框里那行红字就够。"""
    stripped = text.strip()
    if not stripped or normalize_capture_time(stripped) is not None:
        return None
    return f"拍摄时间看不懂：「{stripped}」。可以填成 2024-05-16 20:52:02，秒可以省。"


def _offset_problem(text: str) -> str | None:
    """时区那一栏有一处不对就说清楚。**空不算不对**（那是「照片里不记时区」）。"""
    stripped = text.strip()
    if not stripped or normalize_offset(stripped) is not None:
        return None
    return f"时区看不懂：「{stripped}」。可以填成 +08:00 或 -05:00；留空表示不记时区。"


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
    "FIELD_RADIUS",
    "LOOKUP_DELAY_MS",
    "ClipboardContent",
    "MetadataEditDialog",
    "dialog_style",
]
