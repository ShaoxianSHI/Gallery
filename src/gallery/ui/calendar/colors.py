"""日历自己的那份配色 —— **全部从 :class:`~gallery.ui.theme.AppTheme` 派生**。

刻意不照抄 Google Calendar 的色值。那一套是为网页版准备的浅色界面，而本软件要跟着系统
的浅色 / 深色一起变（见 ``ui.theme``）：底色只有 ``AppTheme.background`` 一个来源，
其余颜色都由它与文字色派生，于是切到深色时整棵日历一起翻过去，不必两份色表。

各色的来历（都写出来，改主题时能顺着看）：

===================  ==========================================================
次级文字（月份名）   正文 70%
最淡的文字（钟点）   正文 45%
分隔线               底色掺 8% 正文色；列分隔线 15%
照片条 / 底板        底色掺 7% 正文色
选中的一天           强调色压 12% 透明度 —— 与链接同一个色相，于是「点得动」
强调色 / 蓝圆点      ``AppTheme.link``（系统链接色，浅色下蓝、深色下浅蓝）
今天时刻线           ``AppTheme.error``（本软件唯一的红）
===================  ==========================================================

**没有「周末底色」**：早年是给周六周日两列掺 3% 正文色区分出来的，现在七列一律用
窗口的底色 —— 少一种颜色要跟着主题走，照片格子之间也只剩表头的星期名与分隔线。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

from gallery.ui.theme import AppTheme


def mix(first: QColor, second: QColor, ratio: float) -> QColor:
    """两个颜色按 ``ratio`` 线性插值（0 = 第一个，1 = 第二个）。"""
    ratio = min(1.0, max(0.0, ratio))
    return QColor(
        round(first.red() + (second.red() - first.red()) * ratio),
        round(first.green() + (second.green() - first.green()) * ratio),
        round(first.blue() + (second.blue() - first.blue()) * ratio),
        round(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


@dataclass(frozen=True, slots=True)
class CalendarColors:
    """日历画自己那些东西要用的全部颜色。

    底色**不在这里**：各视图都铺在窗口自己的底色上（与网格、大图页同一条规矩），
    需要盖住什么时才用 :attr:`chip`。
    """

    text: QColor
    muted: QColor
    faint: QColor
    outline: QColor
    hairline: QColor
    chip: QColor
    selection: QColor
    accent: QColor
    on_accent: QColor
    now: QColor

    @classmethod
    def derive(cls, theme: AppTheme) -> CalendarColors:
        """从一份界面主题算出日历的配色（系统换浅色 / 深色时重算一遍）。"""
        background, text = theme.background, theme.text
        accent = theme.link
        return cls(
            text=QColor(text),
            muted=theme.text_at(0.70),
            faint=theme.text_at(0.45),
            outline=mix(background, text, 0.16),
            hairline=mix(background, text, 0.08),
            chip=mix(background, text, 0.07),
            selection=QColor(accent.red(), accent.green(), accent.blue(), round(255 * 0.12)),
            accent=QColor(accent),
            # 强调色上的字用**底色**：深色主题下强调色本身是浅蓝，压深字才读得出来。
            on_accent=QColor(background),
            now=QColor(theme.error),
        )


__all__ = ["CalendarColors", "mix"]
