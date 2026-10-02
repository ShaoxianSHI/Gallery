"""把照片**移动**进另一个相册，以及把它们挪回来（界面上是「移动到相册」，见 ADR 0019）。

这是本项目里第四个会改动用户文件的操作，也是**第一个会让用户的文件离开原处**的
（前三样见 ``core.metadata`` 的包文档：改名、移到废纸篓、粘贴）。红线 1 写的是
「绝不删除、移动用户照片」，因此这一条比前三样都更需要一个站得住的理由：

- **只在界面上由用户亲手触发**：入口是照片右键菜单里的「移动到相册」，点了才会动，
  没有任何后台整理、批量脚本或「顺便做一下」（红线 1 排除的正是那些）。
- **动的是位置，不是内容**：像素、EXIF、修改时间一个字节都不改。同卷内那是一次
  ``rename``：原子的、瞬时的，不产生第二份数据。
- **绝不覆盖**：目标相册里已有同名时按访达的规矩另起一个名字（``一张 2.jpg``，
  见 :func:`~gallery.core.metadata.naming.free_name_in`），而不是让后来的那张凭空消失。
- **进撤销栈**：⌘Z 把它挪回原来的相册（:func:`undo_move_records`）。这是它与「粘贴」
  最大的区别 —— 撤销一次粘贴是删文件（红线 1 不允许），而**撤销一次移动仍然是一次
  移动**：东西一直都在，只是回到了它原来待着的地方。
- **每一项都记账**（:data:`~gallery.core.metadata.journal.ACTION_MOVE`），与另外三个
  写操作同一处；一次操作里的多项共用一个 ``batch``，于是一次 ⌘Z 撤一整批。

跨卷（库在外接盘上，或相册横跨两个卷）时 ``rename`` 会返回 ``EXDEV``，这时才退化成
拷贝 + 删除，且**删除只在拷贝成功之后发生** —— 与 ``core.metadata.trash`` 是同一套做法。

本模块不弹窗、不问「要不要继续」：成与不成都落在返回值里（:class:`MoveResult`），
由界面决定怎么把话说清楚。只有「目标压根不是一个文件夹」这种调用方的错才抛
:class:`~gallery.core.errors.TransferError`。
"""

from __future__ import annotations

import errno
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from gallery.core import paths
from gallery.core.errors import TransferError
from gallery.core.metadata.journal import (
    ACTION_MOVE,
    ACTION_UNDO,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_name_in


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
class Skipped:
    """没有移的一项，以及**为什么**（这句直接给用户看）。"""

    source: Path
    message: str


@dataclass(frozen=True, slots=True)
class Failure:
    """想移没移成的一项，以及失败的原因。"""

    source: Path
    message: str

    @property
    def name(self) -> str:
        """出问题的那个名字。

        与 :class:`gallery.core.metadata.trash.Failure` 的 ``name`` 同名同义，界面拼横幅
        时可以把两种失败一视同仁（``main_window._first_reason``）。
        """
        return self.source.name


@dataclass(frozen=True, slots=True)
class MoveResult:
    """一次移动的结果。三类分开摆，界面据此拼横幅。"""

    #: 真的移过去的（可能为空：全部跳过或全部失败）。
    moved: tuple[MovedItem, ...] = ()
    #: 刻意没移的（本来就在那个相册里、文件夹移进它自己的子目录）。
    skipped: tuple[Skipped, ...] = ()
    #: 想移没移成的。
    failures: tuple[Failure, ...] = ()
    #: 是否都记下了账。记账失败**不撤销已经移走的文件**（见 ``journal`` 模块文档），
    #: 但界面必须提示用户这一批 ⌘Z 撤不回来。
    journaled: bool = True

    @property
    def did_anything(self) -> bool:
        return bool(self.moved)


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
) -> MoveResult:
    """把 ``sources`` **移动**进 ``target``，返回结果。

    :param root: 库根。记账写在它下面（``journal/``），与另外三个写操作同一处。
    :param target: 目标相册，必须已经存在且是个目录。
    :param sources: 要移动的路径。界面上给的是网格里选中的照片；理论上也接受文件夹。
    :param should_cancel: 每移一项之前问一次。只在**项与项之间**取消：移一半停下来会让
        用户自己都说不清哪些被移了，那比移完更糟。
    :raises TransferError: ``target`` 不是目录。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise TransferError(f"「{destination.name}」不是一个相册。")

    cancelled = should_cancel if should_cancel is not None else _never_cancel
    batch = uuid4().hex[:12]
    moved: list[MovedItem] = []
    skipped: list[Skipped] = []
    failures: list[Failure] = []
    journaled = True

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure(source=source, message="它已经不在原来的位置了。"))
            continue
        if _same_directory(source.parent, destination):
            skipped.append(Skipped(source=source, message=f"它已经在「{destination.name}」里了。"))
            continue
        if source.is_dir() and _is_within(source, destination):
            failures.append(
                Failure(source=source, message="不能把相册移进它自己或它自己的子相册。")
            )
            continue
        if cancelled():
            break
        name = free_name_in(destination, source.name)
        if name is None:
            failures.append(Failure(source=source, message="这个名字太长，再加编号也放不下了。"))
            continue
        created = destination / name
        try:
            _move(source, created)
        except OSError as error:
            message = _move_error_message(error)
            journaled &= _log(
                root,
                WriteRecord(
                    action=ACTION_MOVE,
                    source=source,
                    target=created,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=source.is_dir(),
                    batch=batch,
                ),
            )
            failures.append(Failure(source=source, message=message))
            continue
        journaled &= _log(
            root,
            WriteRecord(
                action=ACTION_MOVE,
                source=source,
                target=created,
                result=RESULT_OK,
                is_dir=source.is_dir(),
                batch=batch,
            ),
        )
        moved.append(MovedItem(source=source, created=created))
    return MoveResult(
        moved=tuple(moved),
        skipped=tuple(skipped),
        failures=tuple(failures),
        journaled=journaled,
    )


def undo_move_records(root: Path, records: Sequence[WriteRecord]) -> UndoMoveResult:
    """把 :mod:`gallery.core.metadata.undo` 挑出来的移动记录挪回原处。

    与 :func:`trash.restore_entries` 对称：逐条还原，取不回来的如实报告，不静默跳过。

    **原位置被占了就不动它**（与取回废纸篓同一个取向）：用户在这期间自己放了东西进去，
    替他腾出位置等于删掉那个东西 —— 那需要用户亲手做。
    """
    root = Path(root)
    restored: list[MovedItem] = []
    failures: list[Failure] = []
    journaled = True
    for record in records:
        current, original = record.target, record.source
        if not current.exists():
            message = f"「{current.name}」已经不在「{current.parent.name}」里了，可能已经被你移走。"
            journaled &= _log(
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
            failures.append(Failure(source=current, message=message))
            continue
        if original.exists():
            message = f"「{original.name}」这个位置已经有别的文件了。"
            journaled &= _log(
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
            failures.append(Failure(source=current, message=message))
            continue
        try:
            _move(current, original)
        except OSError as error:
            message = _move_error_message(error)
            journaled &= _log(
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
            failures.append(Failure(source=current, message=message))
            continue
        journaled &= _log(
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


def _move_error_message(error: OSError) -> str:
    """一句说清为什么没移成的中文。"""
    reason = error.strerror or str(error)
    if error.errno == errno.ENOSPC or "No space left" in reason or "磁盘空间不足" in reason:
        return "磁盘空间不足。"
    if error.errno in (errno.EACCES, errno.EPERM) or isinstance(error, PermissionError):
        return "没有权限写进这个相册。"
    if error.errno == errno.ENOENT:
        return "它已经不在原来的位置了。"
    return f"移动失败：{reason}。"


def _same_directory(left: Path, right: Path) -> bool:
    """两个路径是不是同一个文件夹（**按磁盘上的真实位置**比，不比字面写法）。

    与 ``paste._same_directory`` 同理：界面上拿到的是 ``/var/…``，别处报来的是
    ``/private/var/…``，不解析就比不相等，于是「它已经在那个相册里了」判不出来，
    会把照片原地又「移动」一次（平白多出一份「一张 2.jpg」）。
    """
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _is_within(outer: Path, inner: Path) -> bool:
    return paths.is_within(outer.resolve(), inner.resolve())


def _never_cancel() -> bool:
    return False


def _log(root: Path, record: WriteRecord) -> bool:
    return append_record(root, record)


__all__ = [
    "Failure",
    "MovedItem",
    "MoveResult",
    "Skipped",
    "UndoMoveResult",
    "move_into",
    "undo_move_records",
]
