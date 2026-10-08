"""把照片**移动**进另一个相册，以及把它们挪回来（ADR 0019）。
**只由用户在右键菜单里亲手触发**；**动的是位置不是内容**，同卷内是一次原子 ``rename``；**绝不
覆盖**（撞名按访达规矩另起名字，见 :func:`~gallery.core.metadata.naming.free_name_in`）；目标正是
它所在的相册时复制一份而不是什么都不做（ADR 0044）。**在撤销栈里**：⌘Z 挪回原处 —— 撤销粘贴要删
文件（删除只允许用户亲手触发），移动不删；事件绑定跟着走、撤销也跟回来（ADR 0043 决策 8），跨卷
退化成拷贝 + 删除，**删除只在拷贝成功之后发生**。
"""

from __future__ import annotations

import errno
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from gallery.core import paths
from gallery.core.cancellation import never_cancel
from gallery.core.errors import TransferError
from gallery.core.events.store import EventStore, follow_moves
from gallery.core.metadata.failures import (
    SOURCE_GONE,
    Failure,
    os_reason,
    same_directory,
)
from gallery.core.metadata.journal import (
    ACTION_MOVE,
    ACTION_UNDO,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_target, occupied_names
from gallery.core.metadata.paste import PastedFile, copy_one_into


@dataclass(frozen=True, slots=True)
class MovedItem:
    """移过去的一项：从哪儿来、变成了哪个新位置。"""

    #: 移动**之前**的路径（它已经不在那里了）。
    source: Path
    #: 目标相册里的新路径。
    created: Path

    @property
    def renamed(self) -> bool:
        """是不是为了不覆盖已有的同名而改了名。"""
        return self.source.name != self.created.name


@dataclass(frozen=True, slots=True)
class MoveResult:
    """一次移动的结果。界面据此拼横幅。"""

    #: 真的移过去的（可能为空：全部失败）。
    moved: tuple[MovedItem, ...] = ()
    #: **本来就在那个相册里、因而被复制了一份**的那些（ADR 0044）。源文件原地未动。
    copied: tuple[PastedFile, ...] = ()
    #: 想移没移成的。
    failures: tuple[Failure, ...] = ()
    #: 是否都记下了账。记账失败**不撤销已经移走的文件**（见 ``journal`` 模块文档），
    #: 但界面必须提示用户这一批 ⌘Z 撤不回来。
    journaled: bool = True

    @property
    def did_anything(self) -> bool:
        return bool(self.moved or self.copied)


@dataclass(frozen=True, slots=True)
class UndoMoveResult:
    """撤销一次移动的结果。"""

    #: 挪回原处的（:attr:`MovedItem.source` 是它挪之前在的地方，:attr:`MovedItem.created`
    #: 是它回到的原路径）。
    restored: tuple[MovedItem, ...] = ()
    #: 挪不回来的（已经被移走、原位置被占了……）。
    failures: tuple[Failure, ...] = ()
    journaled: bool = True

    @property
    def ok(self) -> bool:
        return not self.failures


def move_into(
    root: Path,
    target: Path,
    sources: Sequence[Path],
    *,
    should_cancel: Callable[[], bool] | None = None,
    events: EventStore | None = None,
) -> MoveResult:
    """把 ``sources`` **移动**进 ``target``，返回结果。
    :param root: 库根。记账写在它下面（``journal/``）。
    :param should_cancel: 每移一项之前问一次；只在**项与项之间**取消。
    :param events: 事件库（可换绑用）；``None`` 或不可用时跳过那一步。
    :raises TransferError: ``target`` 不是目录，或者它落在库外。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise TransferError(f"「{destination.name}」不是一个相册。")
    _guard(root, destination)

    cancelled = should_cancel or never_cancel
    batch = uuid4().hex[:12]
    moved: list[MovedItem] = []
    copied: list[PastedFile] = []
    failures: list[Failure] = []
    #: 真要跟着搬的绑定：``(旧位置, 新位置, 是不是文件夹)``，累积到整批结束再一次事务搬完。
    follow: list[tuple[Path, Path, bool]] = []
    journaled = True
    # **目录只扫一次**（理由与偏差见 :func:`~gallery.core.metadata.naming.free_name_in`）。
    taken = occupied_names(destination)

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure.at(source, message=SOURCE_GONE))
            continue
        if source.is_dir() and paths.is_within(source, destination):
            failures.append(Failure.at(source, message="不能把相册移进它自己或它自己的子相册。"))
            continue
        if cancelled():
            break
        if same_directory(source.parent, destination):
            # 已经在这一层：移动无从谈起，于是**复制一份**。记账走 ACTION_PASTE 且不进撤销栈
            # —— 源路径原封不动，撤销一次「移动」要么把它挪到它已经在的地方，要么删掉副本。
            outcome = copy_one_into(root, destination, source, batch=batch, taken=taken)
            journaled = journaled and outcome.journaled
            if outcome.item is not None:
                copied.append(outcome.item)
            if outcome.failure is not None:
                failures.append(outcome.failure)
            continue
        created = free_target(destination, source.name, taken)
        if created is None:
            failures.append(Failure.at(source, message="这个名字太长，再加编号也放不下了。"))
            continue
        # **移动之前**问一次：移完之后源路径已经不存在，那时再问恒为假，于是移相册的日志里
        # ``is_dir`` 会记成照片。绑定跟过去同样要用它（相册要按前缀搬整棵子树）。
        is_dir = source.is_dir()
        try:
            _move(source, created)
        except OSError as error:
            message = os_reason(error, action="移动")
            journaled &= append_record(
                root,
                WriteRecord(
                    action=ACTION_MOVE,
                    source=source,
                    target=created,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=is_dir,
                    batch=batch,
                ),
            )
            failures.append(Failure.at(source, message=message))
            continue
        journaled &= append_record(
            root,
            WriteRecord(
                action=ACTION_MOVE,
                source=source,
                target=created,
                result=RESULT_OK,
                is_dir=is_dir,
                batch=batch,
            ),
        )
        moved.append(MovedItem(source=source, created=created))
        follow.append((source, created, is_dir))
    # guard: allow-long-comment（硬性约束：绑定与移动同一事务，漏一次就丢一批绑定）
    # 绑定跟着走，且**一次事务搬完整批**（ADR 0043 决策 8）。只收真的移走的那些 ——
    # 失败的那些路径压根没变，而复制出来的那些是新文件、与绑定无关（ADR 0044）。
    # 监听器随后还会把同一件事报回来一次（``MOVE`` 不做回环屏蔽，新文件显示到界面靠它），
    # 那一次搬不到什么：源路径上已经没有绑定了。
    follow_moves(events, follow)
    return MoveResult(
        moved=tuple(moved),
        copied=tuple(copied),
        failures=tuple(failures),
        journaled=journaled,
    )


def undo_move_records(
    root: Path, records: Sequence[WriteRecord], *, events: EventStore | None = None
) -> UndoMoveResult:
    """把 :mod:`gallery.core.metadata.undo` 挑出来的移动记录挪回原处。

    与 :func:`trash.restore_entries` 对称：逐条还原，取不回来的如实报告。**原位置被占了就不动它** ——
    替用户腾位置等于删掉那个东西。:param events: 撤销一次移动仍是一次移动，绑定跟着搬回去
    （与撤销改名同一套道理）。
    """
    root = Path(root)
    restored: list[MovedItem] = []
    failures: list[Failure] = []
    follow: list[tuple[Path, Path, bool]] = []
    journaled = True
    for record in records:
        current, original = record.target, record.source
        if not current.exists():
            message = f"「{current.name}」已经不在「{current.parent.name}」里了，可能已经被你移走。"
            journaled &= append_record(
                root,
                WriteRecord(
                    action=ACTION_UNDO,
                    source=current,
                    target=original,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=record.is_dir,
                    batch=record.batch,
                ),
            )
            failures.append(Failure.at(current, message=message))
            continue
        if original.exists():
            message = f"「{original.name}」这个位置已经有别的文件了。"
            journaled &= append_record(
                root,
                WriteRecord(
                    action=ACTION_UNDO,
                    source=current,
                    target=original,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=record.is_dir,
                    batch=record.batch,
                ),
            )
            failures.append(Failure.at(current, message=message))
            continue
        try:
            _move(current, original)
        except OSError as error:
            message = os_reason(error, action="移动")
            journaled &= append_record(
                root,
                WriteRecord(
                    action=ACTION_UNDO,
                    source=current,
                    target=original,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=record.is_dir,
                    batch=record.batch,
                ),
            )
            failures.append(Failure.at(current, message=message))
            continue
        journaled &= append_record(
            root,
            WriteRecord(
                action=ACTION_UNDO,
                source=current,
                target=original,
                result=RESULT_OK,
                is_dir=record.is_dir,
                batch=record.batch,
            ),
        )
        restored.append(MovedItem(source=current, created=original))
        follow.append((current, original, record.is_dir))
    follow_moves(events, follow)
    return UndoMoveResult(restored=tuple(restored), failures=tuple(failures), journaled=journaled)


def _move(source: Path, target: Path) -> None:
    """同卷内改名；跨卷才拷贝 + 删除（与 ``core.metadata.trash`` 同一套）。"""
    try:
        source.rename(target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        # shutil.move 自己就是「先拷贝、成功之后再删源」，跨卷失败时源文件分毫未动。
        shutil.move(str(source), str(target))


def _guard(root: Path, destination: Path) -> None:
    """确认目标相册落在允许的范围内。

    **源那一侧不查**（网格选中的照片必然在库内；撤销的目标来自日志的原路径），**目标必须查** ——
    它落到库外就是把照片搬出 Gallery 管得着的地方，没有后续流程能撤回。**库根本身不挡**：要挡的是
    「跑到库外」，不是「落在库里」；配置目录仍挡掉（里面只有 Gallery 自己看得见的缓存）。
    """
    root = Path(root)
    try:
        paths.guard_within_root(root, destination, allow_root=True)
    except paths.GuardError as exc:
        raise TransferError(_transfer_reason(destination, exc), path=destination) from exc


#: 拒绝移动/复制的那几种原因 → 给用户的那句话。见 :data:`rename._GUARD_MESSAGES`。
#: **「库本身」这一条今天到不了**（上面那道检查是 ``allow_root=True``，库根本身是合法目标），
#: 留着是为了这张表是**全的**：``GuardReason`` 加成员时它当场炸，而不是让某天改了
#: ``allow_root`` 的人得到一句含糊的通用话。
_GUARD_MESSAGES: Final = {
    paths.GuardReason.OUTSIDE_ROOT: "只能移到库里的相册：{path}。",
    paths.GuardReason.IS_ROOT: "不能把照片移到库本身，请选一个相册。",
    paths.GuardReason.IS_CONFIG_DIR: "这是 Gallery 自己的配置目录，不能移东西进去。",
}


def _transfer_reason(path: Path, error: paths.GuardError) -> str:
    """按拒绝的**原因**给一句中文 —— 理由见 :func:`rename._rename_reason`。"""
    return _GUARD_MESSAGES[error.reason].format(path=path)


__all__ = [
    "Failure",
    "MoveResult",
    "MovedItem",
    "PastedFile",
    "UndoMoveResult",
    "move_into",
    "undo_move_records",
]
