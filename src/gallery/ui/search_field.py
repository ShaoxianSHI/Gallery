"""窗口标题栏右上角那个胶囊搜索框（ADR 0026）。

**它与 :mod:`gallery.ui.title_bar` 是兄弟，不是子控件** —— ``TitleBar`` 的透明标志会罩住子控件。
它只回答回车提交了什么、按了 Esc、是不是正在输入 —— 一个文件都不碰，只发信号（红线 1）。
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QCursor,
    QFocusEvent,
    QIcon,
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
    QToolButton,
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

#: 胶囊离窗口右边多远。**这一格是「右上角那一块」的右缘**，面包屑右端的条件胶囊要与它
#: 对齐，所以它同时被 ``BreadcrumbBar.set_right_inset`` 用到 —— 「对齐的那个数」只有这一个。
#:
#: **从它开始往右量的那一块里现在还有一枚切换按钮**（:data:`ScopeToggle`，ADR 0040 决策 5），
#: 也就是说胶囊自己被推到左边去了，但这一格仍然是「右上角那一块」的右缘：面包屑要对齐的仍然是
#: 胶囊的右缘，换算在 :meth:`MainWindow._layout_title_bar` 里做。
MARGIN_RIGHT = 20

#: 切换按钮的边长（点）。**正方形**：它只有一个状态可切（搜索 ⇄ 筛选），没有下拉箭头要放 ——
#: 里面塞两样东西就成了「外部应用」那种 44 点宽的按钮（见 ``external_button.BUTTON_WIDTH``），
#: 而一个 44 点宽的单状态按钮摆在 208 点宽的胶囊右边会喧宾夺主。
TOGGLE_EDGE = 22

#: 按钮里那个图标的边长（点）。**与按钮边长分开**：在 22 点里画一个 22 点的东西，它会顶到边框
#: 上，读起来像「图标被框住了」而不是「图标在按钮里」。
#:
#: **13 这一档有出处**：与 :data:`ICON_EDGE`（胶囊里那个放大镜）、
#: ``external_button.ICON_EDGE``（外部应用那个）同一档 —— 标题条上三个图标都是 13，而 16 就明显
#: 粗一圈。第三个图标偏一档，整条标题条上的图标看着就不是一套的了。
TOGGLE_ICON_EDGE = 13

#: 切换按钮与胶囊之间的缝（点）。与 :data:`~gallery.ui.external_button.BUTTON_GAP`（9）同一档：
#: 挤在一起读起来像胶囊自带的一个零件，而它是另一样东西。
TOGGLE_GAP = 8

#: 两种模式下占位文字各写什么。**两句话要一眼能分**：「筛选照片…」说明照片还在原地、只是少了
#: 一些，「搜索照片…」说明整层已经被换掉了（ADR 0040 决策 5）。
PLACEHOLDER_SEARCH = "搜索照片…"
PLACEHOLDER_FILTER = "筛选照片…"

#: 胶囊比标题栏那一条的中线再往下挪多少（点）：挪 0 点齐、挪 2 点就比标题低 1.75 点，
#: 取中间这一格。**别再往下加** —— 2 点已经实测过是过头的那一头。
TEXT_ALIGN_DROP = 1

#: 放大镜直径（点）。
ICON_EDGE = 13

#: 画之前往里收多少（点）。**不是可有可无的余量**：笔宽画在路径两侧，收少了会被画布裁掉。
ICON_INSET = 1.6

#: 放大镜那两笔的粗细（点）。
_ICON_STROKE = 1.4

#: 输入区里那一列的字号，与标题文字（13 点）差一档 —— 它是次要控件。**与弹出列表、弹出
#: 菜单共用同一个数**（``POPUP_FONT_PX``）：那三样在用户眼里是同一种排版，各存一份早晚差一档。
_FONT_PX = POPUP_FONT_PX

#: 候选那一行在行内边距为 0 时，文字**笔尖**离视口左沿还有多远（点）。**不是 0**：Qt 画
#: ``::item`` 的文字之前还要让开一小段，QSS 的 ``padding`` 在那之后才叠加；只能量过写死。
_ITEM_TEXT_BASELINE = 1

#: 候选列表最窄还能是多少（点）：左沿是跟着输入框那一侧让开的，让开得太过就成了一条看不出
#: 是什么的竖条。**与胶囊离窗口右边多远无关**（原因见 :meth:`SearchField._fill_popup`）。
_POPUP_MIN_WIDTH = 120


class SearchEdit(QLineEdit):
    """胶囊里那个输入框。**键盘这一层自己接管**：窗口把 ``Esc`` 绑成了「上一级」，不抢过来的话
    用户正在搜索时按 Esc 会连带着把这一层退出去。
    """

    def __init__(self, owner: SearchField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setObjectName("searchEdit")
        self.setPlaceholderText(PLACEHOLDER_SEARCH)
        self.setFrame(False)
        # **只有点它才进来**：默认的 ``StrongFocus`` 会让窗口打开时把光标送进这个框，于是
        # 方向键在网格上立刻失灵。要它进来有一条明路：⌘F（``focus_search``）。
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
    """胶囊长什么样。底色与改名的输入框同一个函数派生，聚焦时边框换成调色板的 ``Highlight``。
    ``radius`` 由摆它的那一处按实际高度给过来：超过高度一半就整个不画，胶囊会变成方框。
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
    """候选列表长什么样 —— 样式表由 :mod:`gallery.ui.menu_style` 生成，这里只补字号与行内边距。"""
    return (
        f"{popup_list_style(list_widget, item_padding=item_padding)}\n"
        f"QTreeWidget#searchPopup {{ font-size: {_FONT_PX}px; }}"
    )


def magnifier(app_theme: AppTheme, ratio: float) -> QPixmap:
    """那个放大镜：**现画的**，不往资源里塞一张图。

    **坐标一律用逻辑像素**（只有画布尺寸乘 ``ratio``），否则 Retina 上整张图会放大一倍。
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

    挂了样式表之后 Qt 不再按 ``State_MouseOver`` 挑高亮色，所以自己把悬停那行设成当前项。
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
    """标题栏右上角那个胶囊搜索框；:meth:`submit` 把清空输入框这件事一并做了。"""

    #: 用户回车确认了（**清空输入框之后**才发，信号里带着原文）。
    submitted = Signal(str)
    #: 用户按了 Esc，且输入框里已经没有字可清 —— 外面该处理的是「撤销一条筛选条件」或
    #: 「返回上一层」，这一层不替它决定。
    dismissed = Signal()
    #: 开始 / 结束输入。别的命令要在输入期间让开（与改名的两个输入框同一件事）。
    editing_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchField")
        # **只定死宽度**（高度交给摆它的那一处）：``setFixedSize`` 会把高度也钉死，于是窗口
        # 给不出 :data:`HEIGHT` 点时 ``setGeometry`` 改不动，胶囊就压到照片区顶边上。
        self.setFixedWidth(WIDTH)
        self.resize(WIDTH, HEIGHT)
        # 没有它，样式表里的底色与边框一条都不会画：Qt 只给 QFrame 那一族自动画样式背景，
        # 普通 QWidget 得自己声明。
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

        # 候选列表挂在**窗口**上（是胶囊的兄弟，不是子控件），也**不是** ``Qt.Popup`` 窗口：
        # 挂在胶囊里会被裁掉（Qt 只把子控件画在父矩形之内，而它要伸到胶囊下面去）；用弹出
        # 窗口则会在 ``show()`` 那一刻把焦点从输入框抢走。挂在窗口上的普通子控件不吃焦点。
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
        #: 上一次**真的**下发过的样式表是哪一份（起手是「没有」，所以构造时那一次必定下发）。
        self._applied_theme: AppTheme | None = None
        self._applied_radius = -1
        self._applied_focused = False
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    # ---- 对外 --------------------------------------------------------------

    def set_scope(self, filtering: bool) -> None:
        """切到「筛选当前视图」还是「搜索整个库」（ADR 0040 决策 5）。

        **只改占位文字**：候选列表、``#`` 语法、胶囊的圆角与配色全都一样 —— 用户敲的是同一套
        条件语法，变的只是那些条件管到哪儿。把两件事做成两套语法，用户就得学两遍，而两遍里
        有一遍会是他不用的那个。
        """
        self._edit.setPlaceholderText(PLACEHOLDER_FILTER if filtering else PLACEHOLDER_SEARCH)
        self.setToolTip(
            "在这些条件里筛选当前这一层（切到全部照片就筛整库，切到日历就筛日历）"
            if filtering
            else "在整个库里检索（切到筛选按钮可以只筛当前这一层）"
        )

    def focus_search(self) -> None:
        """聚焦并把光标放进胶囊（⌘F）。"""
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()

    def load(self, text: str) -> None:
        """把一条已有的筛选条件**放回输入框**接着改（面包屑上点一枚胶囊时），**整串选中**。"""
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

        列表那一列本来就写着 ``#``，不要再补一个；补完**列表要收起来**，否则回车是「再补全一次」。
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

        判据是「有没有敲空格」而不是「这一段认不认得出字段」—— 要的是边打边筛。
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
        """列一遍候选。**没有候选就不弹**：一个空框只是把界面弄花。"""
        self._popup.clear()
        for field in fields:
            # **只一列**：这份列表原本还带一句灰字提示，但那一列从来没被画出来过
            # （``QTreeWidgetItem`` 带着两列的值构造、再 ``add``，树不会长出第 2 列）。
            self._popup.addTopLevelItem(QTreeWidgetItem([f"#{field.label}"]))
        if not fields:
            self._close_popup()
            return
        # 位置换算到**窗口**坐标（列表是胶囊的兄弟，见 __init__ 那段）。
        anchor = self.mapTo(self._host, QPoint(0, self.height() + 2))
        # 高**按行高算**，不按「行数 × 一个写死的数」—— 写死的那个数并不是这一档字号下真正
        # 的行高，于是最下面多出一截空白。上下各留 :data:`POPUP_LIST_INSET` 正好是视口留白。
        rows = len(fields) * self._popup.sizeHintForRow(0)
        height = min(
            rows + POPUP_LIST_INSET * 2,
            max(0, self._host.height() - anchor.y()),
        )
        # 左沿让开的那一段由 :meth:`_popup_left` 算，**右沿仍与胶囊齐**（宽度跟着左移一起
        # 收窄）。让开量有上限：**列表不许窄到看不出是什么**（:data:`_POPUP_MIN_WIDTH`），
        # 而这个上限要**按胶囊自己算、不按窗口算** —— 写成 ``host.width() - WIDTH`` 就等于
        # 「让开量 ≤ MARGIN_RIGHT」，MARGIN_RIGHT 一调小上限就跟着变紧。
        field_left = self.mapTo(self._host, QPoint(0, 0)).x()
        left = max(0, min(self._popup_left(), field_left + WIDTH - _POPUP_MIN_WIDTH))
        self._popup.setGeometry(left, anchor.y(), WIDTH - max(0, left - field_left), height)
        items = self._candidate_items()
        if items:
            self._popup.setCurrentItem(items[0])
        self._popup.raise_()
        self._popup.show()

    def _item_padding(self) -> int:
        """**固定就是菜单那一档**（:data:`POPUP_LIST_ITEM_PAD`）：让路的活整个交给框的位置。

        框往右挪、宽度跟着收窄（见 :meth:`_popup_left`），框内留白回到菜单那一档。
        """
        return POPUP_LIST_ITEM_PAD

    def _popup_left(self) -> int:
        """候选列表的左沿（**宿主**坐标）—— 让列表里那个 ``#`` 落在输入框里那个正下方。

        让开量 = 边框 + 内边距 + 行内边距 + Qt 自己让开的一小段，再减输入框里那个字的内缩。
        **现量而不是写死**，任一样动了写死的数就不作数。
        """
        inset = POPUP_LIST_INSET + self._item_padding() + _ITEM_TEXT_BASELINE
        return self.mapTo(self._host, QPoint(0, 0)).x() + max(0, self._text_inset() - inset)

    def _text_inset(self) -> int:
        """输入框里那个字离**胶囊左沿**多远（点），是 :meth:`_item_padding` 的另一半。
        **现量而不是写死**：这一段是「左边距 + 放大镜 + 间距」三样加起来。
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
        """输入框失焦：收掉候选列表（**这里不提交** —— 搜索不是改名）。

        **指针正落在候选列表上时不算**：点选候选同样会失焦，而那一下由 ``itemClicked`` 去接。
        """
        if self._popup.isVisible() and self._pointer_over_popup():
            return
        self._close_popup()
        if self._focused:
            self._focused = False
            self._apply_theme()
            self.editing_changed.emit(False)

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新下发一次颜色。

        **样式表的三个输入**（主题、圆角半径、有没有焦点）没变就什么都不做 —— 再下发会让 Qt
        重新 polish 整棵子树。
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
        # 候选列表是**建好之后**才存在的，主题早就下发过了，于是它一直拿着系统默认那一档。
        # 先把调色板补给它，再生成样式表 —— **顺序不能换**：样式表里写死的是 ``rgba(...)``，
        # 必须从刚推下去的那份调色板生成。代价是装完样式表之后 Qt 会重算这个控件的调色板
        # （那一档只在这一个控件上偏，而列表整块是样式表在画，没人读它）。
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
        # 圆角半径按**当前高度**算：Qt 样式表里半径一旦超过高度的一半就不画，而胶囊的高度
        # 由 ``MainWindow._layout_title_bar`` 给 —— 高度一变就得把样式重下一次。
        super().resizeEvent(event)
        self._apply_theme()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().focusInEvent(event)
        self.focus_gained()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - 名字是 Qt 定的
        """点胶囊的哪一块都是「进去输入」。"""
        super().mousePressEvent(event)
        self.focus_search()


def scope_arrows(app_theme: AppTheme, ratio: float, *, active: bool) -> QPixmap:
    """那两支反向的横向箭头（上下各一支，左上那支朝左、右下那支朝右）：**现画的**，
    与 :func:`magnifier` 同一套规矩（坐标一律逻辑像素，仓库里不放图）。

    **选中态换的是不透明度而不是形状** —— 两枚图标长得不一样会让用户以为是两个不同的功能，
    而它们是同一个功能的两端。区别只在「现在是不是筛选模式」这一件事上。

    **画布是 :data:`TOGGLE_ICON_EDGE` 而不是按钮边长**：画布给多大，图形就画多大，所以
    图标跟着按钮一起变大就会顶到边框上。

    **各段比例一律按画布算**，不写死点数 —— 换一档画布时形状跟着缩，放大镜那边同理。

    **图形占画布 0.56、笔宽仍取 :data:`_ICON_STROKE`**：两支箭头叠在一起的墨迹是放大镜的
    一倍多，占满就会读成「图标被框住了」；而笔宽不跟着缩，才与放大镜是同一支笔。
    """
    size = TOGGLE_ICON_EDGE
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(app_theme.text if active else app_theme.text_at(0.55))
    pen.setWidthF(_ICON_STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    left, right = size * 0.22, size * 0.78
    # 两支箭杆分居画布的 0.30 与 0.70，箭头张开的那一半（0.125）因此离上下边还剩 2.4 点 ——
    # 笔宽画在路径两侧，收少了会被画布裁掉（与放大镜的 :data:`ICON_INSET` 同一件事）。
    head_len, head_half = size * 0.145, size * 0.125
    # ``back`` 是「箭头从尖端往箭杆那一头退多少」：两支箭头的分叉都朝箭杆内侧，尖端那一端
    # 才不会被画布裁掉（写死成同一个方向的话，朝右那支的分叉正好戳到画布右缘上）。
    for tip_x, other_x, back, y in (
        (left, right, head_len, size * 0.30),
        (right, left, -head_len, size * 0.70),
    ):
        painter.drawLine(QPointF(other_x, y), QPointF(tip_x, y))
        painter.drawLine(QPointF(tip_x, y), QPointF(tip_x + back, y - head_half))
        painter.drawLine(QPointF(tip_x, y), QPointF(tip_x + back, y + head_half))
    painter.end()
    return pixmap


def toggle_style(app_theme: AppTheme, *, active: bool, radius: int = 0) -> str:
    """切换按钮长什么样。

    **选中时整枚按钮填满高亮色**（用户原话：「选中时高亮整个按钮，而不只是轮廓」）。

    边框单独换色**不够**：按钮是 22 点的圆角小块，一圈 1 像素的蓝边在小尺寸下只占边长的一成，
    而这一枚按钮没有文字标签可看 —— 唯一的「现在开着吗」信号就是那圈边。于是底色也跟着换成
    ``Highlight``，与弹出菜单里选中项（``menu_style`` 的 ``:selected``）用同一档：用户在菜单与
    按钮之间来回走，两处要看起来是同一件事。

    **未选中时底色与胶囊同一个来源**（:func:`~gallery.ui.rename_edit.field_background`）——
    输入面那一族统一是窗口底色靠那一圈边框显出边界（ADR 0035 决策 1）。
    """
    corner = max(0, radius or TOGGLE_EDGE // 2)
    if active:
        border = "palette(highlight)"
        background = "palette(highlight)"
    else:
        border = rgba(app_theme.text_at(0.28))
        background = rgba(field_background(app_theme))
    return f"""
    QToolButton#searchScopeToggle {{
        background: {background};
        border: 1px solid {border};
        border-radius: {corner}px;
    }}
    QToolButton#searchScopeToggle:hover {{ border: 1px solid palette(highlight); }}
    """


class ScopeToggle(QToolButton):
    """胶囊右边那枚按钮：把搜索框在「搜索整个库」与「筛选当前视图」之间切换（ADR 0040）。

    **一个文件都不碰**，只发 :attr:`toggled` —— 与 :mod:`gallery.ui.rename_edit` 同一件事。
    """

    #: 用户切了模式（带的是「现在是筛选吗」这个 ``bool``）。
    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchScopeToggle")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # **不要 ``setFlat``**：那会让 Qt 不再画样式背景，而我们要画的就是那一圈边框与底色
        # （与胶囊同一份样式来源，见 :func:`toggle_style`）。
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._active = False
        self._applied_theme: AppTheme | None = None
        self._applied_radius = -1
        self._applied_active: bool | None = None
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    def set_filtering(self, filtering: bool) -> None:
        """当前是不是筛选模式。**不发 :attr:`toggled`** —— 那是「用户按了」的信号，
        而这里只是外面（换库、恢复偏好）在同步状态；发了会让外面以为用户自己切过一次。"""
        if self.isChecked() != filtering:
            self.blockSignals(True)
            try:
                self.setChecked(filtering)
            finally:
                self.blockSignals(False)
        if self._active != filtering:
            self._active = filtering
            self._apply_theme()

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重下一次 —— 输入面那一族的颜色由 :mod:`gallery.ui.theme` 钉死，
        别处不兜一层（ADR 0035 决策 2）。"""
        app_theme = theme.current_theme()
        radius = self.height() // 2
        if (
            self._applied_theme == app_theme
            and self._applied_radius == radius
            and self._applied_active == self._active
        ):
            return
        self.setStyleSheet(toggle_style(app_theme, active=self._active, radius=radius))
        self.setIcon(QIcon(scope_arrows(app_theme, self.devicePixelRatioF(), active=self._active)))
        self._applied_theme = app_theme
        self._applied_radius = radius
        self._applied_active = self._active

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._apply_theme()


__all__ = [
    "HEIGHT",
    "MARGIN_RIGHT",
    "PLACEHOLDER_FILTER",
    "PLACEHOLDER_SEARCH",
    "TEXT_ALIGN_DROP",
    "TOGGLE_EDGE",
    "TOGGLE_GAP",
    "TOGGLE_ICON_EDGE",
    "WIDTH",
    "CandidateList",
    "ScopeToggle",
    "SearchEdit",
    "SearchField",
    "popup_style",
    "scope_arrows",
    "search_field_style",
    "toggle_style",
]
