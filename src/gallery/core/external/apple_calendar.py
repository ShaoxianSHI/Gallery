"""苹果日历没有日期 URL scheme，只能走 AppleScript。

它的 ``Info.plist`` 只注册了 ``webcal`` 与 ``ical``（订阅与导入），``calshow:`` 在 macOS 上
根本没注册；真正能定位的入口是脚本字典里的 ``switch view`` 与 ``view calendar at``。
**年视图不在那本字典里**（``view type`` 枚举只有 day / week / month），于是年映射到月。
日期逐项设年月日、时间定在中午——不用 ``date "…"`` 字面量，那跟着系统语言走。
"""

from __future__ import annotations

import datetime as dt
import re

from gallery.core.external.runner import CommandResult

#: 脚本里的应用标识。**用 bundle id 而不是名字**：名字跟着界面语言走，id 不会。
APP_IDENTIFIER = "com.apple.iCal"

#: Gallery 的三层 → 日历的视图。**年没有对应的**：脚本字典里的视图只有日 / 周 / 月，
#: 于是年取最接近的那一层 —— 落在锚点那一天的月。
_VIEWS = {"year": "month view", "month": "month view", "week": "week view"}

#: ``osascript`` 报错末尾那个 ``(-1743)``。
_ERROR_CODE = re.compile(r"\((-?\d+)\)\s*$")

#: 被拒绝的自动化授权。**这是这三条里唯一要用户动手的**，所以给全路径。
_NOT_AUTHORIZED = (
    "未能控制「日历」：请在「系统设置 › 隐私与安全性 › 自动化」里允许 Gallery 控制「日历」"
)


def script(level: str, day: dt.date) -> str:
    """「哪一层 + 哪一天」对应的 AppleScript。

    先把日号归 1 再改年月：否则从 31 日改到只有 30 天的月份会溢出到下一个月。
    时间定在中午，跨夏令时那一天也不会落到前一天或后一天。
    """
    view = _VIEWS.get(level)
    if view is None:
        raise ValueError(f"level 只能是 {tuple(_VIEWS)} 之一，收到 {level!r}")
    return (
        f'tell application id "{APP_IDENTIFIER}"\n'
        "\tset target to current date\n"
        "\tset day of target to 1\n"
        f"\tset year of target to {day.year}\n"
        f"\tset month of target to {day.month}\n"
        f"\tset day of target to {day.day}\n"
        "\tset hours of target to 12\n"
        "\tset minutes of target to 0\n"
        "\tset seconds of target to 0\n"
        f"\tswitch view to {view}\n"
        "\tview calendar at target\n"
        "\tactivate\n"
        "end tell"
    )


def failure_detail(result: CommandResult) -> str:
    """``osascript`` 的报错 → 用户照着能做的一句话（不带句号，横幅会补）。"""
    if result.returncode == -1:
        return "「日历」未响应"
    code = _code_of(result.stderr)
    if code == -1743:
        return _NOT_AUTHORIZED
    if code == -1712:
        return "「日历」未响应"
    if code in (-1728, -10814):
        return "未找到系统「日历」"
    return "未能打开系统「日历」"


def _code_of(stderr: str) -> int | None:
    """从 ``execution error: … (-1743)`` 里取末尾那个错误码；没有就是 ``None``。"""
    match = _ERROR_CODE.search(stderr.strip())
    return int(match.group(1)) if match else None


__all__ = ["APP_IDENTIFIER", "failure_detail", "script"]
