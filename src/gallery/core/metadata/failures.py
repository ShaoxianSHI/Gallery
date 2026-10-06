"""一次批量操作里，没做成的那些条目 —— **三个模块共用一份记录与一套说法**。

``paste`` / ``transfer`` / ``trash`` 都会逐项往下走，个别项因为「磁盘满了」「没权限」
「东西已经不在了」而失败。它们的收尾结构一模一样：一串失败项 + 一个 ``journaled``
标志，界面也用同一套办法拼横幅（``main_window._first_reason``）。

于是这里收着两样过去各写各的东西：

- :class:`Failure` —— 失败项的记录。过去有三个同名同义的类，而形状**并不相同**：
  ``trash`` 的那份只有 ``name``、``transfer`` 的那份多一个 ``source`` 并把 ``name``
  做成派生属性、``paste`` 的那份两个都没有。界面因此得按来源分别 import、分别处理，
  ``_first_reason`` 拼横幅时还得先把两种形状凑成一样才能读同一个 ``.name``。
  现在**只有一个形状**，且 ``name`` 一定在那儿（界面唯一要用的就是它）。

  ``source`` 是可选的，因为**分组卡片不是磁盘上的东西**（``photos.GroupRef`` 没有
  路径，也移不走）。与其为了凑一个假路径，不如让「没有路径」是一个说出来的事实。

- :func:`os_reason` —— 把一个 ``OSError`` 翻译成一句用户看得懂、且指向下一步的话。
  过去也是三份，而其中**真正该统一的那三样**（磁盘满了 / 东西没了 / 没权限）
  在三处各说各的：磁盘满有「磁盘空间不足」也有「磁盘满了」。那三样说的是同一件事，
  就不该有三套措辞。
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


#: 「源已经不在原来的位置了」那一句。它被两处用：``os_reason`` 的 ``ENOENT`` 分支，
#: 以及 ``transfer`` 在动手前发现源压根不存在时 —— 后者没有 ``OSError`` 可译，
#: 于是引用同一句，而不是另抄一份（``tests/core/metadata/test_failures.py`` 钉住两边一致）。
SOURCE_GONE = "它已经不在原来的位置了，可能刚刚被别的东西移走了。"


def os_reason(exc: OSError, *, is_dir: bool = False, action: str | None = None) -> str:
    """把系统错误翻译成一句用户看得懂、且**指向下一步**的话。

    五种情况全仓库统一说法 —— 它们说的是同一件事，不该因为撞上哪个操作就换个措辞：

    - **磁盘满**（``ENOSPC``）：只有一条路，清理空间。
    - **东西不在了**（``ENOENT``）：多半是同一件事在另一个程序里刚发生。
    - **没权限**（``EACCES`` / ``EPERM``）：分「文件」与「文件夹」两种提示 ——
      一个只读的文件和一个读不了的文件夹，用户下一步要做的事不一样。
    - **名字太长**（``ENAMETOOLONG``）：换个短名字，没有别的办法。
    - **源和目标是同一个**（``shutil.SameFileError``）：这是程序自己的判断，
      不是系统拒绝的，所以单独一条。

    判据既认 ``errno`` 也认系统原话里那几个词 —— macOS 与 Linux 的部分错误不带
    ``errno``（而 ``strerror`` 会本地化成中文），只认一种会漏。

    其余一律带上系统的原话（``strerror``），因为那才是定位问题真正要的信息；
    「它可能正在被其他程序使用」只是最常见的一种可能，不能当成结论写给用户。

    :param is_dir: 出错的是不是一个文件夹（只影响没权限那一条的提示）。
    :param action: 操作名（「复制」「移动」）。**只加在「说不清原因」那一条上** ——
        前五种各自已经把原因说清楚了，再加个前缀只是啰嗦（「复制失败：磁盘满了」）。
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
        return f"没有权限（{reason}）。{hint}"
    if exc.errno == errno.ENAMETOOLONG or "File name too long" in reason or "文件名太长" in reason:
        return "名字太长，这个磁盘放不下。"
    prefix = f"{action}失败：" if action else ""
    return f"{prefix}{reason}。它可能正在被其他程序使用。"


def same_directory(left: Path, right: Path) -> bool:
    """两个路径是不是同一个文件夹 —— **按磁盘上的真实位置**比，不比字面写法。

    访达的剪贴板里可能是 ``/private/var/…`` 这样的写法，而界面上拿到的是 ``/var/…``；
    不解析就比不相等，于是「已经在同一目录了」这条判断会失效（粘贴会粘出一堆重复
    文件，「移动」会把照片原地又移一次、平白多出一份「一张 2.jpg」）。

    **走 :func:`~gallery.core.paths.resolved_str` 而不是裸的 ``resolve()``**：粘贴与移动
    都在逐项循环里问这一条，而右侧那个目标目录整场循环不变 —— 同一个问题问几百遍，
    而 ``resolve()`` 每次都要 ``lstat`` 路径上的每一段（实测十二段的库一次约 19 µs）。
    """
    from gallery.core.paths import resolved_str

    return resolved_str(left) == resolved_str(right)


__all__ = ["SOURCE_GONE", "Failure", "os_reason", "same_directory"]
