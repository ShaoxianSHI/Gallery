"""把一段字面量安全地放进 SQL 的 ``LIKE``。

**为什么单独一个顶层模块**：它跨两个领域包 —— ``core.library.search``（用户输入的检索词）
与 ``core.events.store``（移动事件绑定时拼路径前缀）。两者不能互相 import：
``search`` 已经在 import ``events.store`` 的 schema 常量，反过来再 import 就是环。
判据与 ``ui/outline.py`` 相同：改了它会不会牵连另一个包，这里正是会。
"""

from __future__ import annotations

from typing import Final

#: ``LIKE`` 用的转义符。**反斜杠自己也要转义**，所以替换顺序是先它。
ESCAPE: Final = "\\"


def like_pattern(text: str, tail: str = "") -> str:
    """把一段文字转成 ``LIKE … ESCAPE '\\'`` 的模式，可接一段**不转义**的 ``tail``。

    ``tail`` 是给「前缀 + 目录分隔符」那种查询用的（``like_pattern(prefix, "/%")``）：
    前缀来自用户路径要转义，而后面那个通配符是查询本身要的，不能被转义掉。

    **下划线必须转义**：文件名里 ``IMG_0001`` 太常见，不转的话 ``_`` 会顶替任何字符，
    「相册 A」那一条查询会顺手匹配上「相册 B」。``%`` 同理。
    """
    escaped = text.replace(ESCAPE, ESCAPE * 2)
    for character in ("%", "_"):
        escaped = escaped.replace(character, ESCAPE + character)
    return f"{escaped}{tail}"
