"""一次动作做完之后，横幅上那**一行中文**是怎么拼出来的。

这些函数**一行 Qt 都没有**（所以能单独测）：把 ``core`` 的结果对象翻成一句话，外加那句用户一定
要知道的补充（记账失败 ⌘Z 撤销不了）。
"""

from __future__ import annotations

from collections.abc import Sequence

from gallery.core.events.orphans import OrphanScan
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
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MovedItem, MoveResult
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

    撤销**能把绑定放回来**，所以这一句要**承诺 ⌘Z** ——「删掉 37 张照片上的记录」那句话读着
    吓人，而它其实撤得回来。
    """
    what = f"「{outcome.event.display_name}」"
    photos = f"，连带从 {len(outcome.unbound)} 张照片上移掉了它的记录" if outcome.unbound else ""
    note = "。⌘Z 可以撤销" if outcome.journaled else "。⌘Z 撤销不了：没能记下操作日志"
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


def shows_imported_banner(created: int, duplicates: int, skipped: int) -> str:
    """从「演迹」导入演出之后（ADR 0047）。

    **跳过的那几场、与没被当成演出的那几行都要说一声**，否则用户会以为自己的表格少了几场
    —— 线索在这里，去查哪一行由他自己定。**不提 ⌘Z**：一次导入不进撤销栈（它就是一串
    新建事件），承诺撤销是空话。
    """
    if created == 0 and duplicates == 0 and skipped == 0:
        return "表格里没有读到能记下来的演出。"
    head = f"已从「演迹」导入 {created} 场演出" if created > 0 else "没有新增演出"
    if duplicates > 0:
        head += f"，跳过 {duplicates} 场已存在的"
    if skipped > 0:
        head += f"，另有 {skipped} 行没导入"
    return f"{head}。"


def event_undo_banner(result: UndoResult, kind_label: str) -> str:
    """⌘Z 撤掉一次事件改动之后。**两种改动说法不一样** —— 撤的是删除就说「回来了」。"""
    what = (
        f"{kind_label}「{result.event.display_name}」" if result.event is not None else kind_label
    )
    verb = "已恢复" if result.action == ACTION_EVENT_DELETE else "已把"
    if not result.journaled:
        return f"{verb}{what}改回去，但没能记下操作日志，下一次 ⌘Z 不一定接得上。"
    return f"{verb}{what}改回去。"


# ---- 孤儿绑定（ADR 0043 决策 9）------------------------------------------------

#: 确认框里列事件名时最多列几个。**再多就只给个数** —— 那份清单是用来判断的，不是用来读的。
MAX_LISTED_EVENTS: int = 3


def orphan_dialog(scan: OrphanScan) -> str:
    """开库时发现孤儿绑定，确认框的正文。**纯文本，不碰 Qt**（与本文件其余部分同一层）。

    **两档分开说，理由写进正文**：「相册已不存在」那一档混着「那个盘没挂上」，用户只有看了
    这句话才知道默认保留的是什么、以及勾了会失去什么。

    **不许提 ⌘Z**：这里没有撤销 —— 把绑定恢复到一张不存在的照片上用户用不上，而
    ``delete_event`` 那句「⌘Z 可以…」在这里是反的。
    """
    lines = [_orphan_headline(scan), "", _why_it_matters()]
    if scan.missing:
        lines += ["", f"要清掉这 {len(scan.missing)} 条吗？"]
    if scan.vanished:
        lines += ["", _vanished_paragraph(scan)]
    return "\n".join(lines)


def orphan_dialog_title(scan: OrphanScan) -> str:
    """确认框的标题。**只提「照片没了」那一档** —— 另一个标题会让用户以为两档一样性质。"""
    return f"{len(scan.missing)} 条事件记录指向已经不存在的照片"


def orphans_forgotten_banner(removed: int) -> str:
    """清完之后。**说清删的是记录、不是照片** —— 用户怕的是后者。"""
    if removed <= 0:
        return "没有清掉任何记录。"
    return f"已清掉 {removed} 条指向已不存在照片的记录（照片本身没有动过）。"


def _orphan_headline(scan: OrphanScan) -> str:
    names = scan.event_titles(include_vanished=False)
    if not names:
        return "有一些事件记录指向已经不存在的照片。"
    return f"{len(scan.missing)} 条事件记录指向已经不存在的照片，涉及{_list_names(names)}。"


def _why_it_matters() -> str:
    """说清「为什么值得清」—— 不说的话用户只会想「不动它不就完了」。"""
    return (
        "照片可能是在 Gallery 之外被删掉或移走的。留着没有坏处，\n"
        "但同一个路径日后被另一张照片用上时，它会悄悄算进那个事件。"
    )


def _vanished_paragraph(scan: OrphanScan) -> str:
    """第二档也要点名事件 —— 用户得知道「是哪个事件的照片不见了」才可能去核实。"""
    names = scan.event_titles(include_vanished=True, only=("vanished",))
    who = f"，涉及{_list_names(names)}" if names else ""
    return (
        f"另有 {len(scan.vanished)} 条指向已经不存在的相册{who}。\n"
        "那也可能是那个磁盘没挂上，所以默认保留 —— 要连它们一起清掉吗？"
    )


def _list_names(names: Sequence[str]) -> str:
    shown = "、".join(f"「{name}」" for name in names[:MAX_LISTED_EVENTS])
    if len(names) > MAX_LISTED_EVENTS:
        shown += f"等 {len(names)} 个事件"
    return shown


def paste_banner(result: PasteResult, target_name: str) -> str:
    """粘贴之后的那行提示：粘了哪些、为什么没成。

    **改了名的要单独点出来**：用户要求的是「不覆盖」，而系统给出的新名字得让他看见。话怎么
    拼在 :func:`_into_album_parts` 里，与「复制到相册 / 移动到相册」共用同一份。

    **「本来就在这一层的那几项」不再是空操作**（ADR 0044），它们混在 ``pasted`` 里按
    「一张的副本.jpg」报出来 —— 用户要的是「做了一件事」，横幅说「什么都没做」就是撒谎。
    """
    return _banner_line(
        _into_album_parts(
            result.pasted,
            result.failures,
            journaled=result.journaled,
            target_name=target_name,
            done_verb="粘贴",
            fail_verb="粘",
        )
    )


def duplicate_banner(result: PasteResult) -> str:
    """「复制」⌘D 之后的那行提示（ADR 0044）。

    **不套 :func:`_into_album_parts`**：那句话的骨架是「到「某个相册」」，而这里压根没有目标相册
    —— 每一项的目标就是它自己所在的那一层，有几个不同的父目录就得说几个名字，凑成一句反而更绕。

    单项时把新名字点出来（那一项往往就是用户刚点的那个），多项只报个数。

    **这一句不承诺 ⌘Z**：撤销一次复制就是删文件，红线 1。
    """
    parts: list[str] = []
    if result.pasted:
        head = f"已复制 {len(result.pasted)} 份副本"
        if len(result.pasted) == 1:
            head += f"（{result.pasted[0].created.name}）"
        parts.append(head)
    if result.failures:
        parts.append(_failure_clause(result.failures, fail_verb="复制"))
    if not result.journaled:
        parts.append("没能记下操作日志")
    return _banner_line(parts)


def transfer_banner(result: MoveResult | PasteResult, target_name: str, verb: str) -> str:
    """「复制到相册 / 移动到相册」之后的那行提示（ADR 0019）。

    与 :func:`paste_banner` 一套说法（含「改了名的要单独点出来」）。移动比粘贴多一句
    「⌘Z 可以撤销」—— 这是用户敢用它的前提（红线 1，见 ADR 0019 决策 3），也是两条路唯一
    刻意不同的地方。

    **那句话只在真的移走了东西时才说**（ADR 0044）：只复制的那一批没有任何东西进过撤销栈，
    说「⌘Z 可以撤销」就是许了一个不存在的承诺。
    """
    moved = result.moved if isinstance(result, MoveResult) else result.pasted
    parts = _into_album_parts(
        moved,
        result.failures,
        journaled=result.journaled,
        target_name=target_name,
        done_verb=verb,
        fail_verb=verb,
    )
    copied = result.copied if isinstance(result, MoveResult) else ()
    if copied:
        parts.append(_stayed_put_clause(copied, target_name))
    if isinstance(result, MoveResult) and result.moved and result.journaled:
        parts.append("⌘Z 可以撤销")
    return _banner_line(parts)


def _stayed_put_clause(copied: Sequence[PastedFile], target_name: str) -> str:
    """「移动到相册」而目标正是它所在的那一层：不说跳过，说复制了一份（ADR 0044）。"""
    if len(copied) == 1:
        item = copied[0]
        return f"{item.source.name} 本来就在「{target_name}」里，已复制一份（{item.created.name}）"
    return f"另有 {len(copied)} 项本来就在「{target_name}」里，各复制了一份"


def _failure_clause(failures: Sequence[Failure], *, fail_verb: str) -> str:
    """没成的那些：只报第一条（更多就带上条数）—— 一行横幅放不下更多，而失败原因往往同源。

    一律读 :attr:`Failure.name`：直接读它才不必赌 ``source`` 不是 ``None``（分组卡片那一种就
    没有路径）。
    """
    first = failures[0]
    more = f"，共 {len(failures)} 项" if len(failures) > 1 else ""
    return f"{first.name} 没{fail_verb}成{more}：{first.message.rstrip('。')}"


def _into_album_parts(
    moved: Sequence[PastedFile | MovedItem],
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
    「已**粘贴** 3 项」而失败那句是「没**粘**成」。返回**分段**而不是整句，是为了让调用方能
    在后面追加自己那一句（移动的「⌘Z 可以撤销」、副本的那句「本来就在这里」）。
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
    if failures:
        parts.append(_failure_clause(failures, fail_verb=fail_verb))
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
    "duplicate_banner",
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
    "shows_imported_banner",
    "transfer_banner",
    "trash_banner",
    "undo_banner",
    "undo_note",
]
