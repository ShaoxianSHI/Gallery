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
- **事件绑定跟着走，撤销也跟回来**（ADR 0043 决策 8）：移动改的是绝对路径，而绑定按路径记。
  撤销是一次移动，于是绑定也搬回去 —— 与撤销改名同一套道理，**不做这一步的话撤销会把事件弄丢**。

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
    events: EventStore | None = None,
) -> MoveResult:
    """把 ``sources`` **移动**进 ``target``，返回结果。

    :param root: 库根。记账写在它下面（``journal/``），与另外三个写操作同一处。
    :param target: 目标相册，必须已经存在且是个目录。
    :param sources: 要移动的路径。界面上给的是网格里选中的照片；理论上也接受文件夹。
    :param should_cancel: 每移一项之前问一次。只在**项与项之间**取消：移一半停下来会让
        用户自己都说不清哪些被移了，那比移完更糟。
    :param events: 事件库（可换绑用）。``None`` 或不可用时跳过那一步 —— 见
        :func:`~gallery.core.metadata.rename.rename_entry` 的同一段说明。
    :raises TransferError: ``target`` 不是目录，或者它落在库外。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise TransferError(f"「{destination.name}」不是一个相册。")
    _guard(root, destination)

    cancelled = should_cancel or never_cancel
    batch = uuid4().hex[:12]
    moved: list[MovedItem] = []
    skipped: list[Skipped] = []
    failures: list[Failure] = []
    #: 真要跟着搬的绑定：``(旧位置, 新位置, 是不是文件夹)``，攒到整批结束再一次事务搬完。
    follow: list[tuple[Path, Path, bool]] = []
    journaled = True
    # **目录只扫一次**（理由与偏差见 :func:`~gallery.core.metadata.naming.free_name_in`）。
    taken = occupied_names(destination)

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure.at(source, message=SOURCE_GONE))
            continue
        if same_directory(source.parent, destination):
            skipped.append(Skipped(source=source, message=f"它已经在「{destination.name}」里了。"))
            continue
        if source.is_dir() and paths.is_within(source, destination):
            failures.append(Failure.at(source, message="不能把相册移进它自己或它自己的子相册。"))
            continue
        if cancelled():
            break
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
    # 绑定跟着走，且**一次事务搬完整批**（ADR 0043 决策 8）。只收真的移走的那些 ——
    # 跳过与失败的那些路径压根没变。监听器随后还会把同一件事报回来一次（``MOVE`` 不做回声
    # 屏蔽，新文件的上屏靠它），那一次搬不到什么：源路径上已经没有绑定了。
    follow_moves(events, follow)
    return MoveResult(
        moved=tuple(moved),
        skipped=tuple(skipped),
        failures=tuple(failures),
        journaled=journaled,
    )


def undo_move_records(
    root: Path, records: Sequence[WriteRecord], *, events: EventStore | None = None
) -> UndoMoveResult:
    """把 :mod:`gallery.core.metadata.undo` 挑出来的移动记录挪回原处。

    与 :func:`trash.restore_entries` 对称：逐条还原，取不回来的如实报告，不静默跳过。

    **原位置被占了就不动它**（与取回废纸篓同一个取向）：用户在这期间自己放了东西进去，
    替他腾出位置等于删掉那个东西 —— 那需要用户亲手做。

    :param events: 事件库（可换绑用）。撤销一次移动仍然是一次移动，绑定跟着搬回去
        （方向与当初那次相反），与撤销改名同一套道理。
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

    **这里原本一道都没有**，而它是四个写操作里唯一没有的（改名
    :mod:`~gallery.core.metadata.rename`、移到废纸篓 :mod:`~gallery.core.metadata.trash`、
    新建相册 :mod:`~gallery.core.metadata.album` 都有）。理由也正是那几个写的那句：
    网格里根本不会出现库根和配置目录，所以这一道今天**够不到** —— 但它是「让用户的
    文件离开原处」的那个入口，红线 1 点名的正是它，值得对「库根」「配置目录」多一层
    保险。

    **源那一侧不查**：界面上给的是网格里选中的照片（必然在库内），而
    :func:`undo_move_records` 要挪回的地方来自日志里那条移动的原路径。**目标必须查** ——
    它一旦落到库外，就是把用户的照片搬出了 Gallery 管得着的地方，而那种事没有任何
    一条后续流程能撤回。

    **库根本身不挡**：守卫要挡的是「跑到库外」，不是「落在库里」。配置文件里写
    ``rename._guard`` / ``trash._guard`` 挡库根，是因为它们查的是**源**（把库本身
    改名或删掉，Gallery 就再也找不到照片了）；对目标来说那两件事都不成立。
    配置目录仍然挡掉 —— 它里面放的每一样都是「只有 Gallery 自己看得见」的缓存，
    用户的照片粘进去不会变得可见，只会成为下次清理的垃圾（红线 5）。
    """
    root = Path(root)
    try:
        paths.guard_within_root(root, destination, allow_root=True)
    except ValueError as exc:
        raise TransferError(_transfer_reason(destination, str(exc)), path=destination) from exc


def _transfer_reason(path: Path, reason: str) -> str:
    """按闸门说的那一件事给一句中文 —— 理由见 :func:`rename._rename_reason`。"""
    if "配置目录" in reason:
        return "这是 Gallery 自己的配置目录，不能移东西进去。"
    return f"只能移到库里的相册：{path}。"


__all__ = [
    "Failure",
    "MoveResult",
    "MovedItem",
    "Skipped",
    "UndoMoveResult",
    "move_into",
    "undo_move_records",
]
