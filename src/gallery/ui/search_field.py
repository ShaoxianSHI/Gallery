"""窗口标题栏右上角那个胶囊搜索框（ADR 0026 决策 8）。

**它与 :mod:`gallery.ui.title_bar` 是兄弟，不是子控件。** ``TitleBar`` 不在编辑时带着
``WA_TransparentForMouseEvents``（那是为了让标题栏那一段仍然能拖窗口，见 ADR 0025 决策 5），
而那个标志**会连子控件一起罩住** —— 塞进去的话放大镜那一块收不到点击。于是两者都由
``MainWindow._layout_title_bar`` 一起摆。

它只回答三件事：**用户回车提交了什么**、**用户按了 Esc**、**用户是不是正在输入**。
条件怎么解析、结果怎么算、界面怎么重排，一概不在这里 —— 与 ``title_bar`` 同一个取向：
**它一个文件都不碰，只发信号**（红线 1）。

``#`` 后面那份候选列表来自 :mod:`gallery.core.library.search`（纯数据，不碰文件系统），
因此候选表与「界面上写的是什么、查的是什么」永远是同一份。
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QCursor,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcutEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from gallery.core.library.search import SearchField as Field
from gallery.core.library.search import (
    partial_field,
    suggestions,
)
from gallery.ui import theme
from gallery.ui.menu_style import (
    POPUP_FONT_PX,
    POPUP_LIST_INSET,
    POPUP_LIST_ITEM_PAD,
    popup_list_style,
)
from gallery.ui.rename_edit import field_background
from gallery.ui.theme import AppTheme, rgba

#: 胶囊多宽。够放下「搜索照片…」与十来个字，又不至于从标题正中伸到交通灯那边。
WIDTH = 208

#: 胶囊多高。比标题栏（见 ``title_bar.HEIGHT``）矮一点，上下留白，不贴着窗口顶边。
HEIGHT = 22

#: 胶囊离窗口右边多远。与左边交通灯那一段（78 点）不对称是刻意的：那边系统自己画着
#: 三个按钮，胶囊是后来加的，靠边会显得被挤出去，于是留宽一点。
#:
#: **这一格是「右上角那一块」的右缘**，面包屑右端那几枚条件胶囊要与它对齐（它们是同
#: 一列东西，只是分两行摆着），于是它同时被 ``BreadcrumbBar.set_right_inset`` 用到 ——
#: 换句话说「对齐的那个数」只有这一个，别在面包屑那一侧另写一份。
MARGIN_RIGHT = 20

#: 胶囊比标题栏那一条的中线再往下挪多少（点）。
#:
#: **不是为了对齐才有的那个数**：同一扇窗口、同一个中文标题，截图量下来胶囊中线 16.75 点、
#: 标题墨迹中线 17.00 点 —— 两者本来就齐，差的那 0.25 点肉眼分辨不出。
#:
#: 这一格是随后看着截图调出来的：挪 0 点齐、挪 2 点就比标题低 1.75 点（一眼看得出来偏），
#: 于是取中间这一格。**别再往下加** —— 2 点已经实测过是过头的那一头。
TEXT_ALIGN_DROP = 1

#: 放大镜直径（点）。
ICON_EDGE = 13

#: 画之前往里收多少（点）。**不是可有可无的余量**：笔宽画在路径两侧，收少了就会被
#: 画布裁掉（见 :func:`magnifier`）。
ICON_INSET = 1.6

#: 放大镜那两笔的粗细（点）。
_ICON_STROKE = 1.4

#: 输入区里那一列的字号，与标题文字（13 点）差一档 —— 它是次要控件。
#: **与弹出列表、弹出菜单共用同一个数**（``menu_style.POPUP_FONT_PX``）：那三样东西
#: 在用户眼里是同一种排版，各存一份的话早晚在一个主题下差一档。
_FONT_PX = POPUP_FONT_PX

#: 候选那一行在行内边距为 0 时，文字**笔尖**离视口左沿还有多远（点）。
#:
#: **不是 0**：Qt 画 ``::item`` 的文字之前自己还要让开一小段，QSS 的 ``padding`` 是在
#: 这一段**之后**才叠加上去的（实测 1 点）。
#:
#: 它跟着 Qt 版本与系统样式变，所以只能量过写死 —— 写错的后果由
#: ``test_the_candidate_hash_lines_up_with_the_typed_one`` 那条像素对拍兜着（它量的是
#: 两个字真正画在哪，不是控件在哪）。
_ITEM_TEXT_BASELINE = 1

#: 候选列表最窄还能是几多（点）。左沿是「跟着输入框那一侧让开」的，让开得太过就成了一条
#: 看不出是什么的竖条 —— 这一格是那条下限，**与胶囊离窗口右边多远无关**（原因见
#: :meth:`SearchField._fill_popup` 里那段话）。够放下最长的那几项（「# 像素尺寸」）。
_POPUP_MIN_WIDTH = 120


class SearchEdit(QLineEdit):
    """胶囊里那个输入框。

    **键盘这一层与 :class:`~gallery.ui.rename_edit.RenameEdit` 一样自己接管**：
    窗口把 ``Esc`` 绑成了「上一级」（以及搜索条件上的「撤销一条」），
    不抢过来的话，用户正在搜索时按 Esc 会连带着把这一层退出去。
    """

    def __init__(self, owner: SearchField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setObjectName("searchEdit")
        self.setPlaceholderText("搜索照片…")
        self.setFrame(False)
        # **只有点它才进来**：默认的 ``StrongFocus`` 会让窗口打开时把光标送进这个框，
        # 于是方向键在网格上立刻失灵（用户得先点一下照片区才找得回来）。要它进来
        # 有一条明路：⌘F（``focus_search``）。访达的搜索框也是这个脾气。
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---- 事件 --------------------------------------------------------------

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().focusInEvent(event)
        self._owner.focus_gained()

    def event(self, event: QEvent) -> bool:
        # 见 RenameEdit.event：先把 Esc 从「上一级」那个 QAction 手里抢过来。
        if isinstance(event, QShortcutEvent) and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._owner.move_selection(
            1 if key == Qt.Key.Key_Down else -1
        ):
            # 候选列表开着时 ↑↓ 是「换选」；没开着就让输入框自己走历史记录。
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            if self._owner.popup_visible():
                # 回车 / Tab 在候选列表打开时是「补全选中那个字段」，不是「提交」。
                self._owner.complete()
                event.accept()
                return
            if key != Qt.Key.Key_Tab:
                self._owner.submit()
                event.accept()
                return
        if key == Qt.Key.Key_Escape:
            self._owner.dismiss()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802 - Qt 定的
        super().focusOutEvent(event)
        self._owner.edit_ended()


def search_field_style(app_theme: AppTheme, *, focused: bool, radius: int = 0) -> str:
    """胶囊长什么样。

    底色与改名的输入框**同一个函数派生**（见 ``rename_edit.field_background``）：
    它也是一块输入面，两处各派生一次早晚会差一档，而那时没人说得清是哪一档。

    聚焦时边框换成调色板的 ``Highlight`` —— 与系统、与网格里那一处选区永远一致，
    不挑一个「看着顺手的蓝」。

    ``radius`` 是**圆角半径，由摆它的那一处按实际高度给过来**（默认 = 一半高度，
    也就是正圆）。写死不成立：胶囊的高度跟着窗口让出来的那一条走（实测安全区不足
    28 点时是 18），而 Qt 样式表里半径一旦超过高度的一半就整个不画 —— 胶囊于是变成
    一个方框（实测：18 高配 11 半径，四角是方的）。
    """
    border = "palette(highlight)" if focused else rgba(app_theme.text_at(0.28))
    corner = max(0, radius or HEIGHT // 2)
    return f"""
    QWidget#searchField {{
        background: {rgba(field_background(app_theme))};
        border: 1px solid {border};
        border-radius: {corner}px;
    }}
    QLabel#searchIcon {{ background: transparent; }}
    QLineEdit#searchEdit {{
        background: transparent;
        border: none;
        color: {rgba(app_theme.text)};
        font-size: {_FONT_PX}px;
        selection-background-color: palette(highlight);
        selection-color: palette(highlighted-text);
    }}
    """


def popup_style(list_widget: QTreeWidget, *, item_padding: int = POPUP_LIST_ITEM_PAD) -> str:
    """候选列表长什么样 —— **样式表由 :mod:`gallery.ui.menu_style` 生成**。

    颜色不在这儿定：那是「菜单怎么长什么样」那一层的活（ADR 0024），而用户要的是
    候选列表与右键菜单**看起来是同一种东西**。两份各自派生一遍，早晚差一档。

    这里只补两样：列表自己的字号（菜单那一档偏大，列表窄）与行内边距
    （:paramref:`item_padding` —— 它要让列表里那个 ``#`` 与输入框里那个对齐，
    见 :meth:`SearchField._item_padding`）。
    """
    return (
        f"{popup_list_style(list_widget, item_padding=item_padding)}\n"
        f"QTreeWidget#searchPopup {{ font-size: {_FONT_PX}px; }}"
    )


def magnifier(app_theme: AppTheme, ratio: float) -> QPixmap:
    """那个放大镜：**现画的**，不往资源里塞一张图。

    一个圆加一根柄，两条路径 —— 换成图标文件就要多一份要打包、要适配深浅两档的资源，
    而它永远是同一个形状。

    **坐标一律用逻辑像素**，只有画布尺寸乘上 :paramref:`ratio`。``QPainter`` 会按设备
    像素比缩放它收到的那套坐标，于是拿设备像素画上去，在 Retina 上就是放大了一倍 ——
    画布只有 13 点、图形却按 26 点画，结果只剩手柄的尖落在画布里（实测：不透明像素
    只剩右下角一个）。

    这与 :data:`ICON_INSET` 是两件事：**收圈**挡住笔宽被裁，**用逻辑坐标**挡住整张图
    被放大。
    """
    pixmap = QPixmap(round(ICON_EDGE * ratio), round(ICON_EDGE * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(app_theme.text_at(0.6))
    pen.setWidthF(_ICON_STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    usable = ICON_EDGE - 2 * ICON_INSET
    radius = usable * 0.33
    center = ICON_INSET + usable * 0.4
    # 用 ``QRectF`` 那个重载：``drawEllipse(QPointF, w, h)`` 在 PySide6 里绑到的
    # 不是「左上角 + 宽高」（实测圆被画到 0..8 而不是 2..9）。
    painter.drawEllipse(QRectF(center - radius, center - radius, radius * 2, radius * 2))
    # 手柄从圆环的 45 度方向出发，走到那个内缩过的角上 —— 与圆环连得上，也不顶边。
    painter.drawLine(
        QPointF(center + radius * 0.72, center + radius * 0.72),
        QPointF(ICON_EDGE - ICON_INSET, ICON_EDGE - ICON_INSET),
    )
    painter.end()
    return pixmap


class CandidateList(QTreeWidget):
    """候选列表：**鼠标移到哪一行，那一行就高亮**（菜单也是这么做的）。

    为什么自己接一次移动而不是只靠样式表的 ``::item:hover``：

    - 挂了样式表之后 Qt 不再按 ``State_MouseOver`` 自己去挑高亮色（与
      :func:`gallery.ui.menu_style.style_sheet` 里那条菜单的注释是同一件事），
      而「当前项」那一份高亮它是照画的；
    - 把悬停的那一行设成**当前项**，顺带让鼠标与键盘说同一句话：点它就补全它
      （``itemClicked`` 取的是当前项），不必有两套「选中」。

    **装在视口上而不是覆写 ``mouseMoveEvent``**：滚动区域的视口是 Qt 自己的控件，
    落在它身上的事件不会再回到视图的 ``mouseMoveEvent`` 上（实测那次移动完全没反应）。
    要接就得接在视口上（:meth:`eventFilter`），这是滚动区域里唯一稳的地方。

    **不抢焦点**：整份列表 ``NoFocus``，按键都留给输入框（见 :class:`SearchField`）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 鼠标跟踪：不开的话根本收不到 ``MouseMove``，那一次悬停就没有反应。
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt 定的
        """视口上的移动 = 悬停即当前项。鼠标不在任何一行上时**保持原样**。"""
        if watched is self.viewport() and event.type() == QEvent.Type.MouseMove:
            move = cast(QMouseEvent, event)
            item = self.itemAt(move.position().toPoint())
            if item is not None and self.currentItem() is not item:
                self.setCurrentItem(item)
        return super().eventFilter(watched, event)


class SearchField(QWidget):
    """标题栏右上角那个胶囊搜索框。

    :meth:`submit` 把清空输入框这件事一并做了（用户确认之后输入框就该是干净的），
    外面只需要接 :attr:`submitted` 那个信号。
    """

    #: 用户回车确认了（**清空输入框之后**才发，信号里带着原文）。
    submitted = Signal(str)
    #: 用户按了 Esc，且输入框里已经没有字可清 —— 外面该处理的是「撤销一条筛选条件」
    #: 或「返回上一层」，这一层不替它决定。
    dismissed = Signal()
    #: 开始 / 结束输入。别的命令要在输入期间让开（与改名的两个输入框同一件事）。
    editing_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchField")
        # **只定死宽度**：它要贴着窗口右边，宽度是设计的一部分。高度交给摆它的那一处
        # （``MainWindow._layout_title_bar``）—— ``setFixedSize`` 会把高度也钉死，
        # 于是窗口给不出 :data:`HEIGHT` 点时 ``setGeometry`` 改不动，胶囊就压到了
        # 照片区顶边上（守护测试 ``test_innermost_layer_has_no_border`` 量到了那一块）。
        self.setFixedWidth(WIDTH)
        self.resize(WIDTH, HEIGHT)
        # **没有它，样式表里的底色与边框一条都不会画**：Qt 只给 QFrame 那一族自动画
        # 样式背景，普通 QWidget 得自己声明（实测：胶囊整个透明，窗口底色上什么都没有）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._icon = QLabel(self)
        self._icon.setObjectName("searchIcon")
        self._icon.setFixedWidth(ICON_EDGE + 2)
        self._edit = SearchEdit(self, self)
        self._edit.textChanged.connect(self._on_text_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)
        layout.addWidget(self._icon)
        layout.addWidget(self._edit, 1)

        # 候选列表挂在**窗口**上（是胶囊的兄弟，不是子控件），也**不是** ``Qt.Popup`` 窗口。
        #
        # 两条理由都是量出来的：
        #
        # - 挂在胶囊里会被**裁掉**：Qt 只把子控件画在父矩形之内，而这份列表恰恰要伸到
        #   胶囊下面去（实测：整块不画）。
        # - 用弹出窗口则会在 ``show()`` 那一刻**把焦点从输入框抢走**，于是「列表开着」
        #   与「输入框有焦点」没法同时成立，↑↓ 与回车都得绕一大圈（Qt 自己的
        #   ``QCompleter`` 为此加了 ``WA_ShowWithoutActivating``，实测照样被
        #   ``focusOutEvent`` 打断）。挂在窗口上的普通子控件不吃焦点，所有按键留在输入框里。
        self._host = self if parent is None else parent
        self._popup = CandidateList(self._host)
        self._popup.setObjectName("searchPopup")
        self._popup.setFrameShape(QFrame.Shape.NoFrame)
        self._popup.setRootIsDecorated(False)
        self._popup.setHeaderHidden(True)
        self._popup.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._popup.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._popup.setUniformRowHeights(True)
        self._popup.itemClicked.connect(lambda _item, _column: self.complete())
        self._popup.hide()

        self._focused = False
        #: 正在补全字段（补全那一瞬不许把列表重新弹起来，见 :meth:`complete`）。
        self._completing = False
        #: 上一次**真的**下发过的样式表是哪一份（见 :meth:`_apply_theme`）。
        #: 起手是「没有」，所以构造时那一次一定会下发。
        self._applied_theme: AppTheme | None = None
        self._applied_radius = -1
        self._applied_focused = False
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    # ---- 对外 --------------------------------------------------------------

    def focus_search(self) -> None:
        """聚焦并把光标放进胶囊（⌘F）。"""
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()

    def load(self, text: str) -> None:
        """把一条已有的筛选条件**放回输入框**接着改（面包屑上点一枚胶囊时）。

        **整串选中**：用户多半是改值（``#相机 Canon`` → ``#相机 Sony``），选中一按就换，
        不用先把光标挪到末尾去删。什么也不改直接按回车，那就是「没改」。
        """
        self._close_popup()
        self._edit.setText(text)
        self.focus_search()

    def is_editing(self) -> bool:
        return self._edit.hasFocus()

    def text(self) -> str:
        return self._edit.text()

    def submit(self) -> None:
        """回车：把原文交出去，然后**清空输入框**（用户确认之后它就该是干净的）。"""
        raw = self._edit.text()
        self._close_popup()
        self._edit.clear()
        self.submitted.emit(raw)

    def dismiss(self) -> None:
        """Esc：有关联的候选列表就先关它，其次清空输入框，都没什么可清了就交出去。"""
        if self.popup_visible():
            self._close_popup()
            return
        if self._edit.text():
            self._edit.clear()
            return
        self.dismissed.emit()

    def stop_edit(self) -> None:
        """外部强制收起（换层、关窗），不发任何信号。"""
        self._close_popup()
        self._edit.clear()

    def popup_visible(self) -> bool:
        return self._popup.isVisible()

    def visible_fields(self) -> list[str]:
        """候选列表此刻列着哪些字段名（测试与「没有候选就别弹」的判据用）。"""
        return [item.text(0) for item in self._candidate_items()]

    # ---- 候选列表 ----------------------------------------------------------

    def move_selection(self, delta: int) -> bool:
        """候选列表打开时用 ↑↓ 换选；没打开就交回输入框（那里是历史记录）。"""
        if not self.popup_visible():
            return False
        items = self._candidate_items()
        if not items:
            return True
        current = self._popup.currentItem()
        index = items.index(current) if current in items else 0
        self._popup.setCurrentItem(items[max(0, min(len(items) - 1, index + delta))])
        return True

    def complete(self) -> None:
        """把选中那个字段补成 ``#相机 ``（**带尾随空格**），接着就能敲值。

        列表里那一列本来就写着 ``#``，所以补全时**不要再补一个**（补成 ``##相机``
        就再也认不出字段了，回落到按文件名搜 —— 用户看见的是一个自己不认的东西）。

        **补完之后列表要收起来**（靠 :attr:`_completing` 这个闸）：留着的话列表里只剩
        刚补上的那一个字段，而回车此时是「再补全一次」—— 用户按到天荒地老也提交不出去。

        **补完把焦点还给输入框**：点着候选补全的那一下会顺手把输入框的焦点带走，
        而用户接下来就是要接着敲值。
        """
        item = self._popup.currentItem()
        if item is None:
            return
        self._close_popup()
        self._completing = True
        try:
            self._edit.setText(f"{item.text(0)} ")
            self._edit.setCursorPosition(len(self._edit.text()))
        finally:
            self._completing = False
        # **只给焦点，不全选**：全选的话用户接着敲的那个字会顶掉刚补上的字段名。
        self._edit.setFocus(Qt.FocusReason.MouseFocusReason)

    def _candidate_items(self) -> list[QTreeWidgetItem]:
        return [self._popup.topLevelItem(row) for row in range(self._popup.topLevelItemCount())]

    # ---- 内部 --------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        """``#`` 后面还没敲空格时开候选列表；一旦进入「值」那一步就收起来。

        **判据是「有没有敲空格」而不是「这一段认不认得出字段」**：用户敲「省」那半个
        字段名时该看见「省 / 州」被筛出来，而不是一个空列表 —— 要的是边打边筛。
        认不出来的字段名按文件名搜，那是**回车之后**的事（见 ``search.parse_input``）。
        """
        if self._completing:
            # 刚补全过：那个字段已经定了，值还没敲 —— 列表这时开着只会挡住回车。
            self._close_popup()
            return
        query = partial_field(text)
        if not query and text.strip() != "#":
            self._close_popup()
            return
        self._fill_popup(suggestions(query))

    def _fill_popup(self, fields: list[Field]) -> None:
        """列一遍候选。**没有候选就不弹**：一个空框只是把界面弄花，不会帮上任何人。"""
        self._popup.clear()
        for field in fields:
            # **只一列**：这份列表原本还带一句灰字提示（「5MB 或 >10MB」），但那一列
            # 从来没被画出来过（``QTreeWidgetItem`` 带着两列的值构造、再 ``add``，
            # 树却不会长出第 2 列），界面上一直只是每行右边一片空白。去掉更干净 ——
            # 核心里那份 ``hint`` 仍然留着，错误横幅还要用（见 ``MainWindow``）。
            self._popup.addTopLevelItem(QTreeWidgetItem([f"#{field.label}"]))
        if not fields:
            self._close_popup()
            return
        # 位置换算到**窗口**坐标（列表是胶囊的兄弟，见 __init__ 那段）。
        anchor = self.mapTo(self._host, QPoint(0, self.height() + 2))
        # 高**按行高算**，不按「行数 × 一个写死的数」：写死的那个数（曾经是 26）
        # 并不是这一档字号下真正的行高（实测 23），于是最下面多出一截空白 ——
        # 列表看着比它列出来的那些行高出一块，而用户看不出那一块是干什么的。
        # 上下各留 :data:`POPUP_LIST_INSET`（边框 + 内边距），正好是视口的上下留白。
        rows = len(fields) * self._popup.sizeHintForRow(0)
        height = min(
            rows + POPUP_LIST_INSET * 2,
            max(0, self._host.height() - anchor.y()),
        )
        # 左沿让开的那一段由 :meth:`_popup_left` 算，**右沿仍与胶囊齐** —— 宽度跟着
        # 左移一起收窄，于是「# 与输入框对齐」与「不许越过右边」两件事都成立。
        # 窗口窄到放不下时宁可让列表贴着窗口左边（那时右沿也已经顶到窗口边了）。
        #
        # 让开量有个上限：**列表不许窄到看不出是什么**（:data:`_POPUP_MIN_WIDTH`）。
        # **按胶囊自己算这个上限，不按窗口算** —— 写成 ``host.width() - WIDTH`` 时它
        # 等于「让开量 ≤ ``MARGIN_RIGHT``」（两者只差一个右边距），于是胶囊一往右挪、
        # :data:`MARGIN_RIGHT` 调小，这个上限就跟着变紧，``#`` 对齐那条会在与本意完全
        # 无关的地方红 —— 守卫测试 ``test_the_candidate_list_follows_the_capsule_layout``
        # 一直是靠「它碰巧比那个上限大」才过的。
        field_left = self.mapTo(self._host, QPoint(0, 0)).x()
        left = max(0, min(self._popup_left(), field_left + WIDTH - _POPUP_MIN_WIDTH))
        self._popup.setGeometry(left, anchor.y(), WIDTH - max(0, left - field_left), height)
        items = self._candidate_items()
        if items:
            self._popup.setCurrentItem(items[0])
        self._popup.raise_()
        self._popup.show()

    def _item_padding(self) -> int:
        """**固定就是菜单那一档**（:data:`POPUP_LIST_ITEM_PAD`）——这里不再算什么。

        这一段原本是照着「文字要与输入框里那个字对齐」算出来的（实测 17 点），于是
        列表里那个 ``#`` 前面空着一大片。那一片有**一半是白给的**：菜单那一档 8 点是
        给「左边留了对勾 gutter」那份样式定的，列表没有对勾，要那么多内边距没有道理。

        现在让路的活整个交给**框的位置**（见 :meth:`_popup_left`）：框往右挪这么多、
        宽度跟着收窄，``#`` 仍然落在输入框里那个正下方，而框内的留白回到菜单那一档。
        """
        return POPUP_LIST_ITEM_PAD

    def _popup_left(self) -> int:
        """候选列表的左沿（**宿主**坐标）—— 让列表里那个 ``#`` 落在输入框里那个正下方。

        「列表里那个 ``#`` 离自己左沿多远」是三段加起来：

        - 边框 + 内边距（:data:`POPUP_LIST_INSET`，实测 7 点）；
        - 行内边距（:meth:`_item_padding`，8 点）；
        - Qt 在这两段**之前**自己让开的一小段（:data:`_ITEM_TEXT_BASELINE`，实测 1 点）。

        拿它与输入框里那个字的内缩（:meth:`_text_inset`）一减，就是框该往右让开的那
        一段。**现量而不是写死**：输入框那一侧是「左边距 + 放大镜 + 间距」三样加起来的，
        任一样动了写死的数就不作数，而两个 ``#`` 错开几点用户一眼看得见。

        宽度跟着这一段一起收（见 :meth:`_fill_popup`），所以右沿仍旧压在胶囊右沿上。
        """
        inset = POPUP_LIST_INSET + self._item_padding() + _ITEM_TEXT_BASELINE
        return self.mapTo(self._host, QPoint(0, 0)).x() + max(0, self._text_inset() - inset)

    def _text_inset(self) -> int:
        """输入框里那个字离**胶囊左沿**多远（点）。

        :meth:`_item_padding` 的另一半。**现量而不是写死**：这一段是「左边距 + 放大镜
        + 间距」三样加起来，任一样动了（换个放大镜尺寸、改个内边距），写死的那个数
        就不作数 —— 而两个 ``#`` 错开几点是用户一眼看得见的。

        ``layout().activate()`` 是为了布局还没跑过的时候也问得到（``__init__`` 里
        :meth:`_apply_theme` 跑在布局之前，那一次量出来的值会在随后的 ``resizeEvent``
        里被重新算掉）。
        """
        layout = self.layout()
        if layout is not None:
            layout.activate()
        corner = self._edit.contentsRect().topLeft()
        return corner.x() + self._edit.mapTo(self, corner).x()

    def _close_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()

    def _pointer_over_popup(self) -> bool:
        """鼠标指针此刻是不是在这份候选列表上。"""
        app = QApplication.instance()
        widget = app.widgetAt(QCursor.pos()) if app is not None else None
        return widget is not None and (widget is self._popup or self._popup.isAncestorOf(widget))

    def edit_ended(self) -> None:
        """输入框失焦：收掉候选列表（与改名的输入框不同，**这里不提交** ——
        搜索不是改名，点到别处不该顺手把半句话变成一条筛选条件）。

        **指针正落在候选列表上时不算**：点选一个候选同样会让输入框失焦，而那一下的
        结果由 ``itemClicked`` 去接（收在这里就把它吃掉了）。判定问的是「指针在哪儿」
        而不是「焦点去哪儿了」—— 指针在列表上就说明用户点的就是列表。
        """
        if self._popup.isVisible() and self._pointer_over_popup():
            return
        self._close_popup()
        if self._focused:
            self._focused = False
            self._apply_theme()
            self.editing_changed.emit(False)

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新下发一次颜色（与 ``panels.welcome`` 同一个做法）。

        **这一份样式表一个字节都没变就什么都不做。** 再下发一次不是「重新拼一遍字符串」
        那么轻 —— Qt 会把整棵子树重新 polish 一遍（``repolish``），于是每次焦点进出
        （:meth:`focus_gained` / :meth:`edit_ended` 都要调它）都要把输入框、放大镜、
        候选列表那一整棵树走一遍。

        **判据是「样式表的三个输入」**：主题、圆角半径（按胶囊高度算，见
        :meth:`resizeEvent`）、以及**有没有焦点**（``focused`` 决定边框是不是高亮色）。
        少了第三个，焦点那一栏就永远不会换色 —— 而症状只是「点进去边框没亮」，
        不像崩了那么好找。
        """
        app_theme = theme.current_theme()
        radius = self.height() // 2
        if (
            self._applied_theme == app_theme
            and self._applied_radius == radius
            and self._applied_focused == self._focused
        ):
            return
        self.setStyleSheet(search_field_style(app_theme, focused=self._focused, radius=radius))
        # 候选列表是**建好之后**才存在的（``_build_ui`` 的最后一步），主题早就下发过了，
        # 于是它一直拿着系统默认那一档颜色。先把调色板补给它，再按「取自同一个地方」
        # 的规矩生成样式表（见 ``theme.push_palette`` 与 ``popup_style``）。
        #
        # **顺序不能换**：样式表里写死的是 ``rgba(...)``，所以它必须从**刚推下去的那份**
        # 调色板生成。代价是装完样式表之后 Qt 会重新 polish 一遍这个控件并**重算它的
        # 调色板**（实测换成深色后列表的 ``Highlight`` 停在浅色那一档 ``#548fff``）——
        # 那一档只在这一个控件上偏，界面上没有任何地方读它（列表整块是样式表在画），
        # 而 ``QMenu`` 不受这个重算影响，右键菜单那个自绘快捷键列读的仍是刚推下去的那份。
        theme.push_palette(self._popup)
        self._popup.setStyleSheet(popup_style(self._popup, item_padding=self._item_padding()))
        self._icon.setPixmap(magnifier(app_theme, self.devicePixelRatioF()))
        self._applied_theme = app_theme
        self._applied_radius = radius
        self._applied_focused = self._focused

    def focus_gained(self) -> None:
        """输入框拿到焦点：边框换成高亮色，并告诉外面「键盘归这里了」。"""
        if self._focused:
            return
        self._focused = True
        self._apply_theme()
        self.editing_changed.emit(True)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        # 圆角半径按**当前高度**算（见 :func:`search_field_style`）：胶囊的高度由
        # ``MainWindow._layout_title_bar`` 按窗口让出来的那一条给，而 Qt 样式表里
        # 半径一旦超过高度的一半就不画 —— 高度一变就得把样式重下一次。
        super().resizeEvent(event)
        self._apply_theme()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().focusInEvent(event)
        self.focus_gained()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - 名字是 Qt 定的
        """点胶囊的哪一块都是「进去输入」。"""
        super().mousePressEvent(event)
        self.focus_search()


__all__ = [
    "HEIGHT",
    "MARGIN_RIGHT",
    "TEXT_ALIGN_DROP",
    "WIDTH",
    "CandidateList",
    "SearchEdit",
    "SearchField",
    "popup_style",
    "search_field_style",
]
