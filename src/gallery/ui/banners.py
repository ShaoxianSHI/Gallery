"""一次动作做完之后，横幅上那**一行中文**是怎么拼出来的。

这些函数**一行 Qt 都没有**（所以能单独测）：把 ``core`` 的结果对象翻成一句话，外加那句用户一定
要知道的补充（记账失败 ⌘Z 撤销不了）。
"""

from __future__ import annotations

from collections.abc import Sequence

from gallery.core.events.store import EventWriteOutcome
from gallery.core.external.launch import OpenOutcome
from gallery.core.library.facade import MetadataWriteOutcome
from gallery.core.library.photos import FolderRef, GridEntry
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import (
    ACTION_EVENT_DELETE,
    ACTION_METADATA,
    ACTION_MOVE,
    ACTION_RENAME,
)
from gallery.core.metadata.paste import PastedFile, PasteResult
from gallery.core.metadata.paste import Skipped as PasteSkipped
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MovedItem, MoveResult
from gallery.core.metadata.transfer import Skipped as MoveSkipped
from gallery.core.metadata.trash import TrashResult
from gallery.core.metadata.undo import UndoResult


def rename_banner(result: RenameResult) -> str:
    """改名之后的那行提示。

    记账失败必须说出来：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来 —— 让他以为「随时能
    撤销」比不告诉他更糟。
    """
    note = "" if result.journaled else "，但没能记下操作日志，⌘Z 撤销不了"
    return f"已重命名为「{result.entry.name}」{note}。"


def undo_note(journaled: bool) -> str:
    return "" if journaled else "，但没能记下操作日志"


# ---- 事件（ADR 0043）----------------------------------------------------------


def event_created_banner(outcome: EventWriteOutcome) -> str:
    """记完一次之后的那一句。**新建不在撤销栈里**（撤销它就是删），所以这一句不承诺 ⌘Z。"""
    return f"已记为「{outcome.event.display_name}」。"


def event_updated_banner(outcome: EventWriteOutcome) -> str:
    """改了事件之后。**一句里说清成没成与能不能撤** —— 与元数据那一族同一套规矩。"""
    if not outcome.changed:
        return f"「{outcome.event.display_name}」没有改动。"
    if not outcome.journaled:
        return f"已更新「{outcome.event.display_name}」。⌘Z 撤销不了：没能记下操作日志。"
    return f"已更新「{outcome.event.display_name}」。⌘Z 可以撤销。"


def event_deleted_banner(outcome: EventWriteOutcome) -> str:
    """删掉事件之后。**连着删掉的照片关联要一起说**：那是用户看不见的损失，不说他不会知道。

    撤销**能把绑定放回来**，所以这一句承诺 ⌘Z。
    """
    what = f"「{outcome.event.display_name}」"
    photos = f"，连带从 {len(outcome.unbound)} 张照片上移掉了它的记录" if outcome.unbound else ""
    note = "" if outcome.journaled else "。⌘Z 撤销不了：没能记下操作日志"
    return f"已删除{what}{photos}{note}。"


def photos_bound_banner(count: int, event_name: str, *, by_range: bool = False) -> str:
    """把照片记进一个事件之后。**新记进去几张**说清 —— 已经记着的那些不是这次的成果。

    ``by_range`` 为真时提一句「按日期」：那是几百张的批量操作，用户该知道它为什么这么多。
    """
    if count <= 0:
        return f"这些照片已经在「{event_name}」里了，没有改动。"
    how = "按日期范围把" if by_range else "已把"
    return f"{how} {count} 张照片记进「{event_name}」。"


def photos_unbound_banner(count: int, event_name: str) -> str:
    """把照片从一个事件里拿出来之后。"""
    if count <= 0:
        return f"这些照片本来就不在「{event_name}」里，没有改动。"
    return f"已把 {count} 张照片从「{event_name}」里拿出来。"


def event_undo_banner(result: UndoResult, kind_label: str) -> str:
    """⌘Z 撤掉一次事件改动之后。**两种改动说法不一样** —— 撤的是删除就说「回来了」。"""
    what = (
        f"{kind_label}「{result.event.display_name}」" if result.event is not None else kind_label
    )
    verb = "已恢复" if result.action == ACTION_EVENT_DELETE else "已把"
    if not result.journaled:
        return f"{verb}{what}改回去，但没能记下操作日志，下一次 ⌘Z 不一定接得上。"
    return f"{verb}{what}改回去。"


def paste_banner(result: PasteResult, target_name: str) -> str:
    """粘贴之后的那行提示：三件事都要说清 —— 粘了哪些、跳过了哪些、为什么没成。

    **改了名的要单独点出来**：用户要求的是「不覆盖」，而系统给出的新名字得让他看见。话怎么
    拼在 :func:`_into_album_parts` 里，与「复制到相册 / 移动到相册」共用同一份。
    """
    return _banner_line(
        _into_album_parts(
            result.pasted,
            result.skipped,
            result.failures,
            journaled=result.journaled,
            target_name=target_name,
            done_verb="粘贴",
            fail_verb="粘",
        )
    )


def transfer_banner(result: MoveResult | PasteResult, target_name: str, verb: str) -> str:
    """「复制到相册 / 移动到相册」之后的那行提示（ADR 0019）。

    与 :func:`paste_banner` 一套说法（含「改了名的要单独点出来」）。移动比粘贴多一句
    「⌘Z 可以撤销」—— 这是用户敢用它的前提（红线 1，见 ADR 0019 决策 3），也是两条路唯一
    刻意不同的地方。
    """
    moved = result.moved if isinstance(result, MoveResult) else result.pasted
    parts = _into_album_parts(
        moved,
        result.skipped,
        result.failures,
        journaled=result.journaled,
        target_name=target_name,
        done_verb=verb,
        fail_verb=verb,
    )
    if isinstance(result, MoveResult) and result.journaled:
        parts.append("⌘Z 可以撤销")
    return _banner_line(parts)


def _into_album_parts(
    moved: Sequence[PastedFile | MovedItem],
    skipped: Sequence[PasteSkipped | MoveSkipped],
    failures: Sequence[Failure],
    *,
    journaled: bool,
    target_name: str,
    done_verb: str,
    fail_verb: str,
) -> list[str]:
    """「把若干项搬进某个相册」之后那句话的**唯一一份算法**。

    粘贴（ADR 0018）与复制 / 移动到相册（ADR 0019）说的是同一件事，于是按同一套说法报出来；
    两处刻意不同的地方由参数带进来 —— ``done_verb`` / ``fail_verb`` 分成两个，因为粘贴那句是
    「已**粘贴** 3 项」而失败那句是「没**粘**成」。失败项一律读 :attr:`Failure.name`：直接读它
    才不必赌 ``source`` 不是 ``None``（分组卡片那一种就没有路径）。返回**分段**而不是整句，是
    为了让调用方能在后面追加自己那一句（移动的「⌘Z 可以撤销」）。
    """
    parts: list[str] = []
    if moved:
        head = f"已{done_verb} {len(moved)} 项到「{target_name}」"
        renamed = [item for item in moved if item.renamed]
        if len(renamed) == 1:
            head += f"（「{renamed[0].source.name}」改名为「{renamed[0].created.name}」）"
        elif renamed:
            head += f"（其中 {len(renamed)} 项改了名，免得覆盖已有的）"
        parts.append(head)
    if skipped:
        parts.append(f"{len(skipped)} 项本来就在「{target_name}」里")
    if failures:
        first = failures[0]
        more = f"，共 {len(failures)} 项" if len(failures) > 1 else ""
        parts.append(f"{first.name} 没{fail_verb}成{more}：{first.message.rstrip('。')}")
    if not journaled:
        parts.append("没能记下操作日志")
    return parts


def _banner_line(parts: list[str]) -> str:
    """把几段话拼成横幅上那一行：分号隔开，末尾一个句号。"""
    return "；".join(parts) + "。"


def trash_banner(result: TrashResult) -> str:
    """移到废纸篓之后的那行提示：动了多少、能不能撤销、没成的那些为什么没成。

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
    if result.action == ACTION_METADATA:
        # 撤销位置编辑：文件回到原样，但**被别的工具改过的那几个字段留在了那里** —— 不说的话
        # 用户会以为照片已经整个回到刚才那样了。
        if result.failures:
            count = len(result.failures)
            return f"只撤销了一部分：{count} 项在 Gallery 之外被改过，没有动它们{note}。"
        name = result.entries[0].name if result.entries else "照片"
        return f"已撤销，「{name}」的位置恢复原样{note}。"
    if result.action == ACTION_RENAME:
        return f"已撤销，恢复为「{result.entries[0].name}」{note}。"
    if result.action == ACTION_MOVE:
        # 撤销移动没有 entries：挪回来的东西**不一定在当前这一层**，界面不知道该往网格里插
        # 哪一行 —— 交给监听器（ADR 0013）去发现，横幅只说发生了什么。
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


def album_cover_banner(album_name: str, photo_name: str) -> str:
    """设完相册封面之后的那行提示（ADR 0028）。

    **两样都要说出来**：是哪一张成了封面、它是**哪个相册**的封面 —— 只说「已设为封面」的话，
    用户得自己回去确认设的是不是刚才看的那个相册。不提撤销：这一条只写 Gallery 自己的配置
    文件，不在 ⌘Z 那一摞栈里（ADR 0028 决策 5），要改回去走「取消相册封面」。
    """
    return f"已把「{photo_name}」设为「{album_name}」的封面。"


def album_cover_cleared_banner(album_name: str) -> str:
    """取消相册封面之后的那行提示：说清它回到了什么行为上（ADR 0028）。"""
    return f"「{album_name}」恢复用里面的第一张照片当封面。"


def metadata_edit_banner(outcome: MetadataWriteOutcome, photo_name: str) -> str:
    """编辑元数据之后的那行提示（ADR 0030 / 0031 / 0032）。

    **只有这一句。** 「接下来会发生什么」由对话框里那行小字负责（保存之前就说清了坐标怎么
    写入）；横幅再说一遍是在动作**之后**才到的，那时它已经不能改变任何决定。横幅只回答三件
    事：**改了哪一部分**、**成了吗**、**能不能撤** ——「改了哪一部分」不是啰嗦：只改时间或
    只改位置时，**没改的那一部分一个字节都没动**。
    """
    if not outcome.written:
        return f"「{photo_name}」的元数据没有改动。"
    what = _edited_what(outcome)
    if not outcome.journaled:
        # 这一句不是重复：它不是「发生了什么」，而是「你刚才按 ⌘Z 会发生什么」。
        return f"已更新「{photo_name}」的{what}。⌘Z 撤销不了：没能记下操作日志。"
    return f"已更新「{photo_name}」的{what}。⌘Z 可以撤销。"


def _edited_what(outcome: MetadataWriteOutcome) -> str:
    """这次动的是拍摄时间、位置、还是两样都动了。"""
    plan = outcome.plan
    match (plan.capture_changed, plan.place_changed):
        case (True, True):
            return "拍摄时间与位置"
        case (True, False):
            return "拍摄时间"
        case _:
            return "位置"


def first_reason(failures: Sequence[Failure]) -> str:
    """失败原因的摘要。

    只有一项时点名道姓（用户得知道该去看哪张照片），多项时只给第一条，完整列表在日志里。
    **三处报回来的失败项是同一个类型**（``metadata.failures.Failure``），所以粘贴、移动、移到
    废纸篓三条路在这里合流。
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


def external_app_opened(outcome: OpenOutcome, name: str) -> str:
    """「用外部应用打开当前这一页」做完之后的那一句（ADR 0034）。

    **降级必须说出来**：开在应用里还是开在浏览器里，用户看到的完全不是同一回事
    （后者是浏览器里一个标签页，前者是那个独立窗口）。**应用名前面留一个空格**（中英混排里
    裸贴在一起读着挤），**后面不留** —— 名字自己以汉字收尾。
    """
    if outcome.detail:
        return f"{outcome.detail}。"
    if outcome.in_app:
        return f"已在 {name}中打开当前这一页。"
    return f"已在浏览器中打开 {name}。"


__all__ = [
    "album_cover_banner",
    "album_cover_cleared_banner",
    "album_name",
    "event_created_banner",
    "event_deleted_banner",
    "event_undo_banner",
    "event_updated_banner",
    "external_app_opened",
    "first_reason",
    "metadata_edit_banner",
    "paste_banner",
    "photos_bound_banner",
    "photos_unbound_banner",
    "rename_banner",
    "transfer_banner",
    "trash_banner",
    "undo_banner",
    "undo_note",
]
