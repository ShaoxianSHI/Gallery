"""横幅怎么显示、摆在哪、什么时候自己收起来（ADR 0029）；**那一个槽归谁**归
:mod:`gallery.ui.window.banner_text`（ADR 0054）。

横幅不进任何布局，是压在内容上的一层控件；``auto_hide=True`` 由调用点点名，出错与要用户
动手处理的那几句一律留下。本模块在窗口上贡献一个 ``self._banner_text`` 与两个回调 ——
槽要显示一句话时转给 :meth:`BannerFlowMixin._show_banner`，而那七个字段一个都不在窗口上了。
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QMessageBox

from gallery.ui.window.state.banner_text import BannerText
from gallery.ui.window.widgets.banner_overlay import AUTO_HIDE_SECONDS as BANNER_AUTO_HIDE_SECONDS

logger = logging.getLogger(__name__)

#: 用户触发了一条横幅之后，后台消息让位这么久（秒）。横幅只有一个槽：移到废纸篓后网格立刻要
#: 缩略图，「刚被自家动过、这一刻读不到」的解码失败会迟一步把「1 张没移走」盖掉。
BANNER_STICKY_SECONDS = 2.0


class BannerFlowMixin:
    """横幅那一句怎么显示、摆在哪、什么时候自己收起来。"""

    def _init_banner(self) -> None:
        """建那一个槽的记账；``MainWindow.__init__`` 里只留这一句（ADR 0054）。

        **时钟传进去**（而不是在 :meth:`_show_banner` 里直接调 ``time.monotonic``）：
        让位期是拿秒比的一段窗口，没有注入就只能等真机上两秒才验得到。
        **三个回调都包一层**：横幅控件是在 :meth:`MainWindow._build_ui` 里建的，
        而这一句在它之前 —— 直接取 ``self._banner.hide`` 会当场 ``AttributeError``。
        """
        self._banner_text = BannerText(
            self._show_banner,
            self._banner_message,
            lambda: self._banner.hide(),
            sticky_seconds=BANNER_STICKY_SECONDS,
            monotonic=time.monotonic,
        )

    # ---- 提示 --------------------------------------------------------------
    def _show_banner(self, text: str, *, background: bool = False, auto_hide: bool = False) -> bool:
        """显示横幅，返回这条消息是否真的上了屏。

        ``background=True`` 的后台消息要让着用户刚触发的那条说明（见
        :data:`BANNER_STICKY_SECONDS`）；``auto_hide=True`` 只给「用户做完一件事的确认」，
        出错与要用户动手处理的一律留下（ADR 0029 决策 3）。
        """
        if not self._banner_text.claim_sticky(background=background):
            logger.debug("后台消息先让着用户动作，过一会儿再报：%s", text)
            return False
        self._banner.show_message(
            text,
            auto_hide_seconds=BANNER_AUTO_HIDE_SECONDS if auto_hide else None,
        )
        return True

    def _banner_message(self) -> str:
        """横幅上此刻写着什么（没显示时是空串）—— 槽在抢之前问它。"""
        return self._banner.text() if self._banner.isVisible() else ""

    @property
    def _thumbnail_failures(self) -> int:
        """这一轮解码失败了几张 —— 横幅那一句里的数字（**只读**，三处测试在读它）。

        写走 :meth:`BannerText.note_thumbnail_failure`：失败一张就加一，而那句话要的是
        累计数，所以写入口只能有一个。
        """
        return self._banner_text.thumbnail_failures

    def _clear_banner(self) -> None:
        """把横幅收干净：**连同「谁占着它」的记账一起清**（换照片、退层、换库）。

        只 ``hide()`` 不清账的话，下一条进度会以为「上面已经写着我的字」而继续让着，
        ``_update_level_notice`` 还会收掉一条它并不拥有的横幅。
        """
        self._banner_text.clear_all()
        self._banner.hide()

    # ---- 横幅的摆位（ADR 0029）------------------------------------------------

    def _on_page_changed(self, _index: int) -> None:
        """换页：横幅要重摆（大图页让开的那一块变了），缩略图窗口也要换成新页要的那一批。"""
        self._layout_banner()
        self._refresh_thumbnail_window()

    def _banner_area(self) -> QRect:
        """横幅可以摆的那一片：**整个窗口的内容区**（中央控件已让开标题栏那一条）。

        **不因为元数据面板摆着就减掉它**：横幅是窗口上的一层，居中的基准就该是整个窗口；
        面板交给 :meth:`_banner_avoid` 让开。
        """
        central = self.centralWidget()
        return QRect(central.geometry()) if central is not None else QRect()

    def _banner_avoid(self) -> QRect | None:
        """横幅不该压住的那一块：正摆着的元数据面板（没摆着就是 ``None``）。

        面板不在中央控件的几何里，而是大图页横排布局里的另一个成员，所以要把宽度减掉它。
        """
        panel = self._panel
        if panel is None or not panel.isVisible():
            return None
        return QRect(panel.geometry())

    def _layout_banner(self) -> None:
        """把横幅摆在**整个窗口顶边居中**（它在窗口上，不属于任何布局）。

        窗口改大小、换页、展开收起面板之后都重摆一次 —— 与 ``_layout_title_bar`` 同一套时机。
        """
        self._banner.reposition(self._banner_area(), self._banner_avoid())
        if self._banner.isVisible():
            self._banner.raise_()  # 窗口布局跑完之后中央控件会被抬上来

    def _report_error(self, message: str) -> None:
        QMessageBox.critical(self, "Gallery", message)


__all__ = ["BANNER_STICKY_SECONDS", "BannerFlowMixin"]
