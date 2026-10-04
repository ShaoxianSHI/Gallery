"""日历视图（浏览方式的第三种，年 / 月 / 周 / 日四层）。

界面上它就是新的一块页（:class:`~gallery.ui.calendar.page.CalendarPage`）：工具条
占掉原本放面包屑的那一行，下面四层用 ``Y`` / ``M`` / ``W`` / ``D`` 切。

包里的分工：

- :mod:`~gallery.ui.calendar.page` —— 这一页：状态、快捷键、四个视图的堆叠；
- :mod:`~gallery.ui.calendar.year_view` / ``month_view`` / ``time_view`` —— 四层的画法；
- :mod:`~gallery.ui.calendar.base` —— 四层共用的底子（数据、配色、选中）；
- :mod:`~gallery.ui.calendar.layout` —— **纯像素数学**，不碰 Qt；
- :mod:`~gallery.ui.calendar.colors` —— 从 :class:`~gallery.ui.theme.AppTheme` 派生的配色；
- :mod:`~gallery.ui.calendar.painting` —— 画文字与照片的那些小工具；
- :mod:`~gallery.ui.calendar.thumbs` —— 这一侧的小幅缩略图仓库。

日期与分桶在核心层（:mod:`gallery.core.library.calendar`），界面不自己算日子。
"""

from gallery.ui.calendar.page import CalendarPage

__all__ = ["CalendarPage"]
