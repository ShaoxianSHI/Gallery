"""编辑一张照片的元数据 —— 拍摄时间与位置（ADR 0030 / 0031 / 0032）。

**这个对话框一个文件都不碰**（与 ``title_bar`` 同一个规矩）：它只负责把用户填的东西读成
一个 :class:`~gallery.core.metadata.metadata_edit.MetadataEdit`，真正写盘的是
``write_ops`` → :meth:`gallery.core.library.facade.Library.write_metadata_edit`。按城市库查
候选走的是 ``core`` 的公开接口，也由它自己去读那份随包分发的只读文件。

## 两段，从上到下：拍摄时间 → 位置

拍摄时间在上面、位置在下面，与元数据面板里的分组顺序一致。**「编辑位置」因此改叫
「编辑元数据」**：编辑器从只管坐标与地名扩到也管拍摄时间，名字不改就成了一个与它实际
职责不符的说法。

## 拍摄时间是两个框，不是一个

EXIF 的 ``DateTimeOriginal`` 记的是**墙上时间** —— 拍照当地那个钟面上的几点几分，既不是
UTC 也没有换算；时区在**单独的可选字段** ``OffsetTimeOriginal`` 里，相机十有八九不写它
（见 :mod:`gallery.core.metadata.capture_time`）。于是两个框、两个独立的问题，其中最常见的
一个是「相机时区设错了」。

**照片没记时区时，时区那一栏就是空的** —— 绝不按本机时区替用户补一个：猜错的后果是排序
整体偏几个小时，比空着糟得多。留空 = 照片里不记这一项，与位置那一侧的空框同一套语义。

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

## 按钮是 3×3 的

```
复制        复制时间      复制位置
粘贴        粘贴时间      粘贴位置
清空位置    取消          保存
```

**按行读**：上面两行是「搬」——复制与粘贴，各有「全搬」「只搬时间」「只搬位置」三种；第三行
是三个动作。**三列等宽**，于是「占满一行」是布局算出来的，不是估出来的。

有了两段可搬的内容，「复制 / 粘贴」就有了「搬全部」与「搬一段」的分别：全搬是为了把一张
照片的地点与时间整个交给另一张，只搬一段是为了「这张的时间对了，位置我要另填」。

「清空位置」**只清位置那五栏** —— 拍摄时间不该被一个泛指的按钮顺手删掉；想清掉它，把上面
那两个框清空就是（与位置同一套语义）。

## 剪贴板格式：三行，人也能读

复制出去的形状::

    2024-05-16 20:52:02 +08:00
    30.274150, 120.155070
    杭州, 浙江省, 中国

**没有的段就少那一行**，于是「苏州,,中国」与「2024-05-16 20:52:02」各自都能单独粘回去。

**粘贴时按形状认，不按行号认**：一行能解析成坐标就是坐标，能解析成三层地名就是地名，
能归一成拍摄时间就是时间。于是「粘进来坐标就反查出地点，粘进来地名就正查出坐标，
粘进来时间就填进上面两个框」—— 三段各管各的，互不冒充。

**不用制表符、不加标题行**：多一种分隔符就多一种要认的写法，而这一组行的用途是
「人看得懂 + 我们认得出」，不是变成一个要长期维护的交换格式。

## 两个方向都实时回填

| 用户动了 | 立刻发生 |
|---|---|
| 城市 / 省 / 国家 | 在城市库里按名字正查 → 把那个地点的**坐标写进下面两个框** |
| 纬度 / 经度 | 按坐标反查 → 把**城市 / 省 / 国家写进上面三个框** |

「实时」是防抖 250 毫秒（比人手打字快，又不至于每敲一个字就动一次城市库）。

**回填只覆盖另一侧**，且程序自己写进去的框不再触发连锁 —— 靠一个 ``_programmatic``
标志挡住（否则「地名 → 坐标 → 地名」会自己打起来）。没查到的**一个字都不填**：查不到就
是查不到，绝不编一个地名或一个坐标出来。

界面沿用 ``ui.theme`` 那一份底色与文字色（ADR 0029 起的规矩），系统换深浅色时自己跟。
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
from gallery.ui.theme import AppTheme, rgba

#: **少于这么几个字就不查**。
#:
#: 自带的城市库里有「苏」这样一个真实条目（安徽某个被折叠进市里的区，见
#: ``scripts/build_places_db.py`` 折叠区划那一步）。用户敲下第一个字就恰好命中它的话，
#: 屏幕上会立刻跳出「中国 · 安徽省」和一串坐标 —— 那是数据里的碎片，不是用户要填的地方。
MIN_LOOKUP_LENGTH: Final = 2

#: 打字之后多久去查一次。**250 毫秒**：比人手打字快，又不至于每敲一个字
#: 就去动一次城市库（那份表有 2 万多条）。
LOOKUP_DELAY_MS: Final = 250

#: 坐标框里显示几位小数（与写回时的精度一致）。
COORDINATE_DECIMALS: Final = 6

#: 每个输入框的最小宽度。**不是为了好看**：固定尺寸的对话框宽度由内容算出来，
#: 而「Gironde」「United States」「120.155070」这些内容算出来的宽度比能读它窄得多。
#: 九个键（4 个字的那几个）也靠它才摆得下。
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
    QLabel#metadataEditTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#metadataEditError {{ color: {rgba(app_theme.error)}; font-size: 11px; }}
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
        #: 补全窗口里那一排候选对应的地点（与 ``_completer`` 的模型同序）。
        self._candidates: list[Place] = []
        #: 程序自己往框里写字时为真 —— 那些 setter 会触发 ``textChanged``，
        #: 没有这道挡，「地名 → 坐标 → 地名」会自己打起来。
        self._programmatic = False
        #: 「已复制」那个提示的定时器。**一个就够**：三个复制键轮流亮，一起复原。
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

    # ---- 装配 --------------------------------------------------------------

    def _build(self, state: MetadataState, photo_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        # **不能改变大小**：用户拖一下窗口边就该是同样大小，而不是把提示框拉成一整屏。
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        title = QLabel(f"编辑「{photo_name}」的元数据" if photo_name else "编辑元数据")
        title.setObjectName("metadataEditTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # 定时器**先建**：下面的 ``setText`` 会触发 ``textChanged``，
        # 而那会来启动它 —— 顺序反了就是「打开对话框时崩一次」。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

        # ---- 拍摄时间（ADR 0032）：最上面两栏 ------------------------------
        # **下面没有小字提示**：框上的标签已经写着「拍摄时间」「时区」，而这一栏填什么
        # 本来就是访达「显示简介」那一种用法 —— 用户改自己的照片，不需要被先教育一遍。
        # 墙上时间不是 UTC 这件事由校验说话：填了 `2024-05-16 20:52:02 +09:00` 它照样收。
        self._taken_at = QLineEdit(_display_time(state.capture.taken_at))
        form.addRow("拍摄时间", self._taken_at)
        self._offset = QLineEdit(state.capture.offset or "")
        form.addRow("时区", self._offset)

        # **不留占位文字**：左边那一列标签已经写着「国家」「省 / 州」「城市」「纬度」「经度」，
        # 空框里再浮一遍同样的字只是把它说两遍。
        self._country = QLineEdit(state.place.country or "")
        self._country.textChanged.connect(self._on_names_changed)
        form.addRow("国家", self._country)

        self._region = QLineEdit(state.place.region or "")
        self._region.textChanged.connect(self._on_names_changed)
        form.addRow("省 / 州", self._region)

        # **普通的输入框**，不是下拉：这一栏和上面两栏一样是「填一个地方」，不是
        # 「从清单里挑一个」。下拉那个朝下的小箭头会让人以为清单是封闭的 —— 而城市库
        # 里没有的名字（用户自己的门牌、景区）恰恰是最该能填进去的那些。
        # 补全仍然在：``QLineEdit.setCompleter`` 会照常弹候选，选中后由 ``_on_picked``
        # 把整条「城市 · 省州 · 国家」换回**光标处那一个城市名**。
        self._city = QLineEdit(state.place.city or "")
        self._city.textChanged.connect(self._on_names_changed)
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        #: 候选的模型**建一次**（理由见 :meth:`_refresh_candidates`）。
        self._candidate_model = QStringListModel(self)
        self._city.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_picked)
        form.addRow("城市", self._city)

        coordinates = state.place.coordinates()
        self._latitude = QLineEdit(_format(coordinates[0] if coordinates else None))
        self._latitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("纬度", self._latitude)
        self._longitude = QLineEdit(_format(coordinates[1] if coordinates else None))
        self._longitude.textChanged.connect(self._on_coordinates_changed)
        form.addRow("经度", self._longitude)
        layout.addLayout(form)

        # **报错那一行在按钮上面**：报错说的是上面那几个框里的哪一个不对，放在那儿才找得
        # 回去；压在「保存」下面时，那块空着的两行垫在最底下，看起来像一大截多余的边。
        self._error = QLabel()
        self._error.setObjectName("metadataEditError")
        self._error.setWordWrap(True)
        self._error.setFixedHeight(self.fontMetrics().height() * ERROR_LINES)
        layout.addWidget(self._error)

        layout.addLayout(self._build_buttons())

        # **⌘W 关闭**（`StandardKey.Close` 在 macOS 上就是它）。不挂这一条的话，⌘W 会
        # 一路走到主窗口 —— 在一个模态对话框里关掉整个窗口，那不是用户按键时想做的事。
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)

        # 固定尺寸是按 sizeHint 定的，而 sizeHint 由内容算出来 —— 242px 宽的编辑器
        # 连「United States」都放不下。给每一栏一个下限，宽度就有一个说得过去的下限了。
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

    def _build_buttons(self) -> QGridLayout:
        """九个键，3×3 等宽。**按行读**：搬、搬、动作。"""
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        # 第一行与第二行是「搬」：全搬、只搬时间、只搬位置。
        # **不挂悬浮提示**：键上的字面意思已经就是它们要做的事。
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

        # 第三行三个动作：**清空只清位置**，拍摄时间不该被它顺手删掉。
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
        # 三列等宽，于是「占满一行」是布局算出来的，不是估出来的。
        for column in range(3):
            grid.setColumnStretch(column, 1)
        return grid

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

        **模型只建一次**：这一格每 250 毫秒就会走一遍（防抖的那一头），
        而每次新建一个 ``QStringListModel`` 都要连着重新接一遍
        ``QCompleter`` 的那一串内部连接 —— 候选列表本身不变，变的只是它背后的那几行。
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
                if not self._country.text().strip() and place.country:
                    self._country.setText(place.country)
            finally:
                self._programmatic = False
            self._seen_names = self._names()
            self._fill_from_names(self._names())
            return

    # ---- 清空 ---------------------------------------------------------------

    def _on_clear(self) -> None:
        """把**位置**那一栏全清空 —— 那就是「把位置整个删掉」。

        **不需要一个勾选框**（ADR 0031）：勾选框得先去勾一下才生效，摆在一堆键里
        反而更像一个动作，而「全清空」本来就是编辑器里唯一表达得出「全删」的形状。
        这里只清**框**，仍然要按保存才写盘。

        **拍摄时间那两栏不动**：键名叫「清空位置」，而删掉一张照片的拍摄时间是另一件
        更大的事（ADR 0032）。想清掉它，把那两个框清空就是。
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

    # ---- 复制 / 粘贴 ---------------------------------------------------------
    #
    # 三种搬法共用同一份剪贴板：**全搬 / 只搬时间 / 只搬位置**。格式与解析见模块文档。

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

        **格式是人也能读的**（时间那一行、坐标那一行、地名那一行），要粘进别的地图软件
        时坐标那一行直接可用。**三段里没填的就不写那一行** —— 往剪贴板里塞空字符串，
        粘回来时就得专门去分辨那是「没有」还是「空着」。
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

        **认几种常见写法**（半球标记、`°`、空格 / 逗号 / 分号分隔、经度在前、按形状认行），
        认不出来就明说「看不懂」而不是猜一个填进去 —— 填错的时间或位置会被原样写进照片。
        填完照常走一遍回填：粘进来坐标就反查出地点，粘进来地名就正查出坐标。
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
        """把认出来的那几段写进框里。**要的那一段认不出就整体不填**（半截的状态最难收拾）。

        返回 ``False`` 表示剪贴板里没有用户要的那一段，已经把原因说给用户听了。
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
        """粘完之后决定要不要重跑回填 —— 与「粘到一半补另一半」是同一个道理。"""
        coordinates, names = self._coordinates(), self._names()
        if coordinates != (None, None) and any(names):
            self._seen_coordinates, self._seen_names = coordinates, names
        else:
            if coordinates != (None, None):
                self._seen_coordinates = (None, None)  # 让下面重跑这一侧的反查
            if any(names):
                self._seen_names = ("", "", "")  # 同上，让它重跑正查
        self._refresh()

    # ---- 校验与取值 --------------------------------------------------------

    def _on_accept(self) -> None:
        """**先说清哪一处不对，不关对话框** —— 关掉了用户填的那一份就白填了。"""
        for problem in self._problems():
            if problem is not None:
                self._error.setText(problem)
                return
        self.accept()

    def _problems(self) -> tuple[str | None, str | None, str | None]:
        """拍摄时间、时区、坐标，各说一处不对的地方（``None`` 是没毛病）。

        **按从上到下的顺序**：用户是从上往下填的，先报上面那处，他改完再看下面那条。
        """
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

        **看不懂就返回 ``None``**：那正是「这里没有可用的时间」，而把原文塞给剪贴板只会
        让下一次粘贴再认不出一次。按保存时有 :meth:`_problems` 挡着，不会有错的值被写进去。
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
        """用户填完的那一份。**空 = 照片里不记这一项**（由 ``core`` 判定该不该删）。"""
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
                # **坐标与地名全空**就是「把位置整个清掉」—— 不需要单独的勾（ADR 0031）。
                clear_coordinates=self._coordinates_emptied(),
            ),
        )


# ---- 剪贴板格式 ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClipboardContent:
    """剪贴板里认出来的那几段。**认不出的段就是 ``None``，绝不猜。**"""

    #: 归一后的拍摄时间（``2024:05:16 20:52:02``，带得上偏移就跟在后面）。
    capture: str | None = None
    coordinates: tuple[float, float] | None = None
    #: 三层地名（城市、省州、国家）。
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
    """按模块文档里的格式拼出剪贴板里那几行。**要的那一段是空的就没有内容。**"""
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
    """剪贴板里那几行 → 认出来的那几段；**一段都认不出就返回 ``None``**。

    **按形状认行，不按行号认**：一行能解析成坐标就是坐标，能解析成三层地名就是地名，
    能归一成拍摄时间就是时间。于是少写一行（只复制了时间、只复制了位置）照样认得出来，
    而顺序换了也不影响 —— 多一种要维护的写法就少一种认错的余地。

    **认得宽松，但只认一种「形状」**：一行两个十进制度（半球标记、``°``、空格或逗号
    分隔都行），一行 ``城市, 省州, 国家``，一行看得懂的拍摄时间。**经度在前也认** ——
    用范围判（纬度不超过 90、经度不超过 180）交换一次，两数都合法就按「纬度在前」收。

    **度分秒（``30°16'27"N``）不认**：那一行会被解析成三个数，于是这一行直接不算坐标。
    把度分秒转成十进制度需要另一套解析，而我们只往照片里写十进制度 —— 猜错的坐标会
    被原样写进照片，比「看不懂」糟糕得多。
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


def _split_capture(text: str) -> tuple[str, str | None]:
    """``2024:05:16 20:52:02 +08:00`` → ``(归一后的时间, 归一后的偏移)``。

    不是时间就返回 ``("", None)``。**日期与时间之间是空格**，所以按空白切开来是三个
    token（日期、时间、偏移），而不是两个 —— 早先按两段切过，结果 `DateTimeOriginal`
    的一半永远认不出来。

    偏移那截看不懂就当没有（不是就整个不认）：这一行已经足够认得出是拍摄时间了。
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

    **必须连偏移一起收**：只留下钟点的话，粘回去时那个 ``+08:00` 就没了 —— 而它恰恰是
    「这张照片属于哪个时区」那一半，是这一段存在的一半理由（ADR 0032 决策 2）。
    """
    taken_at, offset = _split_capture(text)
    if not taken_at:
        return None
    return f"{taken_at} {offset}" if offset else taken_at


def _dashed(taken_at: str | None) -> str:
    """EXIF 的冒号写法 → 界面那种连字符写法（``2024-05-16 20:52:02``）。

    **只换前两个冒号**：``2024:05:16 20:52:02`` 里的第三个冒号在「时:分:秒」中间，
    一并换掉就成了 ``20-52-02``。``count=2`` 就是这个意思。

    这一份原本在 ``capture_time.CaptureTime`` 的 ``display`` 方法、``_display_capture``、
    ``_display_time`` 里各写一遍。核心层那个方法没有调用点（界面上摆的是这个对话框里的
    写法），删掉之后剩下的两处就在这里共用这一份。
    """
    return taken_at.replace(":", "-", 2) if taken_at else ""


def _display_capture(text: str) -> str:
    """归一后的时间 → 剪贴板与面板那种写法：``2024-05-16 20:52:02 +08:00``。"""
    taken_at, offset = _split_capture(text)
    dashed = _dashed(taken_at)
    return f"{dashed} {offset}" if dashed and offset else dashed


def _display_time(taken_at: str | None) -> str:
    """输入框里那个写法（连字符；EXIF 的冒号只存在于文件里）。"""
    return _dashed(taken_at)


#: 坐标行里分隔两个数的那些字符。
_SEPARATORS: Final = re.compile(r"[\s,;、/，；]+")

#: 半球标记与度符号，解析时去掉。
_HEMISPHERE: Final = re.compile(r"[NSEWnsew北南东西°'\"″]+")


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
    "LOOKUP_DELAY_MS",
    "ClipboardContent",
    "MetadataEditDialog",
    "dialog_style",
]
