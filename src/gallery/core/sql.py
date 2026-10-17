"""SQL 这一层跨领域共享的两件小事：``LIKE`` 的转义，与「这次失败值得重开连接再试吗」的判据。

**为什么单独一个顶层模块**：它跨两个领域包 —— ``core.library.search``（检索词）与
``core.events.store``（移动绑定时拼路径前缀）。两者不能互相 import：``search`` 已经在
import ``events.store`` 的 schema 常量，反过来再 import 就是环。判据与 ``ui/outline.py``
相同：改了它会不会牵连另一个包，这里正是会。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Final

#: ``LIKE`` 用的转义符。**反斜杠自己也要转义**，所以替换顺序是先它。
ESCAPE: Final = "\\"

#: 「把连接重开一次再试一遍」值得的那几个错误码。
#:
#: 前两个是库文件被换掉的形状（云盘同步、编辑器另存）：SQLite 会按**你我打开的那一份已经不是
#: 磁盘上这一份**报 ``attempt to write a readonly database`` —— 主码 ``SQLITE_READONLY``，
#: 文件名换掉时是扩展码 ``SQLITE_READONLY_DBMOVED``。后两个是两条连接抢同一份库。
RETRYABLE_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_READONLY,
        sqlite3.SQLITE_READONLY_DBMOVED,
    }
)


def is_stale_or_locked(exc: sqlite3.Error) -> bool:
    """这次失败是不是「连接底下的库文件不对了 / 被别的连接占住」—— 重开一次再试值得。

    **只认错误码，不认文案**：文案随 SQLite 版本与语言环境变，拿它当判据迟早失效。
    """
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and code in RETRYABLE_CODES


def file_identity(path: Path) -> tuple[int, int] | None:
    """这份文件的身份（``st_dev`` + ``st_ino``）；读不到（不见了、没权限）返回 ``None``。

    云盘与「另存为」都是**换一份同名的文件**：路径一样、内容一样看着也一样，只有身份变了。
    库文件被换掉之后 SQLite 那条连接写不进去、读还给旧内容，判据就是它（ADR 0064）。
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def like_pattern(text: str, tail: str = "") -> str:
    """把一段文字转成 ``LIKE … ESCAPE '\\'`` 的模式，可接一段**不转义**的 ``tail``。

    ``tail`` 给「前缀 + 目录分隔符」那种查询用（``like_pattern(prefix, "/%")``）：前缀要转义，
    后面那个通配符是查询本身要的。**下划线必须转义**：``IMG_0001`` 太常见，不转的话 ``_``
    会顶替任何字符，「相册 A」会顺手匹配上「相册 B」；``%`` 同理。
    """
    escaped = text.replace(ESCAPE, ESCAPE * 2)
    for character in ("%", "_"):
        escaped = escaped.replace(character, ESCAPE + character)
    return f"{escaped}{tail}"
