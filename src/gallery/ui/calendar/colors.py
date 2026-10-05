"""日历自己的那份配色 —— **全部从 :class:`~gallery.ui.theme.AppTheme` 派生**。

底色只有 ``AppTheme.background`` 一个来源，浅色 / 深色整棵日历一起翻过去，不必两份色表；
**没有「周末底色」**（七列一律用窗口底色）。
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
    """日历画自己那些东西要用的全部颜色（底色**不在这里**；需要盖住什么时才用 :attr:`chip`）。"""

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
