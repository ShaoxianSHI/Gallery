"""一摞统一的撤销栈：改回去，或者从废纸篓取回来。

⌘Z 只有一摞待撤销的写操作，改名（ADR 0004）和移到废纸篓（ADR 0008）都在里面，
栈顶是最近一次：

.. code-block:: text

    改名 A → 删 B → ⌘Z（取回 B）→ ⌘Z（还原 A 的名字）

这摞栈**不存内存里**，它是照着 ``journal/`` 里的记录重放出来的：写操作压栈、
撤销弹栈，弹空了就没有可撤销的了。好处是关掉软件再打开，⌘Z 照样管用，而且撤销
永远不会和「磁盘上实际发生了什么」说两套话。

**一个撤销单元 = 用户的一次操作。** 一次删十张会写十条记录（其中可能有的成功、
有的失败），但它们共用一个 ``batch``，于是重放时归成一摞，一次 ⌘Z 全取回来。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core.library.photos import GridEntry
from gallery.core.metadata import rename, trash
from gallery.core.metadata.journal import (
    ACTION_RENAME,
    ACTION_RESTORE,
    ACTION_TRASH,
    ACTION_UNDO,
    WriteRecord,
    read_records,
)

#: 压栈的动作：用户做过的写操作。
_PUSH_ACTIONS: Final = frozenset({ACTION_RENAME, ACTION_TRASH})

#: 弹栈的动作：这些写操作被撤销掉了。
_POP_ACTIONS: Final = frozenset({ACTION_UNDO, ACTION_RESTORE})

#: 菜单里显示的文案。
_LABELS: Final[dict[str, str]] = {ACTION_RENAME: "重命名", ACTION_TRASH: "移到废纸篓"}


@dataclass(frozen=True, slots=True)
class UndoResult:
    """撤销一次写操作的结果。"""

    #: 撤掉的是哪一种操作。界面据此决定是「替换那一行」还是「把行插回网格」，
    #: 菜单用它显示「撤销 移到废纸篓」这样的文案。
    action: str
    #: 撤销之后网格里应该存在的条目：撤销改名是一条，撤销删除可能是一批。
    entries: tuple[GridEntry, ...]
    #: 撤销改名时被替换掉的那一行。撤销删除时为 ``None`` —— 那一行本来就不在网格里。
    replaced: GridEntry | None = None
    #: 没能撤销的部分（废纸篓里已经没有它、原位置被占用……）。
    failures: tuple[trash.Failure, ...] = ()
    #: 是否记下了这次撤销。为 ``False`` 时界面应说明再按一次 ⌘Z 撤不掉更多了。
    journaled: bool = True


def pending_undo_runs(root: Path) -> list[list[WriteRecord]]:
    """重放日志，返回还没被撤销过的写操作，一摞一摞（栈顶是最后一摞）。

    弹栈按**原路径**配对：一条成功的撤销记录写着「我把这个路径改回去了」，
    于是只有它真正撤销掉的那几条离开这摞栈 —— 批量删除里取回失败的那几张仍留在栈上，
    用户再按一次 ⌘Z 还能再试一次。
    """
    runs: list[list[WriteRecord]] = []
    for record in read_records(Path(root)):
        if not record.succeeded:
            continue
        if record.action in _PUSH_ACTIONS:
            if record.batch and runs and runs[-1][0].batch == record.batch:
                runs[-1].append(record)
            else:
                runs.append([record])
        elif record.action in _POP_ACTIONS:
            _pop(runs, record)
    return runs


def next_undo_label(root: Path) -> str | None:
    """下一次 ⌘Z 会撤掉什么（「重命名」/「移到废纸篓」）。没什么可撤时返回 ``None``。

    菜单里显示它，用户按之前就知道会撤掉什么 —— 访达的「撤销 移动到废纸篓」就是这么用的。
    """
    runs = pending_undo_runs(root)
    return _LABELS.get(runs[-1][0].action) if runs else None


def undo_last_write(root: Path) -> UndoResult | None:
    """撤销最近一次还没被撤销过的写操作。

    :return: 撤销结果；没有可撤销的记录时返回 ``None``。
    :raises RenameError: 撤的是改名，但文件已经不在原处（或原处被别的文件占了）。
    :raises DeleteError: 撤的是删除，但废纸篓里已经没有它（或原位置被占了）。
        单项失败不会中断整批，结果里的 ``failures`` 会逐条说明。
    """
    root = Path(root)
    runs = pending_undo_runs(root)
    if not runs:
        return None
    run = runs[-1]
    if run[0].action == ACTION_RENAME:
        return _undo_rename(root, run[0])
    return _undo_delete(root, run)


def _undo_rename(root: Path, record: WriteRecord) -> UndoResult:
    result = rename.undo_rename_record(root, record)
    return UndoResult(
        action=ACTION_RENAME,
        entries=(result.entry,),
        replaced=result.previous,
        journaled=result.journaled,
    )


def _undo_delete(root: Path, run: Sequence[WriteRecord]) -> UndoResult:
    result = trash.restore_entries(root, run)
    return UndoResult(
        action=ACTION_TRASH,
        entries=result.restored,
        failures=result.failures,
        journaled=result.journaled,
    )


def _pop(runs: list[list[WriteRecord]], record: WriteRecord) -> None:
    """把 ``record`` 撤销掉的那几条从栈上摘下来。"""
    for run_index in range(len(runs) - 1, -1, -1):
        run = runs[run_index]
        for index in range(len(run) - 1, -1, -1):
            if run[index].source == record.target:
                del run[index]
                if not run:
                    runs.pop(run_index)
                return
    if runs:
        # 对不上就退一步：老日志或路径被用户动过的情况下，至少别撤错一摞里的东西。
        runs.pop()


def undo_label(action: str) -> str:
    """动作名对应的中文（未知动作给个中性说法，不让界面显示 ``None``）。"""
    return _LABELS.get(action, "上一步操作")


__all__ = [
    "UndoResult",
    "next_undo_label",
    "pending_undo_runs",
    "undo_label",
    "undo_last_write",
]
