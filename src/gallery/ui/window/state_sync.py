"""「状态变了，界面跟着重算」的**唯一编排点**（ADR 0050）。

界面由状态算出来的那几份映射原先散在每个动作末尾（换层、换页、筛选生效、改名各写一遍），
而它们**都是纯映射** —— 漏掉一个的症状是界面与状态对不上，而不是报错（ADR 0041 修的正是这个）。
`_sync_ui` 把它们按固定次序串成一处，各条路径只管改状态；**次序不是随意的**：换页要第一，
其余几份都读「现在在哪一页」。解码、几何与问 core 的那三样不收进来（ADR 0050 决策 3）。
"""

from __future__ import annotations

__all__ = ["StateSyncMixin"]


class StateSyncMixin:
    """把「从状态到界面」那几份映射收成一处。

    下面六个方法是**别处那些 mixin 提供的**（``LevelStackMixin._sync_page_to_stack``、
    ``TitleBarFlowMixin._refresh_title`` 等），本模块不实现它们 —— 它只决定按什么次序调。
    """

    def _sync_ui(self) -> None:
        """按固定次序把界面重算一遍 —— **整个窗口编排界面的唯一入口**（ADR 0050）。

        **次序不是随意的**：换页第一，其余三份映射都读「现在在哪一页」，晚一步就按上一页摆。
        各条路径只改状态、末尾叫这一声，不必自己挑该调哪几个 —— 漏一个的症状是界面与状态
        对不上（标题留着旧名、命令亮着却指不到东西），而那种错当初就是这么发生的。
        """
        self._sync_page_to_stack()
        self._refresh_title()
        self._update_breadcrumb()
        self._update_action_states()
        self._sync_order_actions()
        self._sync_settings()
