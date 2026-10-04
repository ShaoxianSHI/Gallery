"""起名字的几条规矩：名字能不能用、能有多长、怎么拆后缀、目标目录里还空着哪个名字。

这些函数原先散在几个写操作里，各写一份：改名要判名字合法性（要拿
:data:`MAX_NAME_BYTES`），粘贴要在目标目录里找一个没被占用的名字（``一张.jpg`` →
``一张 2.jpg``）。现在移动到相册（:mod:`~gallery.core.metadata.transfer`）与新建相册
（:mod:`~gallery.core.metadata.album`）也要用同一套，于是收到这里 ——
**「不覆盖用户已有的文件」这条规矩只有一份实现**，否则日后三份会各自漂移。

本模块只回答「这个名字行不行 / 该用哪个」，**不碰磁盘**：唯一的例外是
:func:`free_name_in` 与 :func:`occupied_names`，它们只**读**目标目录（``exists()``
或一次 ``scandir``），从不往用户的文件夹里写任何东西。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from gallery.core.errors import GalleryError

#: 单个文件名的字节上限（APFS / HFS+ 都是 255 字节，与字符数无关）。
MAX_NAME_BYTES: Final = 255

#: 改名的编号从 2 起（与访达一致：原名是第一个，``名字 2`` 才是第二个）。
FIRST_DUPLICATE_NUMBER: Final = 2

#: 名字里绝对不能出现的字符：路径分隔符，以及别的平台用的那一个。
_FORBIDDEN_CHARS: Final = ("/", "\\")


def clean_name(name: str, *, error: type[GalleryError]) -> str:
    """把用户输入变成一个可以直接用的文件名；不合法就抛 ``error``。

    **判据只有这一份**：改名（ADR 0004）与新建相册（ADR 0022）都要问同一串问题，
    两边各写一份的话日后必然漂移 —— 而漂移出来的后果是「同一个名字在两条路上给出
    两种说法」，用户只会认为是程序有毛病。

    :param error: 抛出的异常类型，**由调用方给**：两个入口的失败类型不同
        （``RenameError`` 与 ``AlbumError``），但判据与文案必须一模一样。
    """
    cleaned = name.strip()
    if not cleaned:
        raise error("新名字不能为空。")
    if cleaned in (".", ".."):
        raise error(f"「{cleaned}」是保留名字，不能用。")
    if cleaned.startswith("."):
        raise error("名字不能以「.」开头：以点开头的文件不会出现在 Gallery 里。")
    for char in _FORBIDDEN_CHARS:
        if char in cleaned:
            raise error("名字里不能带「/」：改名只改名字，不会移动文件。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise error("名字里不能有控制字符（回车、Tab 等）。")
    size = len(cleaned.encode("utf-8"))
    if size > MAX_NAME_BYTES:
        raise error(f"名字太长了（{size} 字节）：单个文件名最多 {MAX_NAME_BYTES} 字节。")
    return cleaned


def split_suffix(name: str, *, whole: bool = False) -> tuple[str, str]:
    """``一张.jpg`` → ``("一张", ".jpg")``；``京都`` → ``("京都", "")``。

    以点开头且没有第二个点的（``.DS_Store`` 这种隐藏文件）整体算名字，不拆后缀。

    :param whole: 整个名字都不拆（``京都.2024`` → ``("京都.2024", "")``）。
        **相册用这一档**：文件夹没有扩展名，访达给同名文件夹起名时是在整个名字
        后面加编号（``京都.2024`` → ``京都.2024 2``），不是 ``京都 2.2024``。
    """
    if whole:
        return name, ""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, f".{suffix}"


def occupied_names(directory: Path) -> set[str]:
    """``directory`` 里已经有的那些名字（一次 ``scandir``）。

    交给 :func:`free_name_in` 的 ``occupied`` 用。存在的理由只有一个：
    **批量操作里，一个目录只该扫一次。** 见 :func:`free_name_in` 的说明。
    """
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries}
    except OSError:
        # 目录读不了就当作空的：接下来 ``free_name_in`` 会退回去真的去 ``exists()``，
        # 于是顶多多撞一次上而已，不会覆盖用户已有的东西。
        return set()


def free_name_in(
    directory: Path, name: str, *, whole: bool = False, occupied: set[str] | None = None
) -> str | None:
    """``directory`` 里**还没有**的那个名字：``一张.jpg`` → ``一张 2.jpg`` → ``一张 3.jpg``。

    这是访达自己的规矩，实测得来：在访达里对同一个文件连按两次「复制项目」，中文系统
    给出的正是 ``一张 2.jpg`` / ``一张 3.jpg``，文件夹同理（``京都`` → ``京都 2``）。
    Gallery 的粘贴、移动到相册与移进废纸篓都照它来，用户不用换一套心智模型。

    **绝不覆盖**已有的东西：那些可能是用户自己放进去的、还没处理的文件。

    :param whole: 传给 :func:`split_suffix`（相册那种没有扩展名的，整体算名字）。
    :param occupied: 这个目录里**已经有的名字**（见 :func:`occupied_names`）。
        给了它就纯查表，一次系统调用都不发；不给就退回逐个 ``exists()``。
        **批量操作必须传** —— 见下面那段说明。
    :return: 空着的那个名字；``None`` 表示「加编号也还是太长」—— 光靠加数字缩短不了
        名字，那就如实说不行，而不是截断用户文件名（那会造出一个他认不出的照片）。

    **为什么要多这个口。** 加编号的探测是一条「撞一次就 ``stat`` 一次」的循环，而
    粘贴 / 移动 / 移进废纸篓都是**逐项**调用它的：把 500 张同名照片移进一个已经有
    400 张同名照片的相册，探测次数是 500 × 400 ≈ 20 万次 ``stat`` —— **平方级**，
    而且这恰好是「批量整理」这件事最容易碰到的形状。改成一次 ``scandir`` 之后
    同样的操作是「一次目录扫描 + 500 次查表」。

    **调用方要把选中的名字加回 ``occupied``**，否则后一项会挑到同一个名字。有一处
    刻意的偏差：复制 / 移动**失败**之后那个名字仍然留在集合里，于是下一项会跳过
    一个其实空着的编号（拿到「一张 3」而不是「一张 2」）。宁可跳过一个编号 ——
    它的另一头是「覆盖用户已有的文件」，那条绝对不能出。
    """
    folder = Path(directory)

    def taken(candidate: str) -> bool:
        if occupied is not None:
            return candidate in occupied
        return (folder / candidate).exists()

    if not taken(name):
        return name
    stem, suffix = split_suffix(name, whole=whole)
    number = FIRST_DUPLICATE_NUMBER
    while True:
        candidate = f"{stem} {number}{suffix}"
        if len(candidate.encode()) > MAX_NAME_BYTES:
            return None
        if not taken(candidate):
            return candidate
        number += 1


__all__ = [
    "FIRST_DUPLICATE_NUMBER",
    "MAX_NAME_BYTES",
    "clean_name",
    "free_name_in",
    "occupied_names",
    "split_suffix",
]
