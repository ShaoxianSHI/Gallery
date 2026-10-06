"""一摞统一的撤销栈：改名、移到废纸篓、移动到相册、元数据编辑都在里面（⌘Z 一次撤一摞）。

栈**不存内存里**，照着 ``journal/`` 重放：写操作压栈、撤销弹栈，重开软件 ⌘Z 照样管用，
也不会和磁盘上实际发生的事说两套话。**粘贴不在栈里** —— 撤销它就是删文件（红线 1）。
一次删十张共用一个 ``batch``，仍只值一次 ⌘Z。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core.events.store import Event, EventStore
from gallery.core.library.photos import GridEntry
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import (
    ACTION_CREATE,
    ACTION_EVENT,
    ACTION_EVENT_CREATE,
    ACTION_EVENT_DELETE,
    ACTION_METADATA,
    ACTION_MOVE,
    ACTION_PASTE,
    ACTION_RENAME,
    ACTION_RESTORE,
    ACTION_TRASH,
    ACTION_UNDO,
    ACTION_UNDO_EVENT,
    ACTION_UNDO_METADATA,
    RESULT_PENDING,
    WriteRecord,
    read_records,
)
from gallery.core.metadata.rename import undo_rename_record
from gallery.core.metadata.transfer import Failure as TransferFailure
from gallery.core.metadata.transfer import undo_move_records
from gallery.core.metadata.trash import Failure as TrashFailure
from gallery.core.metadata.trash import restore_entries
from gallery.core.metadata.writeback import MetadataWriter, undo_metadata

#: 弹栈的动作：这些写操作被撤销掉了。
_POP_ACTIONS: Final = frozenset(
    {ACTION_UNDO, ACTION_RESTORE, ACTION_UNDO_METADATA, ACTION_UNDO_EVENT}
)

#: **不收进撤销栈的动作** —— 一份显式的排除清单，而不是「不在压栈清单里就等于排除」。
#: 这几个动作的「撤销」都是**删掉用户的东西**，而删除只许用户亲手触发（红线 1）。靠隐式成立
#: 不够：认不出的动作名也压栈，而这份清单少写一个成员是**静默**的。
#:
#: 新建事件与新建相册同款：撤销它就是删，而刚建的那一刻用户还没往里放东西。**它比别的更
#: 需要这一条**：新建事件之后往往紧跟着绑定几百张照片（那一步不入栈），⌘Z 要是撤到「新建」
#: 就会把那些绑定一起带走。
_NEVER_UNDOABLE: Final = frozenset({ACTION_CREATE, ACTION_PASTE, ACTION_EVENT_CREATE})

#: 菜单里显示的文案。**认不出的动作名走「上一步」**：宁可说得笼统，也不能因为
#: 没有对应条目就让 ⌘Z 在菜单上不出现。
_LABELS: Final[dict[str, str]] = {
    ACTION_RENAME: "重命名",
    ACTION_TRASH: "移到废纸篓",
    ACTION_MOVE: "移动到相册",
    ACTION_METADATA: "编辑元数据",
    ACTION_EVENT: "修改事件",
    ACTION_EVENT_DELETE: "删除事件",
}

#: ``_LABELS`` 里没有的动作名用它。
FALLBACK_LABEL: Final = "上一步"


@dataclass(frozen=True, slots=True)
class UndoResult:
    """撤销一次写操作的结果。"""

    #: 撤掉的是哪一种操作。界面据此决定是「替换那一行」「把行插回网格」还是「刷新事件清单」。
    action: str
    #: 撤销之后网格里应该存在的条目。撤销元数据编辑也是一条 —— 文件还在原地，但大小与
    #: 修改时间变了，界面手上那条引用已经过期（缩略图与索引的键都含它）。
    entries: tuple[GridEntry, ...]
    #: 撤销改名时被替换掉的那一行。撤销删除时为 ``None`` —— 那一行本来就不在网格里。
    replaced: GridEntry | None = None
    #: 没能撤销的部分（废纸篓里已经没有它、原位置被占用……）。取回废纸篓报 ``name``，
    #: 撤销移动报 ``source``（多带一个完整路径，横幅要能说出它现在不在哪儿了）。
    failures: tuple[TrashFailure | TransferFailure, ...] = ()
    #: 是否记下了这次撤销。为 ``False`` 时界面应说明再按一次 ⌘Z 撤不掉更多了。
    journaled: bool = True
    #: 撤销事件改动之后**那个事件**（ADR 0043）。事件不是网格里的条目，所以它不在
    #: :attr:`entries` 里 —— 而横幅要说出它叫什么，于是单独带一个。撤别的动作时恒为 ``None``。
    event: Event | None = None


def pending_undo_runs(root: Path) -> list[list[WriteRecord]]:
    """重放日志，返回还没被撤销过的写操作，一摞一摞（栈顶是最后一摞）。

    弹栈按**原路径**配对：只有那条撤销记录真正撤销掉的才离开栈，批量删除里取回失败的那
    几张仍留在栈上，再按一次 ⌘Z 还能再试。

    **pending 记录照样压栈**（见 :data:`RESULT_PENDING`）：正常写回在日志里成对（pending
    + 终态，终态顶掉它，一次 ⌘Z 就够），剩下孤零零一条意味着进程死在记账与落盘之间 ——
    文件可能已被改过，正是最需要撤销的时候。**只对元数据写回这么放行**：撤销写回是纯写入，
    而改名 / 删除的撤销是反过来搬动用户文件，一条「打算动」的记录不说明动成了什么样。
    """
    runs: list[list[WriteRecord]] = []
    #: 还没等到终态的 pending 记录，按 ``(动作, 路径)`` 记着。
    unresolved: dict[tuple[str, Path], WriteRecord] = {}
    for record in read_records(Path(root)):
        key = (record.action, record.target)
        if record.result == RESULT_PENDING:
            if record.action == ACTION_METADATA:
                unresolved[key] = record
            continue
        # 终态一到，那条 pending 就不该再让人按一次 ⌘Z 了。
        unresolved.pop(key, None)
        if not record.succeeded:
            continue
        if record.action in _NEVER_UNDOABLE:
            continue
        if record.action in _POP_ACTIONS:
            _pop(runs, record)
        else:
            # **不是弹栈的就都压栈**，包括动作名不认识的那几条：按清单匹配时动作名缺失（日志被
            # 截断、外来的记录、新加的动作忘了登记）会把那条记录**静默丢掉**，用户刚做完的事从此
            # 再也撤不掉。压进去之后 :func:`_undo_delete` 会把它移回原处 —— 不认识动作名也能撤。
            if record.batch and runs and runs[-1][0].batch == record.batch:
                runs[-1].append(record)
            else:
                runs.append([record])
    # 剩下的 pending 是最后发生的那件事，所以压在栈顶。
    for record in unresolved.values():
        runs.append([record])
    return runs


def next_undo_label(root: Path) -> str | None:
    """下一次 ⌘Z 会撤掉什么（「重命名」/「移到废纸篓」）。没什么可撤时返回 ``None``。"""
    runs = pending_undo_runs(root)
    return _LABELS.get(runs[-1][0].action, FALLBACK_LABEL) if runs else None


def undo_last_write(
    root: Path,
    *,
    writer: MetadataWriter | None = None,
    events: EventStore | None = None,
) -> UndoResult | None:
    """撤销最近一次还没被撤销过的写操作。

    :param writer: 撤销位置编辑时用的写回工具（测试注入假实现）。其余动作用不上。
    :param events: 事件库（撤销事件改动时用）。``None`` 表示事件库这次不可用。
    :return: 没有可撤销的记录时返回 ``None``。
    :raises RenameError: 撤的是改名，但文件已经不在原处（或原处被别的文件占了）。
    :raises DeleteError: 撤的是删除，但废纸篓里已经没有它（或原位置被占了）。单项失败不会
        中断整批，结果里的 ``failures`` 会逐条说明。
    :raises MetadataWriteError: 撤的是位置编辑，但文件没了、没装 exiftool，或写回原值失败。
    """
    root = Path(root)
    runs = pending_undo_runs(root)
    if not runs:
        return None
    run = runs[-1]
    if run[0].action == ACTION_RENAME:
        return _undo_rename(root, run[0], events)
    if run[0].action == ACTION_MOVE:
        return _undo_move(root, run)
    if run[0].action == ACTION_METADATA:
        return _undo_metadata(root, run[0], writer=writer)
    if run[0].action in (ACTION_EVENT, ACTION_EVENT_DELETE):
        # **必须显式分派**：落到最后那条兜底（``_undo_delete``）就是拿事件记录里的
        # 事件库那个文件当路径去系统废纸篓里捞东西。
        return _undo_event(run[0], events)
    return _undo_delete(root, run)


def _undo_metadata(root: Path, record: WriteRecord, *, writer: MetadataWriter | None) -> UndoResult:
    """把写回去的字段改回原值（ADR 0030）。

    **冲突的字段留在原地并如实报告**：它们在 Gallery 之外被改过，按原值盖回去就是毁掉
    别人的改动。

    **弹不弹栈分两种情形**：

    - **部分冲突**（有撤得成的字段）：撤成功的那几条记 ``ok`` → 这次撤销被弹栈。
      它们确实回到原值了，再让用户按一次去重撤没有意义。
    - **全冲突**（一项都撤不了）：记 ``failed`` → **不弹栈**，栈顶仍是当初那条写回，
      再按一次会重试。留着是因为外部那一方可能改回去了。
    """
    result = undo_metadata(root, record, writer=writer)
    return UndoResult(
        action=ACTION_METADATA,
        entries=() if result.entry is None else (result.entry,),
        failures=result.conflicts,
        journaled=result.journaled,
    )


def _undo_event(record: WriteRecord, events: EventStore | None) -> UndoResult:
    """把一次事件改动改回去（ADR 0043 决策 5）。

    **网格里没有它的条目**（事件不是磁盘上的东西），所以 ``entries`` 恒为空 —— 界面按
    ``action`` 决定刷新哪一处，别指望从 ``entries`` 里找到点什么。

    事件库不可用时如实说一句，而不是当作「没什么可撤的」：那会让用户以为 ⌘Z 坏了。
    """
    if events is None or not events.available:
        return UndoResult(
            action=record.action,
            entries=(),
            failures=(Failure(name="事件", message="这次打不开事件库，撤不了。"),),
            journaled=False,
        )
    outcome = events.undo_record(record)
    if outcome.message is not None:
        return UndoResult(
            action=record.action,
            entries=(),
            failures=(Failure(name="事件", message=outcome.message),),
            journaled=False,
        )
    return UndoResult(
        action=record.action, entries=(), journaled=outcome.journaled, event=outcome.event
    )


def _undo_rename(root: Path, record: WriteRecord, events: EventStore | None) -> UndoResult:
    # 撤销是一次改名，于是事件绑定跟着搬回去（ADR 0043 决策 8）。
    result = undo_rename_record(root, record, events=events)
    return UndoResult(
        action=ACTION_RENAME,
        entries=(result.entry,),
        replaced=result.previous,
        journaled=result.journaled,
    )


def _undo_move(root: Path, run: Sequence[WriteRecord]) -> UndoResult:
    """把移动过的东西挪回原来的相册（ADR 0019）。

    网格该显示什么交给界面重扫：挪回来的东西**可能不在当前这一层**，这里不替界面决定
    插到哪一行。
    """
    result = undo_move_records(root, run)
    return UndoResult(
        action=ACTION_MOVE,
        entries=(),
        failures=result.failures,
        journaled=result.journaled,
    )


def _undo_delete(root: Path, run: Sequence[WriteRecord]) -> UndoResult:
    result = restore_entries(root, run)
    return UndoResult(
        action=ACTION_TRASH,
        entries=result.restored,
        failures=result.failures,
        journaled=result.journaled,
    )


def _pop(runs: list[list[WriteRecord]], record: WriteRecord) -> bool:
    """把 ``record`` 撤销掉的那几条从栈上摘下来。

    返回是否摘到了。**对不上就什么都不做**：曾有一句「退一步」的兜底（``runs.pop()``），
    那等于一次撤销都没做就把栈顶那一摞扔了，用户刚做的事从此再也撤不掉。宁可什么都不摘，
    让下一次 ⌘Z 仍指向那一摞。
    """
    for run_index in range(len(runs) - 1, -1, -1):
        run = runs[run_index]
        for index in range(len(run) - 1, -1, -1):
            if run[index].source == record.target:
                del run[index]
                if not run:
                    runs.pop(run_index)
                return True
    return False


__all__ = [
    "FALLBACK_LABEL",
    "UndoResult",
    "next_undo_label",
    "pending_undo_runs",
    "undo_last_write",
]
