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
from gallery.ui.menu_style import popup_list_style
from gallery.ui.rename_edit import field_background
from gallery.ui.theme import AppTheme, rgba

#: 胶囊多宽。够放下「搜索照片…」与十来个字，又不至于从标题正中伸到交通灯那边。
WIDTH = 208

#: 胶囊多高。比标题栏（见 ``title_bar.HEIGHT``）矮一点，上下留白，不贴着窗口顶边。
HEIGHT = 22

#: 胶囊离窗口右边多远。与左边交通灯那一段（78 点）不对称是刻意的：那边系统自己画着
#: 三个按钮，胶囊是后来加的，靠边会显得被挤出去，于是留宽一点。
MARGIN_RIGHT = 18

#: 放大镜直径（点）。
ICON_EDGE = 13

#: 画之前往里收多少（点）。**不是可有可无的余量**：笔宽画在路径两侧，收少了就会被
#: 画布裁掉（见 :func:`magnifier`）。
ICON_INSET = 1.6

#: 放大镜那两笔的粗细（点）。
_ICON_STROKE = 1.4

#: 输入区里那一列的字号，与标题文字（13 点）差一档 —— 它是次要控件。
_FONT_PX = 12

#: 候选列表一行多高。与菜单那一档（``menu_style``）看齐，不另立一套。
_ROW_HEIGHT = 26


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


def popup_style(list_widget: QTreeWidget) -> str:
    """候选列表长什么样 —— **样式表由 :mod:`gallery.ui.menu_style` 生成**。

    颜色不在这儿定：那是「菜单怎么长什么样」那一层的活（ADR 0024），而用户要的是
    候选列表与右键菜单**看起来是同一种东西**。两份各自派生一遍，早晚差一档。

    这里只补列表自己的字号（菜单那一档偏大，列表窄）。
    """
    return (
        f"{popup_list_style(list_widget)}\nQTreeWidget#searchPopup {{ font-size: {_FONT_PX}px; }}"
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
            item = QTreeWidgetItem([f"#{field.label}", field.hint])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(1, field.hint)
            self._popup.addTopLevelItem(item)
        if not fields:
            self._close_popup()
            return
        # 两列的宽度**写死**而不是 ``adjustSize`` 说了算：提示那一句长短不一
        # （「2024 / 2024-05」比「含子相册」长），按内容缩的话列表宽度会跟着跳。
        self._popup.setColumnWidth(0, WIDTH - 112)
        self._popup.setColumnWidth(1, 106)
        # 位置换算到**窗口**坐标（列表是胶囊的兄弟，见 __init__ 那段），
        # 并夹在窗口以内 —— 全屏或贴着底边时它不许跑到屏幕外面去。
        anchor = self.mapTo(self._host, QPoint(0, self.height() + 2))
        height = min(len(fields) * _ROW_HEIGHT + 8, max(0, self._host.height() - anchor.y()))
        self._popup.setGeometry(
            min(anchor.x(), max(0, self._host.width() - WIDTH)),
            anchor.y(),
            WIDTH,
            height,
        )
        items = self._candidate_items()
        if items:
            self._popup.setCurrentItem(items[0])
        self._popup.raise_()
        self._popup.show()

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
        """换过浅色 / 深色之后重新下发一次颜色（与 ``panels.welcome`` 同一个做法）。"""
        app_theme = theme.current_theme()
        self.setStyleSheet(
            search_field_style(app_theme, focused=self._focused, radius=self.height() // 2)
        )
        # 候选列表是**建好之后**才存在的（``_build_ui`` 的最后一步），主题早就下发过了，
        # 于是它一直拿着系统默认那一档颜色。先把调色板补给它，再按「取自同一个地方」
        # 的规矩生成样式表（见 ``theme.push_palette`` 与 ``popup_style``）。
        theme.push_palette(self._popup)
        self._popup.setStyleSheet(popup_style(self._popup))
        self._icon.setPixmap(magnifier(app_theme, self.devicePixelRatioF()))

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
    "WIDTH",
    "CandidateList",
    "SearchEdit",
    "SearchField",
    "popup_style",
    "search_field_style",
]
