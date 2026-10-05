"""日历视图（浏览方式的第三种，年 / 月 / 周三层）。

:mod:`~gallery.ui.calendar.page` 是这一页（状态、快捷键、三层堆叠），共用的底子在 ``base``，
纯像素数学在 ``layout``，小工具在 ``colors`` / ``painting`` / ``thumbs``；日期与分桶在核心层。
"""

from gallery.ui.calendar.page import CalendarPage

__all__ = ["CalendarPage"]
