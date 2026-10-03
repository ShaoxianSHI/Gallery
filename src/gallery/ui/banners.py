"""一次动作做完之后，横幅上那**一行中文**是怎么拼出来的。

这些函数**一行 Qt 都没有** —— 它们把 ``core`` 那些结果对象（``RenameResult``、
``PasteResult``、…）翻成一句话，外加两句用户一定要知道的补充（记账失败 ⌘Z 撤销不了）。

它们过去是 ``main_window.py`` 顶部的七个私有函数。留在那儿有两个坏处：

- **没法单独测。** 测横幅那句话就要造一个 ``MainWindow``（要 Qt、要一个真的库、
  要事件循环转起来）。而「记账失败时那句提示必须出现」是一条纯粹关于措辞的规矩。
- **混在一堆装配代码里。** 改一句措辞要在一份三千多行的文件里找，而它们与窗口的
  生命周期、线程、信号没有任何关系。

于是收在这儿，``MainWindow`` 那一侧只剩调用。
"""

from __future__ import annotations

from collections.abc import Sequence

from gallery.core.library.photos import FolderRef, GridEntry
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import ACTION_MOVE, ACTION_RENAME
from gallery.core.metadata.paste import PasteResult
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MoveResult
from gallery.core.metadata.trash import TrashResult
from gallery.core.metadata.undo import UndoResult


def rename_banner(result: RenameResult) -> str:
    """改名之后的那行提示。

    记账失败必须说出来：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来，
    让他以为「随时能撤销」比不告诉他更糟。
    """
    note = "" if result.journaled else "，但没能记下操作日志，⌘Z 撤销不了"
    return f"已重命名为「{result.entry.name}」{note}。"


def undo_note(journaled: bool) -> str:
    return "" if journaled else "，但没能记下操作日志"


def paste_banner(result: PasteResult, target_name: str) -> str:
    """粘贴之后的那行提示：三件事都要说清 —— 粘了哪些、跳过了哪些、为什么没成。

    **改了名的要单独点出来**（用户要求的是「不覆盖」，而系统给出的新名字得让他看见，
    否则相册里凭空多出一张「一张 2.jpg」会让人以为程序认错了文件）。
    """
    parts: list[str] = []
    if result.pasted:
        head = f"已粘贴 {len(result.pasted)} 项到「{target_name}」"
        renamed = [item for item in result.pasted if item.renamed]
        if len(renamed) == 1:
            head += f"（「{renamed[0].source.name}」改名为「{renamed[0].created.name}」）"
        elif renamed:
            head += f"（其中 {len(renamed)} 项改了名，免得覆盖已有的）"
        parts.append(head)
    if result.skipped:
        parts.append(f"{len(result.skipped)} 项本来就在「{target_name}」里")
    if result.failures:
        first = result.failures[0]
        more = f"，共 {len(result.failures)} 项" if len(result.failures) > 1 else ""
        parts.append(f"{first.source.name} 没粘成{more}：{first.message.rstrip('。')}")
    if not result.journaled:
        parts.append("没能记下操作日志")
    return "；".join(parts) + "。"


def transfer_banner(result: MoveResult | PasteResult, target_name: str, verb: str) -> str:
    """「复制到相册 / 移动到相册」之后的那行提示（ADR 0019）。

    与 :func:`_paste_banner` 一套说法：动了哪些、跳过了哪些、为什么没成、能不能撤销。
    **改了名的要单独点出来** —— 目标相册里本来就有同名时按访达的规矩改叫「一张 2.jpg」，
    不说清楚的话用户会以为程序认错了文件。

    移动比粘贴多一句「⌘Z 可以撤销」：这是用户敢用它的前提（红线 1，见 ADR 0019 决策 3）。
    """
    moved = result.moved if isinstance(result, MoveResult) else result.pasted
    parts: list[str] = []
    if moved:
        head = f"已{verb} {len(moved)} 项到「{target_name}」"
        renamed = [item for item in moved if item.renamed]
        if len(renamed) == 1:
            head += f"（「{renamed[0].source.name}」改名为「{renamed[0].created.name}」）"
        elif renamed:
            head += f"（其中 {len(renamed)} 项改了名，免得覆盖已有的）"
        parts.append(head)
    if result.skipped:
        parts.append(f"{len(result.skipped)} 项本来就在「{target_name}」里")
    if result.failures:
        first = result.failures[0]
        more = f"，共 {len(result.failures)} 项" if len(result.failures) > 1 else ""
        parts.append(f"{first.name} 没{verb}成{more}：{first.message.rstrip('。')}")
    if not result.journaled:
        parts.append("没能记下操作日志")
    if isinstance(result, MoveResult) and result.journaled:
        parts.append("⌘Z 可以撤销")
    return "；".join(parts) + "。"


def trash_banner(result: TrashResult) -> str:
    """移到废纸篓之后的那行提示。

    三件事必须说清楚：动了多少、能不能撤销、没成的那些为什么没成。
    废纸篓本身是第二层保险，但用户不该靠猜。
    """
    moved = result.moved
    if not moved:
        return f"没有移走任何东西：{first_reason(result.failures)}{undo_note(result.journaled)}。"
    if any(isinstance(entry, FolderRef) for entry in moved):
        head = f"已把相册「{album_name(moved)}」和里面的内容移到废纸篓"
    elif len(moved) == 1:
        head = f"已把「{moved[0].name}」移到废纸篓"
    else:
        head = f"已把 {len(moved)} 项移到废纸篓"
    if result.failures:
        head += f"，但 {len(result.failures)} 项没移走：{first_reason(result.failures)}"
    # 不能一边说「⌘Z 可以撤销」一边说「撤销不了」，所以这两半是一起给的。
    hint = "⌘Z 可以撤销。" if result.journaled else "⌘Z 撤销不了：没能记下操作日志。"
    return f"{head}。{hint}"


def undo_banner(result: UndoResult) -> str:
    """撤销之后的那行提示。"""
    note = undo_note(result.journaled)
    if result.action == ACTION_RENAME:
        return f"已撤销，恢复为「{result.entries[0].name}」{note}。"
    if result.action == ACTION_MOVE:
        # 撤销移动没有 entries：挪回来的东西**不一定在当前这一层**（用户在哪个相册
        # 点的移动，撤回来时人就站在哪儿），所以界面不知道该往网格里插哪一行 ——
        # 交给监听器（ADR 0013）去发现，横幅只说发生了什么。
        if result.failures:
            return f"没能挪回去：{first_reason(result.failures)}{note}。"
        return f"已撤销，照片已挪回原来的相册{note}。"
    restored = result.entries
    if not restored:
        return f"没能取回来：{first_reason(result.failures)}{note}。"
    if len(restored) == 1:
        head = f"已从废纸篓取回「{restored[0].name}」"
    else:
        head = f"已从废纸篓取回 {len(restored)} 项"
    if result.failures:
        head += f"，但 {len(result.failures)} 项没取回来：{first_reason(result.failures)}"
    return f"{head}{note}。"


def first_reason(failures: Sequence[Failure]) -> str:
    """失败原因的摘要。

    只有一项时要点名道姓 —— 用户得知道该去看哪张照片；多项时横幅只有一行容身之地，
    只给第一条，完整列表在日志里。

    **三个模块报回来的失败项是同一个类型**（``metadata.failures.Failure``），所以粘贴、
    移动、移到废纸篓三条路的横幅在这里合流，不需要分形状。统一之前这里是
    ``Failure | TransferFailure``，而 ``paste.Failure`` 连 ``.name`` 都没有 ——
    同一个函数名，两种形状，第三种半套。
    """
    if not failures:
        return "原因不明"
    head = f"「{failures[0].name}」" if len(failures) == 1 else "其中一项"
    return f"{head}：{failures[0].message.rstrip('。')}"


def album_name(entries: Sequence[GridEntry]) -> str:
    """一批条目里的相册名（可能混着照片，取第一个相册的）。"""
    for entry in entries:
        if isinstance(entry, FolderRef):
            return entry.display_name
    return ""


__all__ = [
    "album_name",
    "first_reason",
    "paste_banner",
    "rename_banner",
    "transfer_banner",
    "trash_banner",
    "undo_banner",
    "undo_note",
]
