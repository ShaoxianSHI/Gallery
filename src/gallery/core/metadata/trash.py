"""把照片与相册移进系统废纸篓，以及把它们取回来。

Gallery 语境里的「删除」**不是**销毁文件，而是改名搬家到系统废纸篓（见 ADR 0008）：

- 废纸篓就是访达读的那两个位置（启动卷上的 ``~/.Trash``，别的卷上的
  ``<卷>/.Trashes/<uid>``），所以即使用户没开过 Gallery，照片也还在那儿；
- 同卷内是一次 ``rename``：原子的、瞬时的，不产生第二份数据；
- **本模块没有 ``unlink`` / ``rmtree``**，被删掉的东西永远只是「不在这个相册里了」。

跨卷（照片在别的盘、废纸篓在同一个盘之外）时 ``rename`` 会返回 ``EXDEV``，这时才
退化成拷贝 + 删除：拷贝失败原文件分毫未动，删除只在拷贝成功之后发生。

本模块不弹窗、不问「要不要继续」：被拒绝就抛
:class:`~gallery.core.errors.DeleteError`，由界面决定怎么告诉用户。
"""

from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from gallery.core import paths
from gallery.core.errors import DeleteError
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.failures import Failure, os_reason
from gallery.core.metadata.journal import (
    ACTION_RESTORE,
    ACTION_TRASH,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_name_in

#: 访达用来放「本卷回收站」的目录名（外接盘、U 盘上）。
_VOLUME_TRASH_DIR: Final = ".Trashes"


@dataclass(frozen=True, slots=True)
class TrashResult:
    """一次（可能是批量的）移到废纸篓的结果。"""

    #: 成功移走的条目，仍是它们**原来**的引用（它们已经不在库里了）。
    moved: tuple[GridEntry, ...] = ()
    #: 没能移走的条目与原因。批量删除时部分失败是常态，界面要如实说。
    failures: tuple[Failure, ...] = ()
    #: 是否留下了可回滚的日志记录。为 ``False`` 时界面必须提示「⌘Z 撤销不了」。
    journaled: bool = True

    @property
    def ok(self) -> bool:
        """是不是全部移走了。"""
        return not self.failures


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """一次从废纸篓取回的结果。"""

    #: 取回来的条目，引用已指向它原来的位置。
    restored: tuple[GridEntry, ...] = ()
    #: 取不回来的条目与原因（废纸篓里已经没有它、原位置被占用……）。
    failures: tuple[Failure, ...] = ()
    journaled: bool = True

    @property
    def ok(self) -> bool:
        return not self.failures


def trash_dir_for(path: Path) -> Path:
    """这个位置的「废纸篓」在哪。

    访达把启动卷上的废纸篓放在每个用户自己的 home 里，把别的卷的废纸篓放在卷根下的
    ``.Trashes/<uid>``。跟随同样的规则，移进去的东西访达就认得，用户在访达里也能还原。
    """
    path = Path(path)
    trash_home = Path.home() / ".Trash"
    if _same_volume(path, trash_home.parent):
        return trash_home
    return Path(path.anchor) / _VOLUME_TRASH_DIR / str(os.getuid())


def _same_volume(first: Path, second: Path) -> bool:
    """两个位置是不是在同一个卷上。

    判断不了（断线的外接盘、没权限）时按「是」处理：真到那一步再由移动本身报错，
    总比在这里猜一个卷根路径、然后把文件搬到莫名其妙的地方要好。
    """
    try:
        return first.stat().st_dev == second.stat().st_dev
    except OSError:
        return True


def move_to_trash(entry_path: Path) -> Path:
    """把一个文件或目录移进废纸篓，返回它在废纸篓里的**实际**路径。

    重名时另起一个名字（``照片 2.jpg``），绝不覆盖废纸篓里已有的东西 ——
    那里面可能有用户自己扔进去的、还没还原的东西。

    :raises DeleteError: 废纸篓不可用、系统拒绝了移动。
    """
    source = Path(entry_path)
    is_dir = source.is_dir()
    try:
        directory = trash_dir_for(source)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = _free_name(directory / source.name, is_dir=is_dir)
    except OSError as exc:
        raise DeleteError(
            f"没法使用废纸篓：{os_reason(exc, is_dir=True)}",
            path=source,
            detail=repr(exc),
        ) from exc
    try:
        source.rename(target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise DeleteError(
                f"移不到废纸篓：{os_reason(exc, is_dir=is_dir)}",
                path=source,
                detail=repr(exc),
            ) from exc
        # 跨卷：rename 做不到，退化成拷贝 + 删除。拷贝失败时原文件还在。
        try:
            shutil.move(str(source), str(target))
        except OSError as copy_exc:
            raise DeleteError(
                f"移不到废纸篓：{os_reason(copy_exc, is_dir=is_dir)}",
                path=source,
                detail=repr(copy_exc),
            ) from copy_exc
    return target


def trash_entries(
    root: Path, entries: Iterable[GridEntry], *, batch: str | None = None
) -> TrashResult:
    """把若干条目移进废纸篓，每一项都记一笔账。

    批量删除时**部分失败是常态**（某个文件正被别的程序占用），所以单项失败不抛异常：
    成功与失败一起返回，由界面决定怎么把话说清楚。越权操作（目标是库根、配置目录或库
    外的东西）仍然抛 :class:`~gallery.core.errors.DeleteError`，而且在动任何东西**之前**
    就抛 —— 那是程序缺陷，不是「这个文件动不了」。

    :param batch: 同一次操作里多项共享的编号。``None`` 且一次传多项时自动生成，
        这样一次 ⌘Z 能把整批取回来（见 ``undo.undo_last_write``）。
    """
    root = Path(root)
    items = tuple(entries)
    for entry in items:
        if isinstance(entry, GroupRef):
            continue  # 分组不是磁盘上的东西，没有可移动的路径（下面逐项记成失败）
        _guard(root, Path(entry.path))
    if len(items) > 1 and batch is None:
        batch = _new_batch_id()
    moved: list[GridEntry] = []
    failures: list[Failure] = []
    journaled = True
    for entry in items:
        if isinstance(entry, GroupRef):
            # 界面已经拦过一道；真漏到这里也要**如实说**，不能悄悄少删一个。
            failures.append(
                Failure(
                    # 分组没有路径 —— 它不是磁盘上的东西（photos.GroupRef），移不走。
                    name=entry.name,
                    message="分组不是磁盘上的东西，移不走；双击进入分组后选中里面的照片再删。",
                )
            )
            continue
        source = Path(entry.path)
        is_dir = isinstance(entry, FolderRef)
        try:
            trashed = move_to_trash(source)
        except DeleteError as exc:
            append_record(
                root,
                WriteRecord(
                    action=ACTION_TRASH,
                    source=source,
                    target=source,
                    result=RESULT_FAILED,
                    message=exc.message,
                    is_dir=is_dir,
                    batch=batch,
                ),
            )
            failures.append(Failure.at(source, exc.message))
            continue
        journaled &= append_record(
            root,
            WriteRecord(
                action=ACTION_TRASH,
                source=source,
                target=trashed,
                result=RESULT_OK,
                is_dir=is_dir,
                batch=batch,
            ),
        )
        moved.append(entry)
    return TrashResult(moved=tuple(moved), failures=tuple(failures), journaled=journaled)


def restore_entries(root: Path, records: Sequence[WriteRecord]) -> RestoreResult:
    """把 :mod:`gallery.core.metadata.undo` 挑出来的删除记录取回原处。

    逐条恢复，与 :func:`trash_entries` 对称：取不回来的如实报告，不静默跳过。
    """
    root = Path(root)
    restored: list[GridEntry] = []
    failures: list[Failure] = []
    journaled = True
    for record in records:
        try:
            entry = _restore_one(record)
        except (DeleteError, OSError) as exc:
            message = (
                exc.message
                if isinstance(exc, DeleteError)
                else os_reason(exc, is_dir=record.is_dir)
            )
            journaled &= append_record(
                root,
                WriteRecord(
                    action=ACTION_RESTORE,
                    source=record.target,
                    target=record.source,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=record.is_dir,
                    batch=record.batch,
                ),
            )
            failures.append(Failure.at(record.source, message))
            continue
        journaled &= append_record(
            root,
            WriteRecord(
                action=ACTION_RESTORE,
                source=record.target,
                target=record.source,
                result=RESULT_OK,
                is_dir=record.is_dir,
                batch=record.batch,
            ),
        )
        restored.append(entry)
    return RestoreResult(restored=tuple(restored), failures=tuple(failures), journaled=journaled)


def _restore_one(record: WriteRecord) -> GridEntry:
    """把一条删除记录取回来。引用要在移动**之前**取：之后原路径上已经没有文件了。"""
    original, trashed = record.source, record.target
    if not trashed.exists():
        raise DeleteError(
            f"废纸篓里已经找不到「{original.name}」了，它可能已经被清空，或者你在访达里还原过。",
            path=original,
        )
    if original.exists():
        raise DeleteError(
            f"「{original.name}」这个位置已经有别的文件了。",
            path=original,
        )
    # 移动不改内容也不改时间：先从废纸篓里把引用读出来，再让它指回原路径。
    moved = FolderRef.from_path(trashed) if record.is_dir else PhotoRef.from_path(trashed)
    _move(trashed, original)
    if isinstance(moved, FolderRef):
        return FolderRef(path=original, mtime_ns=moved.mtime_ns)
    return PhotoRef(path=original, size=moved.size, mtime_ns=moved.mtime_ns)


def _move(source: Path, target: Path) -> None:
    """同卷内改名；跨卷才拷贝 + 删除。"""
    is_dir = source.is_dir()
    try:
        source.rename(target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise DeleteError(
                os_reason(exc, is_dir=is_dir),
                path=source,
                detail=repr(exc),
            ) from exc
        try:
            shutil.move(str(source), str(target))
        except OSError as move_exc:
            raise DeleteError(
                os_reason(move_exc, is_dir=is_dir),
                path=source,
                detail=repr(move_exc),
            ) from move_exc


def _guard(root: Path, source: Path) -> None:
    """确认要移走的东西在允许的范围内。

    网格里根本不会出现库根和配置目录，这里仍然挡一道：这是**会让用户的文件离开原处的
    入口之一**（粘贴 / 移动到相册同样会，各有一份同名 ``_guard``），值得对「库根」
    「配置目录」多一层保险。
    """
    root = Path(root)
    try:
        paths.guard_within_root(root, source, allow_root=False)
    except ValueError as exc:
        raise DeleteError(_trash_reason(source, str(exc)), path=source) from exc


def _trash_reason(path: Path, reason: str) -> str:
    """按闸门说的那一件事给一句中文 —— 理由见 :func:`rename._rename_reason`。"""
    if "配置目录" in reason:
        return "这是 Gallery 自己的配置目录，不能删。"
    if "库本身" in reason:
        return "这是库本身，删掉它 Gallery 就找不到任何照片了。"
    return f"只能移除库里的东西：{path}。"


def _free_name(candidate: Path, *, is_dir: bool) -> Path:
    """废纸篓里已经有同名东西时另起一个名字，绝不覆盖。

    **「不覆盖」这条规矩归 :mod:`gallery.core.metadata.naming` 管**（它的模块文档
    写着「只有一份实现，否则日后三份会各自漂移」），这里只交代本地的一件事：
    目录**没有扩展名可拆**，所以整体算名字（``京都.2024`` → ``京都.2024 2``）。
    曾经这里自己写了一遍加编号的循环，于是它已经与粘贴那套漂开了：数到 999 就
    放弃、也压根不管 255 字节的上限 —— 超长的名字要走到 ``rename`` 才炸，
    报一句与真正原因无关的话。
    """
    if not candidate.exists():
        return candidate
    free = free_name_in(candidate.parent, candidate.name, whole=is_dir)
    if free is None:
        raise DeleteError(
            f"「{candidate.name}」这个名字太长，废纸篓里加编号也放不下了。",
            path=candidate,
        )
    return candidate.parent / free


def _new_batch_id() -> str:
    """一次批量操作的编号。重放日志时靠它把一批记录归成一个撤销单元。"""
    return uuid4().hex[:12]


__all__ = [
    "Failure",
    "RestoreResult",
    "TrashResult",
    "move_to_trash",
    "restore_entries",
    "trash_dir_for",
    "trash_entries",
]
