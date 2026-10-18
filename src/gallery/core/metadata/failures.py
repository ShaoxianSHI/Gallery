"""一次批量操作里没做成的条目 —— **``paste`` / ``transfer`` / ``trash`` 共用一份记录与说法**。

三者的收尾结构一样：一串失败项 + 一个 ``journaled`` 标志，界面也用同一套办法拼横幅。
:class:`Failure` 只有一种形状，``name`` 一定在；``source`` 可选，因为分组卡片不是磁盘上的东西。
:func:`os_reason` 把 ``OSError`` 翻译成指向下一步的话，磁盘满了 / 东西没了 / 没权限三样说同一件事。
"""

from __future__ import annotations

import errno
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Failure:
    """一项没成的操作，连同给用户看的原因。"""

    #: 网格里显示的那一个名字 —— 横幅上点名的就是它（「一张.jpg：磁盘满了。」）。
    name: str
    #: 原因（中文，能指向下一步动作）。
    message: str
    #: 出错的那个条目在磁盘上的位置。分组卡片没有（它不是磁盘上的东西），于是是 ``None``。
    source: Path | None = None

    @classmethod
    def at(cls, source: Path, message: str) -> Failure:
        """出错的是磁盘上的一个条目 —— 磁盘上发生的事，九成是这种情况。

        用它而不是直接 ``Failure(source=…, message=…)``：``name`` 是从 ``source``
        推出来的，**不该有人手写**（手写就会写出与实际文件名不一致的那个）。
        """
        return cls(name=source.name, message=message, source=source)


#: 「源已经不在原来的位置了」那一句。``os_reason`` 的 ``ENOENT`` 分支与 ``transfer`` 动手前
#: 发现源不存在时引用同一句，而不是各抄一份（测试锁定两边一致）。
SOURCE_GONE = "它已经不在原来的位置了，可能刚刚被别的东西移走了。"


def os_reason(exc: OSError, *, is_dir: bool = False, action: str | None = None) -> str:
    """把系统错误翻译成一句用户看得懂、且**指向下一步**的话。

    五种情况全仓库统一说法：磁盘满、东西不在了、没权限（分文件与文件夹）、名字太长、源与目标是
    同一个。判据既认 ``errno`` 也认系统原话里那几个词（macOS / Linux 的部分错误不带 ``errno``）；
    其余带上 ``strerror``。:param is_dir: 只影响没权限那一条。:param action: 只加在说不清原因时。
    """
    reason = exc.strerror or str(exc) or exc.__class__.__name__
    if isinstance(exc, shutil.SameFileError):
        return "源和目标是同一个东西。"
    if exc.errno == errno.ENOSPC or "No space left" in reason or "磁盘空间不足" in reason:
        return "磁盘满了，腾出点空间再试。"
    if exc.errno == errno.ENOENT or "No such file" in reason:
        return SOURCE_GONE
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
        hint = (
            "请检查「系统设置 → 隐私与安全性 → 文件与文件夹」里有没有授权。"
            if is_dir
            else "请检查它是不是只读，或者正被别的程序打开着。"
        )
        return f"没有权限，系统返回 {reason}。{hint}"
    if exc.errno == errno.ENAMETOOLONG or "File name too long" in reason or "文件名太长" in reason:
        return "名字太长，这个磁盘放不下。"
    prefix = f"{action}失败：" if action else ""
    return f"{prefix}{reason}。它可能正在被其他程序使用。"


def same_directory(left: Path, right: Path) -> bool:
    """两个路径是不是同一个文件夹 —— **按磁盘上的真实位置**比，不比字面写法。

    访达剪贴板可能是 ``/private/var/…`` 而界面拿到 ``/var/…``，不解析就比不相等，「已经在同一目录」
    的判断会失效。走 :func:`~gallery.core.paths.resolved_str` 而不是裸 ``resolve()``：粘贴与移动在
    逐项循环里问这一条，而目标目录整场不变 —— ``resolve()`` 每次都要 ``lstat`` 每一段（实测 19µs）。
    """
    from gallery.core.paths import resolved_str

    return resolved_str(left) == resolved_str(right)


__all__ = ["SOURCE_GONE", "Failure", "os_reason", "same_directory"]
