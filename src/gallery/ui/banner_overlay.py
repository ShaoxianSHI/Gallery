"""覆盖在内容上的那一条横幅。

**它不在任何布局里。** 横幅原先是网格页竖排布局里的一个 ``addWidget``，于是每一条消息
都会把网格**整体往下挤一段**：照片区忽然矮一截，网格重排、滚动位置跟着动 —— 而用户
正要看的正是刚被挤走的那一屏。这里改成把它挂在窗口上（与 ``TitleBar``、
``SearchField`` 同一手法），尺寸与位置自己算：出现与消失都不动底下任何一样东西
（ADR 0029 决策 1）。

它浮在**内容区的顶边、正好是面包屑那一行**，配色是原来那一对橙色。覆盖在导航信息上
而不是压在最后一排缩略图上：面包屑过几秒就重新露出来，而用户正在挑的永远是下面那几行
照片。

**它同时出现在网格页与大图页**，因为它挂在窗口上而不是某一页里：用户正在看大图时
做的任何一件事（⌘Z 撤销、⌘C 拷贝、⌘⌫ 移走这一张）说的那句话就在**照片上面**，
不必退回网格才看得见（ADR 0029 决策 2）。

**说过就走。** 一句「已重命名为…」过了几秒还压在照片中间是碍事的，而它说完之后确实
没有新内容 —— 需要用户动手处理的那几句（出错、这一层是空的）由调用方点名要留着，
默认判据与时长都写在 :data:`AUTO_HIDE_SECONDS` 上（ADR 0029 决策 3）。

摆位与高度是三段纯算术（:func:`banner_width` / :func:`banner_rect` /
:func:`text_height`），因此不必造一个真窗口就能测 —— 与 ``title_bar.editor_rect``
同一做法。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QHideEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from gallery.ui import theme
from gallery.ui.theme import rgba

#: 「说过就走」的那些话停留多久（秒）。
#:
#: 四秒够读完一句「已把 3 项移到废纸篓。⌘Z 可以撤销。」，又不至于让它一直压在照片上。
#: **要长于 :data:`gallery.ui.main_window.BANNER_STICKY_SECONDS`**：那句话刚上屏的
#: 两秒里后台消息还在让位，之后到收起来之间还留着几秒，够用户扫一眼。
AUTO_HIDE_SECONDS = 4.0

#: 离左右、离顶边各留多少（点）。
#:
#: **横向离窗口边远一点**：窄横幅孤零零地贴着一侧像没摆完，居中才像是刻意摆的。
#: **纵向离内容区顶边 :data:`MARGIN_TOP` 点** —— 与网格页那一行的上边距一样，于是它
#: 正好压在面包屑那一行上（ADR 0029 决策 1）。顶边这一带本来就是导航信息，横幅盖住
#: 几秒、又自己收走，比压住照片下缘更好认（那一带永远是最后一行缩略图）。
MARGIN_SIDE = 28
MARGIN_TOP = 10

#: 横向最宽多少点。再宽就成了段落，两行以内的提示用不着横穿整个窗口。
MAX_WIDTH = 560

#: 框内留白与边框宽（点）。**样式表与 :func:`text_height` 共用这几个数**：高度是按它们
#: 算出来的，两边各写一份的话文字迟早会被裁掉半行或者底下多出一截空白。
PADDING_X = 12
PADDING_Y = 8
BORDER = 1

#: 横幅那两个橙色的分量（RGB）。**就是原来那一对** —— 用户每天看惯了它，换成别的颜色
#: 是把一件已经用过很久的东西改成另一件。
FILL_RGB = (255, 176, 32)
#: 底色留 16% 不透明：它盖在照片与缩略图上，压得太实就等于把那一块内容藏起来了。
FILL_ALPHA = 0.16
#: 边框留 45% —— 比底色实，才看得出横幅有这么一块边界。
EDGE_ALPHA = 0.45


def banner_width(area: QRect) -> int:
    """横幅在 ``area`` 里能有多宽。

    窗口窄到一行都放不下时就让文字换行，也不越出 ``area`` —— 最小窗口宽度是 720
    （见 ``MainWindow.setMinimumSize``），正常情况下 :data:`MAX_WIDTH` 那一档总是赢。
    """
    room = area.width() - 2 * MARGIN_SIDE
    return max(min(room, MAX_WIDTH), 1)


def text_height(text: str, width: int, metrics: QFontMetrics) -> int:
    """这句话在 ``width`` 那么宽里摆开要占多高（含边框与内边距）。

    用 :class:`QFontMetrics` 量而不是 ``QLabel.heightForWidth``：后者量的是 Qt 自己那份
    尺寸策略算出来的东西，而这里的位置与大小全是自己给的（样式表里的留白也不在它的
    计算里）。空话至少占一行高 —— 否则 ``show_message("")`` 会画出一个两像素的条。

    那个矩形的高度是 0：**只约束宽度**（实测 0 / 1 / 100 / 10000 给的是同一个高度），
    换行要几行就量几行。
    """
    inner = max(width - 2 * (PADDING_X + BORDER), 1)
    bounds = metrics.boundingRect(QRect(0, 0, inner, 0), int(Qt.TextFlag.TextWordWrap), text)
    return max(bounds.height(), metrics.height()) + 2 * (PADDING_Y + BORDER)


def banner_rect(area: QRect, size: QSize) -> QRect:
    """把 ``size`` 那么大的一横幅摆在 ``area`` 的哪儿：**水平居中，浮在顶边下方**。

    顶边而不是底边：上面那一行是面包屑与窗口标题所在地盘，横幅盖住的是**导航信息**，
    几秒之后自己会让开；而底边那一带永远是最后一行缩略图，压上去的是用户正在挑的照片
    （ADR 0029 决策 1）。
    """
    width = min(size.width(), max(area.width() - 2 * MARGIN_SIDE, 1))
    height = max(size.height(), 1)
    x = area.x() + max(area.width() - width, 0) // 2
    y = area.top() + MARGIN_TOP
    return QRect(x, y, width, height)


class BannerOverlay(QLabel):
    """窗口上那一条会自己排版、自己摆位、自己收起来的提示。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        self.setWordWrap(True)
        # **文字居中**：横幅的宽度是「装得下就多宽」，不是固定的，于是左对齐的话那句话
        # 总是偏在一边，一条窄横幅看上去像没摆正（横向、纵向都居中）。
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 几何完全由外面给（``setGeometry``），别让某个布局顺手改它。
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # 纯提示，不该吃掉底下那一片的点击与拖动：横幅正压在网格上，
        # 挡住的话点它就变成了点空处（取消选中）。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._area = QRect()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        # 底色与边框是写死的那一对橙色，只有字色跟着主题走（浅色下深字、深色下浅字），
        # 所以换过外观之后要重下这一份 —— 与 ``TitleBar`` 同一个做法。
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self.hide()

    # ---- 内容 --------------------------------------------------------------

    def show_message(self, text: str, *, auto_hide_seconds: float | None = None) -> None:
        """显示 ``text``；``auto_hide_seconds`` 给一个数就那么多秒之后自己收起来。

        收起来的方式是 ``hide`` 而不是淡出：Qt 的淡出要自己驱动一个动画，而这一条
        出现的时机（后台进度刚报上来）常常紧跟着另一条，淡到一半被换掉只会更乱。
        """
        self.setText(text)
        self._place()
        # 已经显示着的时候 ``show()`` 不再发 ``showEvent``，位置得在这里重摆一次 ——
        # 换了页面、展开了元数据面板之后，上一次摆的地方可能已经不在照片区里了。
        if self.isVisible():
            self.raise_()
        else:
            self.show()
        if auto_hide_seconds and auto_hide_seconds > 0:
            self._timer.start(int(auto_hide_seconds * 1000))
        else:
            self._timer.stop()

    def reposition(self, area: QRect) -> None:
        """把它摆在 ``area`` 里（窗口改了大小、换了页面之后由外面叫）。

        收着的时候只记下这一片，显示中才真的重摆 —— 与 ``TitleBar`` 同一取舍。
        """
        self._area = QRect(area)
        if self.isVisible():
            self._place()

    def current_area(self) -> QRect:
        """上一次 :meth:`reposition` 给的那一片（没给过就是空的）。"""
        return QRect(self._area)

    @property
    def auto_hide_pending(self) -> bool:
        """显示中的这一条是不是**说完了自己会收起来**。

        给调用方与测试一个不碰私有成员的问法：「这句话会一直挂着吗」。
        """
        return self._timer.isActive()

    # ---- 内部 --------------------------------------------------------------

    def _target_area(self) -> QRect:
        """摆在哪儿：**外面给的那一片**，还没给过就用父控件自己那块。

        还没给过的那一瞬是有的：窗口刚建好、还没 ``show()`` 时进来的第一条进度。
        那时 ``MainWindow`` 的中央控件几何也还没定，回退到父控件至少不会摆成一个
        一像素宽的条（下一轮 ``resizeEvent`` / ``showEvent`` 就会把它摆正）。
        """
        if not self._area.isEmpty():
            return QRect(self._area)
        parent = self.parentWidget()
        return QRect(parent.rect()) if parent is not None else QRect()

    def _place(self) -> None:
        area = self._target_area()
        width = banner_width(area)
        height = text_height(self.text(), width, QFontMetrics(self.font()))
        self.setGeometry(banner_rect(area, QSize(width, height)))

    def _apply_theme(self) -> None:
        """样式表就是**原来那一份**：浅橙的底 + 深一点的橙边，16% / 45% 两档不透明。

        底色压得淡是有意的 —— 它盖在照片与缩略图上，压实了就等于把那一块内容藏起来；
        边框那一档实一些，好让「横幅有这么一块边界」看得出来。

        只有**字色**跟着主题走（原先写的 ``palette(text)`` 在 Qt 样式表里并不成立，
        实际走的是控件默认色，也就是正文色那一份）。
        """
        fill = QColor(*FILL_RGB)
        fill.setAlphaF(FILL_ALPHA)
        edge = QColor(*FILL_RGB)
        edge.setAlphaF(EDGE_ALPHA)
        self.setStyleSheet(
            f"QLabel#banner {{"
            f"  background: {rgba(fill)};"
            f"  border: {BORDER}px solid {rgba(edge)};"
            f"  border-radius: 6px;"
            f"  padding: {PADDING_Y}px {PADDING_X}px;"
            f"  color: {rgba(theme.current_theme().text)};"
            f"}}"
        )

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """被收掉就不再等自己 —— 换一条消息进来、或外部直接 ``hide()`` 都是这条路。

        定时器归在这里管而不是只在 ``show_message`` 里管，是因为**收掉横幅的地方不止
        那里**：进度收尾、换层、换库都是直接 ``hide()`` 的（见 ``MainWindow``），
        只在显示时重置的话，先前的那个定时器会在新内容上原样再收一次。
        """
        super().hideEvent(event)
        self._timer.stop()


__all__ = [
    "AUTO_HIDE_SECONDS",
    "BORDER",
    "EDGE_ALPHA",
    "FILL_ALPHA",
    "FILL_RGB",
    "MARGIN_SIDE",
    "MARGIN_TOP",
    "MAX_WIDTH",
    "PADDING_X",
    "PADDING_Y",
    "BannerOverlay",
    "banner_rect",
    "banner_width",
    "text_height",
]
