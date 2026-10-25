"""一次动作做完之后，横幅上那**一行中文**是怎么拼出来的。

这些函数**一行 Qt 都没有**（所以能单独测）：把 ``core`` 的结果对象翻成一句话，外加那句用户一定
要知道的补充（记账失败 ⌘Z 撤销不了）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from gallery.core.events.models import shorten_name
from gallery.core.events.orphans import OrphanScan
from gallery.core.events.store import EventWriteOutcome
from gallery.core.external.launch import OpenOutcome
from gallery.core.i18n import t, t_plural
from gallery.core.library.facade import MetadataWriteOutcome, MovieImportOutcome
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
from gallery.core.paths import app_log_file

#: 「搬进相册」那几种动作。**横幅的措辞按它取键** —— 不把「粘贴」这种中文动词当参数传下去。
type AlbumOp = Literal["paste", "copy", "move"]


def _strip_final_stop(text: str) -> str:
    """去掉句末标点：拼进来的那一句外面还会补一个，两个句号读着别扭。"""
    return text.rstrip("。.")


def unexpected_problem() -> str:
    """程序缺陷那一类失败的横幅：**说清去哪儿看详情**，日志现在真的有一份。"""
    return t("banner.unexpected", path=app_log_file())


def rename_banner(result: RenameResult) -> str:
    """改名之后的那行提示。

    记账失败必须说出来：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来 —— 让他以为「随时能
    撤销」比不告诉他更糟。
    """
    key = "banner.rename.journaled" if result.journaled else "banner.rename.not_journaled"
    return t(key, name=result.entry.name)


def undo_note(journaled: bool) -> str:
    return "" if journaled else t("banner.log_failed")


# ---- 事件（ADR 0043）----------------------------------------------------------


def event_created_banner(outcome: EventWriteOutcome) -> str:
    """记完一次之后的那一句。**新建不在撤销栈里**（撤销它就是删），所以这一句不承诺 ⌘Z。"""
    return t("banner.event.created", name=shorten_name(outcome.event.display_name))


def event_updated_banner(outcome: EventWriteOutcome) -> str:
    """改了事件之后。**一句里说清成没成与能不能撤** —— 与元数据那一族同一套规矩。"""
    name = shorten_name(outcome.event.display_name)
    if not outcome.changed:
        return t("banner.event.updated.unchanged", name=name)
    key = (
        "banner.event.updated.journaled"
        if outcome.journaled
        else "banner.event.updated.not_journaled"
    )
    return t(key, name=name)


def event_deleted_banner(outcome: EventWriteOutcome) -> str:
    """删掉事件之后。**连着删掉的照片关联要一起说**：那是用户看不见的损失，不说他不会知道。

    撤销**能把绑定放回来**，所以这一句要**承诺 ⌘Z** ——「删掉 37 张照片上的记录」那句话读着
    吓人，而它其实撤得回来。
    """
    photos = (
        t_plural("banner.event.deleted.photos", len(outcome.unbound)) if outcome.unbound else ""
    )
    head = t(
        "banner.event.deleted.base", name=shorten_name(outcome.event.display_name), photos=photos
    )
    note = t(
        "banner.event.deleted.undoable"
        if outcome.journaled
        else "banner.event.deleted.not_undoable"
    )
    return head + note + t("banner.punct.period")


def photos_bound_banner(count: int, event_name: str, *, by_range: bool = False) -> str:
    """把照片记进一个事件之后。**新记进去几张**说清 —— 已经记着的那些不是这次的成果。

    ``by_range`` 为真时提一句「按日期」：那是几百张的批量操作，用户该知道它为什么这么多。
    """
    name = shorten_name(event_name)
    if count <= 0:
        return t("banner.photos.bound.already", name=name)
    key = "banner.photos.bound.by_range" if by_range else "banner.photos.bound.done"
    return t_plural(key, count, name=name)


def photos_unbound_banner(count: int, event_name: str) -> str:
    """把照片从一个事件里拿出来之后。"""
    name = shorten_name(event_name)
    if count <= 0:
        return t("banner.photos.unbound.none", name=name)
    return t_plural("banner.photos.unbound.done", count, name=name)


def event_cover_set_banner(event_name: str, photo_name: str) -> str:
    """把某张照片设成事件封面之后（ADR 0048）。

    **说清是哪一张**：用户刚点的是网格里那一张，而横幅出现在内容上方 ——
    不点名的话，「已设置封面」在几十张缩略图之上等于没说。
    """
    return t("banner.event.cover.set", photo=photo_name, name=shorten_name(event_name))


def event_cover_cleared_banner(event_name: str) -> str:
    """取消事件封面之后。**说清回到哪儿去** —— 用户要的可能是「换一张」而不是「不要封面」。"""
    return t("banner.event.cover.cleared", name=shorten_name(event_name))


def shows_imported_banner(created: int, duplicates: int, skipped: int) -> str:
    """从「演迹」导入演出之后（ADR 0047）。

    **跳过的那几场、与没被当成演出的那几行都要说一声**，否则用户会以为自己的表格少了几场
    —— 线索在这里，去查哪一行由他自己定。**不提 ⌘Z**：一次导入不进撤销栈（它就是一串
    新建事件），承诺撤销是空话。
    """
    if created == 0 and duplicates == 0 and skipped == 0:
        return t("banner.shows.imported.none")
    head = (
        t_plural("banner.shows.imported.done", created)
        if created > 0
        else t("banner.shows.imported.no_new")
    )
    if duplicates > 0:
        head += t("banner.shows.imported.duplicates", count=duplicates)
    if skipped > 0:
        head += t_plural("banner.shows.imported.skipped", skipped)
    return head + t("banner.punct.period")


def movies_imported_banner(outcome: MovieImportOutcome) -> str:
    """按编号或链接导入影片之后（ADR 0063 / 0068）。

    **跳过的那几部、与抓不到 / 写不下的都要说一声**：用户敲了一批条目，总得知道哪几个成了。
    **不提 ⌘Z**：一次导入不进撤销栈（它就是一串新建事件加几个新文件），承诺撤销是空话。
    """
    parts: list[str] = []
    if outcome.created:
        parts.append(t_plural("banner.movies.imported.created", outcome.created))
    if outcome.duplicates:
        parts.append(t("banner.movies.imported.duplicates", count=outcome.duplicates))
    if not outcome.journaled:
        parts.append(t("banner.log_failed.plain"))
    if outcome.failures:
        more = (
            t_plural("banner.movies.imported.more", len(outcome.failures))
            if len(outcome.failures) > 1
            else ""
        )
        # 失败原因自带句末标点，这里去掉：拼接用的是分号，末尾还会再补一个句号。
        parts.append(f"{_strip_final_stop(outcome.failures[0])}{more}")
    if not parts:
        return t("banner.movies.imported.none")
    return _banner_line(parts)


def movie_site_opened_banner(code: str, outcome: OpenOutcome) -> str:
    """按住 ⌥ 点开一部影片的网站之后（ADR 0063）。"""
    if outcome.detail:
        return t("banner.movie_site.failed", code=code, detail=outcome.detail)
    return t("banner.movie_site.opened", code=code)


def event_undo_banner(result: UndoResult, kind_label: str) -> str:
    """⌘Z 撤掉一次事件改动之后。**两种改动说法不一样** —— 撤的是删除就说「已恢复」。"""
    if result.event is not None:
        what = t(
            "banner.event_undo.what", kind=kind_label, name=shorten_name(result.event.display_name)
        )
    else:
        what = kind_label
    note = t("banner.punct.period") if result.journaled else t("banner.event_undo.log_failed")
    key = (
        "banner.event_undo.restored"
        if result.action == ACTION_EVENT_DELETE
        else "banner.event_undo.changed"
    )
    return t(key, what=what, note=note)


# ---- 孤儿绑定（ADR 0043 决策 9）------------------------------------------------

#: 确认框里列事件名时最多列几个。**再多就只给个数** —— 那份清单是用来判断的，不是用来读的。
MAX_LISTED_EVENTS: int = 3


def orphan_dialog(scan: OrphanScan) -> str:
    """开库时发现孤儿绑定，确认框的正文。**纯文本，不碰 Qt**（与本文件其余部分同一层）。

    **两档分开说，理由写进正文**：用户只有看了这句话才知道默认保留的是什么、勾了会失去什么。
    **不许提 ⌘Z**：这里没有撤销 —— ``delete_event`` 那句承诺在这里是反的。
    """
    lines = [_orphan_headline(scan), "", t("banner.orphan.why")]
    if scan.missing:
        lines += ["", t_plural("banner.orphan.ask", len(scan.missing))]
    if scan.vanished:
        lines += ["", _vanished_paragraph(scan)]
    return "\n".join(lines)


def orphan_dialog_title(scan: OrphanScan) -> str:
    """确认框的标题。**只提「照片没了」那一档** —— 另一个标题会让用户以为两档一样性质。"""
    return t_plural("banner.orphan.title", len(scan.missing))


def orphans_forgotten_banner(removed: int) -> str:
    """清完之后。**说清删的是记录、不是照片** —— 用户怕的是后者。"""
    if removed <= 0:
        return t("banner.orphans.forgotten.none")
    return t_plural("banner.orphans.forgotten.done", removed)


def _orphan_headline(scan: OrphanScan) -> str:
    names = scan.event_titles(include_vanished=False)
    if not names:
        return t("banner.orphan.head.any")
    return t_plural("banner.orphan.head.named", len(scan.missing), names=_list_names(names))


def _vanished_paragraph(scan: OrphanScan) -> str:
    """第二档也要点名事件 —— 用户得知道「是哪个事件的照片不见了」才可能去核实。"""
    names = scan.event_titles(include_vanished=True, only=("vanished",))
    who = t("banner.orphan.vanished.who", names=_list_names(names)) if names else ""
    return t_plural("banner.orphan.vanished", len(scan.vanished), who=who)


def _list_names(names: Sequence[str]) -> str:
    shown = t("banner.orphan.list.separator").join(
        t("banner.quote", text=name) for name in names[:MAX_LISTED_EVENTS]
    )
    if len(names) > MAX_LISTED_EVENTS:
        shown += t_plural("banner.orphan.list.more", len(names))
    return shown


def paste_banner(result: PasteResult, target_name: str) -> str:
    """粘贴之后的那行提示：粘了哪些、为什么没成。拼法在 :func:`_into_album_parts`。

    **改了名的要单独点出来**：用户要求的是「不覆盖」，系统给出的新名字得让他看见。
    **「本来就在这一层的那几项」不是空操作**（ADR 0044），它们按副本报出来。
    """
    return _banner_line(
        _into_album_parts(
            result.pasted,
            result.failures,
            journaled=result.journaled,
            target_name=target_name,
            op="paste",
        )
    )


def duplicate_banner(result: PasteResult) -> str:
    """「复制」⌘D 之后的那行提示（ADR 0044）。

    **不套 :func:`_into_album_parts`**：这里没有目标相册，每项的目标是它自己所在的那一层。
    单项时把新名字点出来。**这一句不承诺 ⌘Z**：撤销一次复制就是删文件，违反「绝不删除、移动
    用户照片」。
    """
    parts: list[str] = []
    if result.pasted:
        head = t_plural("banner.duplicate.created", len(result.pasted))
        if len(result.pasted) == 1:
            head += t("banner.duplicate.named", name=result.pasted[0].created.name)
        parts.append(head)
    if result.failures:
        parts.append(_failure_clause(result.failures, op="copy"))
    if not result.journaled:
        parts.append(t("banner.log_failed.plain"))
    return _banner_line(parts)


def transfer_banner(result: MoveResult | PasteResult, target_name: str, op: AlbumOp) -> str:
    """「复制到相册 / 移动到相册」之后的那行提示（ADR 0019）。

    与 :func:`paste_banner` 一套说法。移动比粘贴多一句「⌘Z 可以撤销」——这是用户敢用它的
    前提（ADR 0019 决策 3 对「绝不移动用户照片」的豁免）。**那句话只在真的移走了东西时才说**
    （ADR 0044）：只复制的那一批没东西进撤销栈，说「⌘Z 可以撤销」就是许一个不存在的承诺。
    """
    moved = result.moved if isinstance(result, MoveResult) else result.pasted
    parts = _into_album_parts(
        moved,
        result.failures,
        journaled=result.journaled,
        target_name=target_name,
        op=op,
    )
    copied = result.copied if isinstance(result, MoveResult) else ()
    if copied:
        parts.append(_stayed_put_clause(copied, target_name))
    if isinstance(result, MoveResult) and result.moved and result.journaled:
        parts.append(t("banner.undoable"))
    return _banner_line(parts)


def _stayed_put_clause(copied: Sequence[PastedFile], target_name: str) -> str:
    """「移动到相册」而目标正是它所在的那一层：不说跳过，说复制了一份（ADR 0044）。"""
    if len(copied) == 1:
        item = copied[0]
        return t(
            "banner.album.stayed.single",
            name=item.source.name,
            target=target_name,
            created=item.created.name,
        )
    return t_plural("banner.album.stayed.many", len(copied), target=target_name)


def _failure_clause(failures: Sequence[Failure], *, op: AlbumOp) -> str:
    """没成的那些：只报第一条（更多就带上条数）—— 一行横幅放不下更多，而失败原因往往同源。

    一律读 :attr:`Failure.name`：直接读它才不必赌 ``source`` 不是 ``None``（分组卡片那一种就
    没有路径）。
    """
    first = failures[0]
    more = t_plural("banner.album.failure.more", len(failures)) if len(failures) > 1 else ""
    return t(
        f"banner.album.failed.{op}",
        name=first.name,
        more=more,
        reason=_strip_final_stop(first.message),
    )


def _into_album_parts(
    moved: Sequence[PastedFile | MovedItem],
    failures: Sequence[Failure],
    *,
    journaled: bool,
    target_name: str,
    op: AlbumOp,
) -> list[str]:
    """「把若干项搬进某个相册」之后那句话的**唯一一份算法**。

    粘贴（ADR 0018）与复制 / 移动到相册（ADR 0019）共用它；两处刻意不同的地方由 ``op``
    带进来。返回**分段**而不是整句，是为了让调用方追加自己那句。
    """
    parts: list[str] = []
    if moved:
        head = t_plural(f"banner.album.done.{op}", len(moved), target=target_name)
        renamed = [item for item in moved if item.renamed]
        if len(renamed) == 1:
            head += t(
                "banner.album.renamed.one",
                source=renamed[0].source.name,
                created=renamed[0].created.name,
            )
        elif renamed:
            head += t_plural("banner.album.renamed.many", len(renamed))
        parts.append(head)
    if failures:
        parts.append(_failure_clause(failures, op=op))
    if not journaled:
        parts.append(t("banner.log_failed.plain"))
    return parts


def _banner_line(parts: list[str]) -> str:
    """把几段话拼成横幅上那一行：分号隔开，末尾一个句号；两个标点都随语言走。"""
    separator = t("banner.punct.separator")
    return separator.join(parts) + t("banner.punct.period")


def trash_banner(result: TrashResult) -> str:
    """移到废纸篓之后的那行提示：动了多少、能不能撤销、没成的那些为什么没成。

    废纸篓本身是第二层保险，但用户不该靠猜。
    """
    moved = result.moved
    if not moved:
        key = (
            "banner.trash.none.journaled" if result.journaled else "banner.trash.none.not_journaled"
        )
        return t(key, reason=first_reason(result.failures))
    if any(isinstance(entry, FolderRef) for entry in moved):
        head = t("banner.trash.album", name=album_name(moved))
    elif len(moved) == 1:
        head = t("banner.trash.single", name=moved[0].name)
    else:
        head = t_plural("banner.trash.many", len(moved))
    if result.failures:
        head += t_plural(
            "banner.trash.failures", len(result.failures), reason=first_reason(result.failures)
        )
    # 不能一边说「⌘Z 可撤销」一边说「不可撤销」，所以这两半是一起给的。
    hint = t(
        "banner.trash.hint.journaled" if result.journaled else "banner.trash.hint.not_journaled"
    )
    return head + t("banner.punct.period") + hint


def undo_banner(result: UndoResult) -> str:
    """撤销之后的那行提示。"""
    note = undo_note(result.journaled)
    if result.action == ACTION_METADATA:
        # 撤销位置编辑：文件回到原样，但**被别的工具改过的那几个字段留在了那里** —— 不说的话
        # 用户会以为照片已经整个回到刚才那样了。
        if result.failures:
            return t_plural("banner.undo.partial", len(result.failures), note=note)
        name = result.entries[0].name if result.entries else "照片"
        return t("banner.undo.metadata", name=name, note=note)
    if result.action == ACTION_RENAME:
        return t("banner.undo.rename", name=result.entries[0].name, note=note)
    if result.action == ACTION_MOVE:
        # 撤销移动没有 entries：挪回来的东西**不一定在当前这一层**，界面不知道该往网格里插
        # 哪一行 —— 交给监听器（ADR 0013）去发现，横幅只说发生了什么。
        if result.failures:
            return t("banner.undo.move.failed", reason=first_reason(result.failures), note=note)
        return t("banner.undo.move.done", note=note)
    restored = result.entries
    if not restored:
        return t("banner.undo.restore.failed", reason=first_reason(result.failures), note=note)
    if len(restored) == 1:
        head = t("banner.undo.restore.one", name=restored[0].name)
    else:
        head = t_plural("banner.undo.restore.many", len(restored))
    if result.failures:
        head += t_plural(
            "banner.undo.restore.failures",
            len(result.failures),
            reason=first_reason(result.failures),
        )
    return head + note + t("banner.punct.period")


def album_cover_banner(album_name: str, photo_name: str) -> str:
    """设完相册封面之后的那行提示（ADR 0028）。

    **两样都要说出来**：是哪一张成了封面、它是**哪个相册**的封面 —— 只说「已设为封面」的话，
    用户得自己回去确认设的是不是刚才看的那个相册。不提撤销：这一条只写 Gallery 自己的配置
    文件，不在 ⌘Z 那一摞栈里（ADR 0028 决策 5），要改回去走「取消相册封面」。
    """
    return t("banner.album_cover.set", photo=photo_name, album=album_name)


def album_cover_cleared_banner(album_name: str) -> str:
    """取消相册封面之后的那行提示：说清它回到了什么行为上（ADR 0028）。"""
    return t("banner.album_cover.cleared", album=album_name)


def metadata_edit_banner(outcome: MetadataWriteOutcome, photo_name: str) -> str:
    """编辑元数据之后的那行提示（ADR 0030 / 0031 / 0032）。

    「接下来会发生什么」由对话框里那行小字负责；横幅只回答**改了哪一部分**、**成了吗**、
    **能不能撤**。只改时间或只改位置时，**没改的那一部分一个字节都没动**。
    """
    if not outcome.written:
        return t("banner.metadata.none", name=photo_name)
    key = "banner.metadata.journaled" if outcome.journaled else "banner.metadata.not_journaled"
    return t(key, name=photo_name, what=_edited_what(outcome))


def _edited_what(outcome: MetadataWriteOutcome) -> str:
    """这次动的是拍摄时间、位置、还是两样都动了。"""
    plan = outcome.plan
    match (plan.capture_changed, plan.place_changed):
        case (True, True):
            return t("banner.metadata.what.both")
        case (True, False):
            return t("banner.metadata.what.capture_time")
        case _:
            return t("banner.metadata.what.place")


def first_reason(failures: Sequence[Failure]) -> str:
    """失败原因的摘要。

    只有一项时点名道姓（用户得知道该去看哪张照片），多项时只给第一条，完整列表在日志里。
    **三处报回来的失败项是同一个类型**（``metadata.failures.Failure``），所以粘贴、移动、移到
    废纸篓三条路在这里合流。
    """
    if not failures:
        return t("banner.reason.unknown")
    reason = _strip_final_stop(failures[0].message)
    if len(failures) == 1:
        return t("banner.reason.named", name=failures[0].name, reason=reason)
    return t("banner.reason.some", reason=reason)


def album_name(entries: Sequence[GridEntry]) -> str:
    """一批条目里的相册名（可能混着照片，取第一个相册的）。"""
    for entry in entries:
        if isinstance(entry, FolderRef):
            return entry.display_name
    return ""


def map_opened(outcome: OpenOutcome, name: str) -> str:
    """「在地图中打开拍摄位置」做完之后的那一句。

    **与 :func:`external_app_opened` 分开**：那句说的是「当前这一页」，而这里打开的是
    这一张照片的拍摄位置，套上去会说错对象。开在浏览器里的那一支照旧说清。
    """
    if outcome.detail:
        return t("banner.detail", detail=outcome.detail)
    key = "banner.map.opened.app" if outcome.in_app else "banner.map.opened.browser"
    return t(key, name=name)


def external_app_opened(outcome: OpenOutcome, name: str) -> str:
    """「用外部应用打开当前这一页」做完之后的那一句（ADR 0034）。

    **降级必须说出来**：开在应用里还是开在浏览器里，用户看到的完全不是同一回事
    （后者是浏览器里一个标签页，前者是那个独立窗口）。**应用名前面留一个空格**（中英混排里
    裸贴在一起读着挤），**后面不留** —— 名字自己以汉字收尾。
    """
    if outcome.detail:
        return t("banner.detail", detail=outcome.detail)
    key = "banner.external.opened.app" if outcome.in_app else "banner.external.opened.browser"
    return t(key, name=name)


def calendar_opened(outcome: OpenOutcome, name: str) -> str:
    """「打开到这一页对应的日期」做完之后的那一句。

    **与 :func:`external_app_opened` 分开**：那句说的是「当前页」，而这里打开的是那一天；
    也**不在应用名前面留空格** —— 这一项的名字是汉字，「已在 日历 中」读着断一口气。
    """
    if outcome.detail:
        return t("banner.detail", detail=outcome.detail)
    return t("banner.calendar.opened", name=name)


__all__ = [
    "album_cover_banner",
    "album_cover_cleared_banner",
    "album_name",
    "calendar_opened",
    "duplicate_banner",
    "event_cover_cleared_banner",
    "event_cover_set_banner",
    "event_created_banner",
    "event_deleted_banner",
    "event_undo_banner",
    "event_updated_banner",
    "external_app_opened",
    "first_reason",
    "map_opened",
    "metadata_edit_banner",
    "movie_site_opened_banner",
    "movies_imported_banner",
    "paste_banner",
    "photos_bound_banner",
    "photos_unbound_banner",
    "rename_banner",
    "shows_imported_banner",
    "transfer_banner",
    "trash_banner",
    "undo_banner",
    "undo_note",
    "unexpected_problem",
]
