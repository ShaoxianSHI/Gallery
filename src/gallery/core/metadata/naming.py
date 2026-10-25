"""起名字的几条规矩：名字能不能用、能有多长、怎么拆后缀、目标目录里还空着哪个名字。

改名、粘贴、造副本、移动、新建相册共用同一套，**「不覆盖用户已有的文件」这条规矩只有一份实现**。
本模块只回答「这个名字行不行 / 该用哪个」，**不碰磁盘**：:func:`free_name_in` 与
:func:`occupied_names` 只**读**目标目录，从不往用户的文件夹里写东西。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from gallery.core.errors import GalleryError
from gallery.core.i18n import t

#: 单个文件名的字节上限（APFS / HFS+ 都是 255 字节，与字符数无关）。
MAX_NAME_BYTES: Final = 255

#: 改名的编号从 2 起（与访达一致：原名是第一个，``名字 2`` 才是第二个）。
FIRST_DUPLICATE_NUMBER: Final = 2

#: 名字里绝对不能出现的字符：路径分隔符，以及别的平台用的那一个。
_FORBIDDEN_CHARS: Final = ("/", "\\")


def clean_name(name: str, *, error: type[GalleryError]) -> str:
    """把用户输入变成一个可以直接用的文件名；不合法就抛 ``error``。

    **判据只有这一份**：改名（ADR 0004）与新建相册（ADR 0022）都走它，各写一份日后必然漂移。

    :param error: 抛出的异常类型，**由调用方给**（``RenameError`` / ``AlbumError``），判据一致。
    """
    cleaned = name.strip()
    if not cleaned:
        raise error(t("metadata.naming.empty"))
    if cleaned in (".", ".."):
        raise error(t("metadata.naming.reserved", name=cleaned))
    if cleaned.startswith("."):
        raise error(t("metadata.naming.leading_dot"))
    for char in _FORBIDDEN_CHARS:
        if char in cleaned:
            raise error(t("metadata.naming.slash"))
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise error(t("metadata.naming.control"))
    size = len(cleaned.encode("utf-8"))
    if size > MAX_NAME_BYTES:
        raise error(t("metadata.naming.too_long", size=size, limit=MAX_NAME_BYTES))
    return cleaned


def split_suffix(name: str, *, whole: bool = False) -> tuple[str, str]:
    """``一张.jpg`` → ``("一张", ".jpg")``；``京都`` → ``("京都", "")``。
    以点开头且没有第二个点的（``.DS_Store``）整体算名字，不拆后缀。
    :param whole: 整个名字都不拆。**相册用这一档**：访达给同名文件夹起名是在整个名字后加编号
        （``京都.2024`` → ``京都.2024 2``），不是 ``京都 2.2024``。
    """
    if whole:
        return name, ""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, f".{suffix}"


def duplicate_base(name: str, *, whole: bool = False) -> str:
    """副本的**首选名字**：``一张.jpg`` → ``一张的副本.jpg``。
    **这只是首选** —— 撞上同名时照 :func:`free_name_in` 退成 ``一张的副本 2.jpg``。对一个副本再复制
    一次是 ``一张的副本的副本.jpg``：照字面再加一层，而不是把已有的「的副本」剥掉退回编号。
    :param whole: 传给 :func:`split_suffix`（相册那种没有扩展名的，整体算名字）。
    """
    stem, suffix = split_suffix(name, whole=whole)
    return f"{stem}{t('metadata.duplicate_suffix')}{suffix}"


def occupied_names(directory: Path) -> set[str] | None:
    """``directory`` 里已经有的那些名字（一次 ``scandir``），交给 :func:`free_name_in`。

    **批量操作里一个目录只该扫一次。** **读不了时返回 ``None``，不是空集**：空集说「什么都没有」，
    ``None`` 说「不知道」。判错成空集时，加编号的探测会把「原名空着」当成事实，随后收尾那一步可能把
    用户自己同名的文件夹连同里面的照片删掉。
    """
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries}
    except OSError:
        return None


def free_name_in(
    directory: Path, name: str, *, whole: bool = False, occupied: set[str] | None = None
) -> str | None:
    """``directory`` 里**还没有**的那个名字：``一张.jpg`` → ``一张 2.jpg`` → ``一张 3.jpg``。
    这是访达自己的规矩（中文系统实测），**绝不覆盖**已有的东西。:param whole: 传给
    :func:`split_suffix`。:param occupied: 目录里已有的名字；``None`` 就退回逐个 ``exists()``。
    **批量操作必须传**：逐项探测是平方级（500 张移进已有 400 张同名的目录约 20 万次 ``stat``），
    一次 ``scandir`` 后只看表。:return: 空着的名字；``None`` 表示加编号也还是太长。
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


def free_target(
    directory: Path, wanted: str, occupied: set[str] | None = None, *, whole: bool = False
) -> Path | None:
    """挑一个**此刻真的空着**的完整目标路径；加编号也还是太长就 ``None``。
    :func:`free_name_in` 只查 ``occupied`` 那张**一批操作开始时的快照**，而 ``copy2`` 会静默覆盖、
    ``Path.rename`` 在 POSIX 上是原子替换，用户自己那份就这么没了。所以动手前**再看一眼磁盘**，
    撞上了就重新加编号（硬性约束：绝不覆盖用户文件）。
    :param whole: 传给 :func:`free_name_in`（相册那种没有扩展名的，整体算名字）。
    """
    folder = Path(directory)
    while True:
        name = free_name_in(folder, wanted, whole=whole, occupied=occupied)
        if name is None:
            return None
        if occupied is not None:
            occupied.add(name)  # 后一项不许挑到同一个名字
        candidate = folder / name
        if not candidate.exists():
            return candidate


__all__ = [
    "FIRST_DUPLICATE_NUMBER",
    "MAX_NAME_BYTES",
    "clean_name",
    "duplicate_base",
    "free_name_in",
    "free_target",
    "occupied_names",
    "split_suffix",
]
