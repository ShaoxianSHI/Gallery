"""会话内的编辑草稿：转一下、镜一下、裁一下的画面，以及按 ``⌘Z`` 一步步撤回它。

**它只在内存里，磁盘上一个字节都没有动**（ADR 0027）：原文件永远是原文件，⌘S 才把它
变成一个新文件。因此这一份状态与别的写操作不同 —��� 它不记账、不可追溯，也**跟着会话
消失**（关窗时问一句就够了，见 ``MainWindow.closeEvent``）。

放在 ``ui`` 顶层而不是 ``viewer/`` 里：它被大图页（编辑）、写操作（⌘S、⌘Z）与关窗
三处都用，改了它会牵连另一个包 —— 判据与 ADR 0006 相同。

**这里一行 Qt 都没有**，于是它能单独测（``tests/ui/test_edit_drafts.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gallery.core.media.edit import EditStep, is_identity, normalized
from gallery.ui.banners import edit_step_label


@dataclass(slots=True)
class _Draft:
    """一张照片的草稿。"""

    #: 当前生效的那些操作（按用户点的先后）。
    steps: list[EditStep] = field(default_factory=list)
    #: ``⌘Z`` 弹出来的那几步。留着是为了能连续撤，不受「一次操作一次撤销」的限制。
    history: list[EditStep] = field(default_factory=list)


class EditDrafts:
    """按路径存着的编辑草稿。

    **「净效果为零」是一张草稿存不存在的样子，不是一份数据。** 转了 90° 又转回来，
    规范化之后是空的（``core.media.edit.normalized``），于是这一格���在字典里等于不存在，
    ⌘S 不会白白产出一份与原文件一模一样的副本。
    """

    def __init__(self) -> None:
        self._drafts: dict[Path, _Draft] = {}

    # ---- 读 ----------------------------------------------------------------

    def steps(self, path: Path) -> tuple[EditStep, ...]:
        """这张照片当前那些操作（没有草稿就是空）。"""
        draft = self._drafts.get(path)
        return normalized(tuple(draft.steps)) if draft is not None else ()

    def has(self, path: Path) -> bool:
        return bool(self.steps(path))

    def paths(self) -> tuple[Path, ...]:
        """有草稿的那些路径（关窗前那句「有 N 张照片有未保存的编辑」用它）。"""
        return tuple(path for path in self._drafts if self.has(path))

    def count(self) -> int:
        return len(self.paths())

    def last_step(self) -> EditStep | None:
        """整个会话里最后动过的那一步（⌘Z 的菜单文案用它）。

        **取的是用户最后按的那一下，不是规范化之后的形式。** ``[左转, 镜像]`` 规范化
        成「先镜像再左转」，于是规范化后的最后一步是左转 —— 而用户最后做的是镜像，
        菜单上就该写「撤销 左右镜像」。
        """
        for path in reversed(list(self._drafts)):
            draft = self._drafts[path]
            if draft.steps:
                return draft.steps[-1]
        return None

    # ---- 写 ----------------------------------------------------------------

    def add(self, path: Path, step: EditStep) -> None:
        """记下一步编辑。新的一步会**清掉撤销栈**（与访达「重做」那套直觉一致：
        做过新动作之后，「回到更早的状态」这条路就不该再通着）。"""
        draft = self._drafts.setdefault(path, _Draft())
        draft.steps.append(step)
        draft.history.clear()

    def undo(self, path: Path) -> EditStep | None:
        """撤掉这张照片最后一步，返回被撤掉的那一步（没有可撤的返回 ``None``）。"""
        draft = self._drafts.get(path)
        if draft is None or not draft.steps:
            return None
        step = draft.steps.pop()
        draft.history.append(step)
        return step

    def undo_last(self) -> tuple[Path, EditStep] | None:
        """整个会话里最后动过的那一张的**最后一步**（⌘Z 走这条，优先于写操作撤销）。"""
        for path in reversed(list(self._drafts)):
            step = self.undo(path)
            if step is not None:
                return (path, step)
        return None

    def clear(self, path: Path) -> None:
        """这张照片草稿没了（存成副本之后，或用户在菜单里「还原」）。"""
        self._drafts.pop(path, None)

    def clear_all(self) -> None:
        self._drafts.clear()

    # ---- 文案 --------------------------------------------------------------

    def undo_label(self) -> str | None:
        """⌘Z 会撤掉什么（「撤销 左转 90°」）。没有可撤的编辑时返回 ``None``，
        让位给写操作那一摞（见 ``ui.write_ops.undo_last_write``）。"""
        step = self.last_step()
        return edit_step_label(step) if step is not None else None


def draft_is_meaningless(steps: tuple[EditStep, ...]) -> bool:
    """这些操作合起来什么都没做吗（转了 360°、镜像了两次）。

    ⌘S 在这时**不产出文件**，只说一句「这张还没有改动」 —— 否则库里会多出一份
    与原文件一模一样的副本，而用户以为那是自己刚才存出来的（ADR 0027 决策 2）。
    """
    return is_identity(steps)


__all__ = ["EditDrafts", "draft_is_meaningless"]
