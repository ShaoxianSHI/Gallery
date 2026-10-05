"""横幅（ADR 0029）：一句话显示在哪儿、待多久，以及**谁有资格占用它**。

横幅**不进任何布局** —— 出现与消失都不动底下任何一样东西，所以每一条消息都不会把网格
往下挤一段。它是压在内容上的那一层控件（:mod:`gallery.ui.banner_overlay`），
摆位是纯函数（``banner_width`` / ``banner_rect`` / ``text_height``），不必造窗口就能测。

这里管的是**窗口这一侧的规矩**：

- :meth:`BannerFlowMixin._show_banner` 返回这条消息是否真的上了屏。``background=True``
  的是后台消息（缩略图解码失败、地点解析进度），它们不许盖掉用户刚触发的那条说明 ——
  判据是时间（:data:`BANNER_STICKY_SECONDS`）。**用户自己做的事要能说清楚，这是第一位的**；
- ``auto_hide=True`` 是「说过就走」，由**调用点点名**：用户做完一件事的确认（已重命名、
  已移到废纸篓、已复制 N 个路径）说完就没有新内容了；出错、要用户动手处理的那几句一律留下
  —— 收掉它们等于在用户还没看完时把话收走（决策 3）；
- :meth:`BannerFlowMixin._clear_banner` 是「用户已经走到别处去了」那一支：**大图页上翻一张
  照片、或退回网格**，横幅连同「谁占着它」的记账一起收掉（用户反馈）。**它与「说过就走」
  是两件事** —— 后者按时间收「做完一件事的确认」，这一条收的是「刚才那一张」的那句话，
  连出错与后台进度一并收：照片都换了一张，那句话已经不对着眼前的东西说了。

与「后台进度那几族怎么记账」不在这里：那在 :mod:`gallery.ui.window.notice`。
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QMessageBox

from gallery.ui.banner_overlay import AUTO_HIDE_SECONDS as BANNER_AUTO_HIDE_SECONDS

logger = logging.getLogger(__name__)

#: 用户触发了一条横幅之后，后台消息让位这么久（秒）。
#:
#: 横幅只有一个槽，而用户刚做的事与后台的收尾常常同时发生：把照片移到废纸篓之后，
#: 网格会立刻去给留下来的那些行要缩略图，于是「刚被自家动过、这一刻读不到」的那一张
#: 解码失败会**迟一步**报上来，把「1 张没移走」整句盖掉。改名与撤销同理。
#:
#: 代价是用户刚操作完的两秒里，真正的解码失败不冒头；两秒后照常报，而那正是用户
#: 手上正有别的动静（滚动、切库）的时候，横幅会再被刷一次。
BANNER_STICKY_SECONDS = 2.0


class BannerFlowMixin:
    """横幅那一句怎么上屏、摆在哪、什么时候自己收起来。"""

    # ---- 提示 --------------------------------------------------------------
    def _show_banner(self, text: str, *, background: bool = False, auto_hide: bool = False) -> bool:
        """显示横幅，返回这条消息是否真的上了屏。

        ``background=True`` 是**后台**消息（缩略图解码失败、地点解析进度）：它们不许盖掉
        用户刚刚触发的那条说明，理由与时长见 :data:`BANNER_STICKY_SECONDS`。用户自己做的事
        要能说清楚，这是第一位的；而「某张照片读不出来」「正在解析地点」这种话，等一会儿
        再说并不会让人误判。

        ``auto_hide=True`` 是**说过就走**：过 :data:`BANNER_AUTO_HIDE_SECONDS` 秒自己收起来
        （ADR 0029 决策 3）。判据是「用户看完要不要动手」：

        - **用户做完一件事的确认** —— 「已重命名为…」「已把 3 项移到废纸篓。⌘Z 可以撤销。」
          「已复制 12 个路径。」—— 说完就没有新内容了，继续压在照片中间只是碍事；
        - **要用户动手处理的那几句一律留下**：出错、没能做成、「这一层是空的」、
          后台进度。收掉它们等于在用户还没看完时把话收走。
        """
        now = time.monotonic()
        if background and now < self._banner_sticky_until:
            logger.debug("后台消息先让着用户动作，过一会儿再报：%s", text)
            return False
        if not background:
            self._banner_sticky_until = now + BANNER_STICKY_SECONDS
        self._banner.show_message(
            text,
            auto_hide_seconds=BANNER_AUTO_HIDE_SECONDS if auto_hide else None,
        )
        return True

    def _clear_banner(self) -> None:
        """把横幅收干净：**连同「谁占着它」的记账一起清**。

        调用点是「用户已经走到别处去了」：换层、换库、**大图页上翻一张照片或退回网格**
        （用户反馈：那句话说的是刚才那一张，人已经在看下一张了，它还压在顶边）。

        **记账必须一起清**，不只是 ``hide()``：三族后台消息（读元数据 / 解析地点 /
        建检索索引）各自记着自己写的是哪一句（:class:`~gallery.ui.window.notice.NoticeSlot`），
        「这一层是空的」那一族还记着 ``_level_notice``。横幅没了而账本还留着的话，下一条
        进度会以为「上面已经写着我的字」而继续让着，而 ``_update_level_notice`` 更会
        **收掉一条它并不拥有的横幅** —— 把用户刚做完一件事的那句话抹了。

        各自 ``NoticeSlot.clear`` 只清自己还占着的那一份（理由写在那个方法的文档里），
        所以这里挨个叫一遍就够，不必自己去碰那几个私有名字。
        """
        self._progress_slot.clear()
        self._place_slot.clear()
        self._index_slot.clear()
        self._level_notice = False
        self._banner_sticky_until = 0.0
        self._banner.hide()

    # ---- 横幅的摆位（ADR 0029）------------------------------------------------

    def _on_page_changed(self, _index: int) -> None:
        """换页：横幅要重新摆 —— 大图页上照片区右边让给了元数据面板，要避开的那一块变了；
        缩略图窗口也要换成**新页**要的那一批（网格按行、日历按格子）。"""
        self._layout_banner()
        self._refresh_thumbnail_window()

    def _banner_area(self) -> QRect:
        """横幅可以摆的那一片：**整个窗口的内容区**。

        中央控件自己已经让开了标题栏那一条（见 ``_build_ui``），所以这里拿它的
        ``geometry`` 就是内容区那一带，不必再算一遍边距。

        **不因为元数据面板摆着就把它减掉**（原先是减的）：横幅是**窗口上**的一层，
        居中的基准就该是**整个窗口** —— 减掉面板之后它落到照片那一半的正中间，看着像是
        只对照片说话，而这一句说的是整个窗口里刚发生的事（用户反馈）。面板交给
        :meth:`_banner_avoid` 让开，位置一分不动。
        """
        central = self.centralWidget()
        return QRect(central.geometry()) if central is not None else QRect()

    def _banner_avoid(self) -> QRect | None:
        """横幅不该压住的那一块：**正摆着的元数据面板**（没摆着就是 ``None``）。

        面板不在中央控件的几何里，而是大图页横排布局里的另一个成员；它摆着时横幅会盖在
        它上面，于是把宽度收窄到「居中之后右边缘停在它左边」（见
        :func:`~gallery.ui.banner_overlay.banner_width`）。
        """
        panel = self._panel
        if panel is None or not panel.isVisible():
            return None
        return QRect(panel.geometry())

    def _layout_banner(self) -> None:
        """把横幅摆在**整个窗口顶边居中**（它在窗口上，不属于任何布局）。

        顶边那一带正好是面包屑所在的那一行（ADR 0029 决策 1）；横向的基准是整个内容区，
        元数据面板摆着时让开的是**宽度**，中心点仍在窗口正中（见 :meth:`_banner_area`）。

        窗口改大小、换页、展开收起元数据面板之后都要重摆一次 —— 与 ``_layout_title_bar``
        同一套时机。
        """
        self._banner.reposition(self._banner_area(), self._banner_avoid())
        if self._banner.isVisible():
            self._banner.raise_()  # 窗口布局跑完之后中央控件会被抬上来

    def _report_error(self, message: str) -> None:
        QMessageBox.critical(self, "Gallery", message)


__all__ = ["BannerFlowMixin"]
