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

from gallery.core.external.launch import OpenOutcome
from gallery.core.library.facade import MetadataWriteOutcome
from gallery.core.library.photos import FolderRef, GridEntry
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import ACTION_METADATA, ACTION_MOVE, ACTION_RENAME
from gallery.core.metadata.paste import PastedFile, PasteResult
from gallery.core.metadata.paste import Skipped as PasteSkipped
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MovedItem, MoveResult
from gallery.core.metadata.transfer import Skipped as MoveSkipped
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

    话怎么拼在 :func:`_into_album_parts` 里，与「复制到相册 / 移动到相册」共用同一份。
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

    与 :func:`paste_banner` 一套说法：动了哪些、跳过了哪些、为什么没成、能不能撤销。
    **改了名的要单独点出来** —— 目标相册里本来就有同名时按访达的规矩改叫「一张 2.jpg」，
    不说清楚的话用户会以为程序认错了文件。

    移动比粘贴多一句「⌘Z 可以撤销」：这是用户敢用它的前提（红线 1，见 ADR 0019 决策 3）。
    这是两条路**唯一**刻意不同的地方，其余全部共用 :func:`_into_album_parts`。
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

    粘贴（⌘V，ADR 0018）与复制 / 移动到相册（ADR 0019）说的是同一件事，于是按同一套
    说法报出来：动了哪些、跳过了哪些、为什么没成、记账记上没有。原来这里是两个几乎
    一样的函数（`transfer_banner` 的文档甚至写着「与 ``_paste_banner`` 一套说法」），
    各写一遍的后果很具体：改了一句措辞忘了另一句，同一个动作于是有两种说法。

    两处**刻意**不同的地方由参数带进来，而不是各留一份：

    - ``done_verb`` 与 ``fail_verb`` **分成两个**：粘贴那一句是「已**粘贴** 3 项」，
      失败那句却是「没**粘**成」；复制 / 移动两条路两处用同一个动词。
    - 失败项一律读 :attr:`Failure.name`。粘贴原先写的是 ``failure.source.name`` ——
      同一个值（``Failure.at()`` 造出来的 ``name`` 正是从 ``source`` 推的），但直接读
      ``name`` 才不必赌 ``source`` 不是 ``None``（分组卡片那一种就没有路径）。

    返回**分段**而不是整句，是为了让调用方能在后面追加自己的那一句（移动的「⌘Z 可以
    撤销」），而不必去拆已经拼好的字符串。
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
    if result.action == ACTION_METADATA:
        # 撤销位置编辑：文件回到原样，但**被别的工具改过的那几个字段留在了那里**。
        # 那句话必须说 —— 否则用户会以为照片已经整个回到刚才那样了。
        if result.failures:
            count = len(result.failures)
            return f"只撤销了一部分：{count} 项在 Gallery 之外被改过，没有动它们{note}。"
        name = result.entries[0].name if result.entries else "照片"
        return f"已撤销，「{name}」的位置恢复原样{note}。"
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


def album_cover_banner(album_name: str, photo_name: str) -> str:
    """设完相册封面之后的那行提示（ADR 0028）。

    **两样都要说出来**：是哪一张成了封面、它是**哪个相册**的封面。用户是在相册内部
    按的右键，只说「已设为封面」的话，他得自己回去确认设的是不是刚才看的那个相册。

    不提撤销：这一条只写 Gallery 自己的配置文件，不在 ⌘Z 那一摞栈里（决策 5），
    说一句「⌘Z 可以撤销」就是在骗人。要改回去是相册右键菜单里的「取消相册封面」。
    """
    return f"已把「{photo_name}」设为「{album_name}」的封面。"


def album_cover_cleared_banner(album_name: str) -> str:
    """取消相册封面之后的那行提示：说清它回到了什么行为上（ADR 0028）。"""
    return f"「{album_name}」恢复用里面的第一张照片当封面。"


def metadata_edit_banner(outcome: MetadataWriteOutcome, photo_name: str) -> str:
    """编辑元数据之后的那行提示（ADR 0030 / 0031 / 0032）。

    **只有这一句。** 「接下来会发生什么」是对话框里的那行小字负责的：保存之前就说清
    「坐标会按这座城市的位置写入」「城市库里没有这个名字，坐标保持原样」，用户是在
    知道后果的前提下按下保存的。横幅再说一遍是重复，而重复的那一遍还是在动作**之后**
    才到 —— 到了那时候它已经不能改变任何决定了。

    横幅只回答三件事：**改了哪一部分**、**成了吗**、**能不能撤**。

    「改了哪一部分」不是啰嗦：同一个对话框里可以只改时间、只改位置、或者两样都改，
    而**没改的那一部分照片上一个字节都没动** —— 说笼统的一句「已更新元数据」，用户
    回头翻那张照片时反而不知道该去核对什么。
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


def external_app_opened(outcome: OpenOutcome, name: str) -> str:
    """「用外部应用打开当前这一页」做完之后的那一句（ADR 0034）。

    **降级必须说出来**：开在应用里还是开在浏览器里，用户看到的完全不是同一回事
    —— 后者是浏览器里一个网页，标签栏还在，而前者是那个独立窗口。把两者说成同一句
    话，下次他就知道「有时候会跳出浏览器」不是出了故障。

    **应用名前面留一个空格**：中英混排里裸贴在一起读着挤（「已在Google 日历…」），
    本仓库的文案也是这么写的（「已把 3 项…」「已拷贝 12 个路径」）。**后面不留** ——
    名字自己以汉字收尾（「Google 日历」），再隔一个空格就成了「日历 中」。
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
    "external_app_opened",
    "first_reason",
    "metadata_edit_banner",
    "paste_banner",
    "rename_banner",
    "transfer_banner",
    "trash_banner",
    "undo_banner",
    "undo_note",
]
