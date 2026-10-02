"""起名字的三条规矩：名字能有多长、怎么拆后缀、目标目录里还空着哪个名字。

这三个函数原先散在两个写操作里，各写一份：改名要拿 :data:`MAX_NAME_BYTES` 判合法性，
粘贴要在目标目录里找一个没被占用的名字（``一张.jpg`` → ``一张 2.jpg``）。现在移动到相册
（:mod:`~gallery.core.metadata.transfer`）也要用同一套，于是收到这里 —— **「不覆盖用户
已有的文件」这条规矩只有一份实现**，否则日后三份会各自漂移。

本模块只回答「这个名字行不行 / 该用哪个」，**不碰磁盘**：唯一的例外是
:func:`free_name_in`，它要 ``exists()`` 看一下目标目录（只读）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: 单个文件名的字节上限（APFS / HFS+ 都是 255 字节，与字符数无关）。
MAX_NAME_BYTES: Final = 255

#: 改名的编号从 2 起（与访达一致：原名是第一个，``名字 2`` 才是第二个）。
FIRST_DUPLICATE_NUMBER: Final = 2


def split_suffix(name: str) -> tuple[str, str]:
    """``一张.jpg`` → ``("一张", ".jpg")``；``京都`` → ``("京都", "")``。

    以点开头且没有第二个点的（``.DS_Store`` 这种隐藏文件）整体算名字，不拆后缀。
    """
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, f".{suffix}"


def free_name_in(directory: Path, name: str) -> str | None:
    """``directory`` 里**还没有**的那个名字：``一张.jpg`` → ``一张 2.jpg`` → ``一张 3.jpg``。

    这是访达自己的规矩，实测得来：在访达里对同一个文件连按两次「复制项目」，中文系统
    给出的正是 ``一张 2.jpg`` / ``一张 3.jpg``，文件夹同理（``京都`` → ``京都 2``）。
    Gallery 的粘贴与移动都照它来，用户不用换一套心智模型。

    **绝不覆盖**已有的东西：那些可能是用户自己放进去的、还没处理的文件。

    :return: 空着的那个名字；``None`` 表示「加编号也还是太长」—— 光靠加数字缩短不了
        名字，那就如实说不行，而不是截断用户文件名（那会造出一个他认不出的照片）。
    """
    folder = Path(directory)
    if not (folder / name).exists():
        return name
    stem, suffix = split_suffix(name)
    number = FIRST_DUPLICATE_NUMBER
    while True:
        candidate = f"{stem} {number}{suffix}"
        if len(candidate.encode()) > MAX_NAME_BYTES:
            return None
        if not (folder / candidate).exists():
            return candidate
        number += 1


__all__ = ["FIRST_DUPLICATE_NUMBER", "MAX_NAME_BYTES", "free_name_in", "split_suffix"]
