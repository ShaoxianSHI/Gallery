"""覆盖在内容上的那一条横幅。

**它不在任何布局里** —— 挤进布局的话消息会把网格整体往下推一段（ADR 0029 决策 1）；横向居中于
整个窗口，元数据面板那一块由 ``avoid`` 让开。摆位与高度是纯算术，不必造窗口就能测。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QHideEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from gallery.ui import theme
from gallery.ui.theme import rgba

#: 「说过就走」的那些话停留多久（秒）。四秒够读完一句「已把 3 项移到废纸篓。⌘Z 可以
#: 撤销。」，又不至于一直压在照片上；**要长于 ``main_window.BANNER_STICKY_SECONDS``**。
AUTO_HIDE_SECONDS = 4.0

#: 离左右、离顶边各留多少（点）。**横向离窗口边远一点**（贴着一侧像没摆完）；
#: **纵向离内容区顶边 :data:`MARGIN_TOP` 点**，于是它正好压在面包屑那一行上（ADR 0029）。
MARGIN_SIDE = 28
MARGIN_TOP = 10

#: 横向最宽多少点。再宽就成了段落，两行以内的提示用不着横穿整个窗口。
MAX_WIDTH = 560

#: 让开 ``avoid``（大图页上的元数据面板）时，最窄还能有多宽（点）。**太窄就不像提示了**
#: （两三字一行、连着五六行），到这条线以下就不再让开。
MIN_AVOID_WIDTH = 240

#: 框内留白与边框宽（点）。**样式表与 :func:`text_height` 共用这几个数**：高度是按它们
#: 算出来的，两边各写一份的话文字迟早会被裁掉半行或者底下多出一截空白。
PADDING_X = 12
PADDING_Y = 8
BORDER = 1

#: 横幅那两个橙色的分量（RGB）。**就是原来那一对** —— 用户每天看惯了它。
FILL_RGB = (255, 176, 32)
#: 底色留 16% 不透明：它盖在照片与缩略图上，压得太实就等于把那一块内容藏起来了。
FILL_ALPHA = 0.16
#: 边框留 45% —— 比底色实，才看得出横幅有这么一块边界。
EDGE_ALPHA = 0.45


def banner_width(area: QRect, *, avoid: QRect | None = None) -> int:
    """横幅在 ``area`` 里能有多宽。

    窗口窄到一行都放不下时就让文字换行，也不越出 ``area``。``avoid`` 是**要避开的那一块**
    （大图页上的元数据面板），与 ``area`` 同一套坐标：横幅居中照 ``area`` 算，会压到那一块
    就收窄到右边缘正好停在它左边、**位置一分都不动**；窄到 :data:`MIN_AVOID_WIDTH` 就不再让。
    """
    room = area.width() - 2 * MARGIN_SIDE
    if avoid is not None and area.intersects(avoid):
        # 从 ``area`` 左边缘到 ``avoid`` 左边缘那一段（再留一道边距）。
        free = max(avoid.left() - MARGIN_SIDE - area.left(), 1)
        # 居中的那一条右边缘在 ``area.width() / 2 + width / 2``，要它不过界就得这么宽。
        room = max(min(room, 2 * free - area.width()), MIN_AVOID_WIDTH)
    return max(min(room, MAX_WIDTH), 1)


def text_height(text: str, width: int, metrics: QFontMetrics) -> int:
    """这句话在 ``width`` 那么宽里摆开要占多高（含边框与内边距）。

    用 :class:`QFontMetrics` 量而不是 ``QLabel.heightForWidth``（后者量的是 Qt 自己那份尺寸
    策略算出来的东西）。空话至少占一行高 —— 否则 ``show_message("")`` 会画出一个两像素的条；
    传给 ``boundingRect`` 的矩形高度是 0：**只约束宽度**，换行要几行就量几行。
    """
    inner = max(width - 2 * (PADDING_X + BORDER), 1)
    bounds = metrics.boundingRect(QRect(0, 0, inner, 0), int(Qt.TextFlag.TextWordWrap), text)
    return max(bounds.height(), metrics.height()) + 2 * (PADDING_Y + BORDER)


def banner_rect(area: QRect, size: QSize, *, avoid: QRect | None = None) -> QRect:
    """把 ``size`` 那么大的一横幅摆在 ``area`` 的哪儿：**水平居中，浮在顶边下方**。

    顶边那一行是面包屑与窗口标题所在地盘，横幅盖住的是**导航信息**，几秒之后自己会让开；
    底边那一带永远是最后一行缩略图（ADR 0029 决策 1）。**居中的基准是 ``area`` 自己**，
    ``avoid`` 只参与算宽度：把 ``area`` 缩掉的话中心点会跟着跑偏。
    """
    width = min(size.width(), banner_width(area, avoid=avoid))
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
        # **文字居中**：横幅的宽度是「装得下就多宽」，左对齐的话那句话总是偏在一边。
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 几何完全由外面给（``setGeometry``），别让某个布局顺手改它。
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # 纯提示，不该忽略掉底下那一片的点击与拖动（它正压在网格上，挡住就变成点空处）。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._area = QRect()
        self._avoid: QRect | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        # 底色与边框是硬编码的那一对橙色，只有字色跟着主题走，所以换过外观之后要重下一份。
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self.hide()

    # ---- 内容 --------------------------------------------------------------

    def show_message(self, text: str, *, auto_hide_seconds: float | None = None) -> None:
        """显示 ``text``；``auto_hide_seconds`` 给一个数就那么多秒之后自己收起来。

        收起来的方式是 ``hide`` 而不是淡出：淡到一半被另一条换掉只会更乱。
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

    def reposition(self, area: QRect, avoid: QRect | None = None) -> None:
        """把它摆在 ``area`` 里（窗口改了大小、换了页面之后由外面叫）。

        ``avoid`` 是要避开的那一块（元数据面板）：居中照旧按 ``area`` 算，只是不让它压到那一
        块上。收着的时候只记下这一片，显示中才真的重摆。
        """
        self._area = QRect(area)
        self._avoid = QRect(avoid) if avoid is not None else None
        if self.isVisible():
            self._place()

    @property
    def auto_hide_pending(self) -> bool:
        """显示中的这一条是不是**说完了自己会收起来**（给调用方与测试一个不碰私有成员的问法）。"""
        return self._timer.isActive()

    # ---- 内部 --------------------------------------------------------------

    def _target_area(self) -> QRect:
        """摆在哪儿：**外面给的那一片**，还没给过就用父控件自己那块。

        窗口刚建好、还没 ``show()`` 时进来的第一条进度就会撞上这一瞬：那时中央控件的几何还
        没定，回退到父控件至少不会摆成一个一像素宽的条。
        """
        if not self._area.isEmpty():
            return QRect(self._area)
        parent = self.parentWidget()
        return QRect(parent.rect()) if parent is not None else QRect()

    def _place(self) -> None:
        area = self._target_area()
        width = banner_width(area, avoid=self._avoid)
        height = text_height(self.text(), width, QFontMetrics(self.font()))
        self.setGeometry(banner_rect(area, QSize(width, height), avoid=self._avoid))

    def _apply_theme(self) -> None:
        """样式表就是**原来那一份**：浅橙的底 + 深一点的橙边，16% / 45% 两档不透明。

        底色压得淡是有意的 —— 它盖在照片与缩略图上，压实了就等于把那一块内容藏起来。只有
        **字色**跟着主题走（样式表里的 ``palette(text)`` 并不成立，实际走的是控件默认色）。
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

        定时器归在这里管而不是只在 ``show_message`` 里管：收掉横幅的地方不止那里（进度收尾、
        换层、换库都是直接 ``hide()``），只在显示时重置的话先前的定时器会再收一次新内容。
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
    "MIN_AVOID_WIDTH",
    "PADDING_X",
    "PADDING_Y",
    "BannerOverlay",
    "banner_rect",
    "banner_width",
    "text_height",
]
