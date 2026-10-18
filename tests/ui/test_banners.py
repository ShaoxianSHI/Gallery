"""横幅上那行中文的测试。

这些断言**在 ``banners`` 还是 ``main_window`` 顶部那七个私有函数的时候是写不出来的** ——
测一句措辞就要造一个 ``MainWindow``（要 Qt、要一个真的库、要事件循环转起来），
而「记账失败时那句提示必须出现」是一条纯粹关于措辞的规矩。
把它们抽出来才有了这些用例，这正是拆分的收益本身。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.events.models import shorten_name
from gallery.core.events.store import Event, EventWriteOutcome
from gallery.core.external.launch import WHERE_APP, WHERE_BROWSER, OpenOutcome
from gallery.core.library.facade import MetadataWriteOutcome
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import ACTION_METADATA, ACTION_MOVE, ACTION_RENAME
from gallery.core.metadata.metadata_edit import MetadataPlan
from gallery.core.metadata.paste import PastedFile, PasteResult
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MovedItem, MoveResult
from gallery.core.metadata.trash import TrashResult
from gallery.core.metadata.undo import UndoResult
from gallery.core.metadata.writeback import CAPTURE_TIME_TAGS, PLACE_TAGS, Assignment
from gallery.ui import banners
from gallery.ui.banners import metadata_edit_banner, undo_banner


def _photo(path: Path) -> PhotoRef:
    return PhotoRef(path=path, size=10, mtime_ns=1)


def _album(path: Path) -> FolderRef:
    return FolderRef(path=path, mtime_ns=1)


def test_a_failed_journal_says_so_because_undo_is_gone(tmp_path: Path) -> None:
    """**记账失败必须说出来**：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来。

    让他以为「随时能撤销」比不告诉他更糟 —— 他会先去做别的，回头发现找不回来。
    """
    before = _photo(tmp_path / "改名前.jpg")
    after = _photo(tmp_path / "改名后.jpg")

    ok = RenameResult(entry=after, previous=before, renamed=True, journaled=True)
    lost = RenameResult(entry=after, previous=before, renamed=True, journaled=False)

    assert banners.rename_banner(ok) == "已重命名为「改名后.jpg」。"
    assert "⌘Z 撤销不了" in banners.rename_banner(lost)


def test_the_undo_note_is_empty_when_everything_worked() -> None:
    """正常时不啰嗦 —— 横幅只有一行容身之地。"""
    assert banners.undo_note(True) == ""
    assert "没能记下操作日志" in banners.undo_note(False)


def test_one_failure_names_it_and_several_do_not(tmp_path: Path) -> None:
    """**只有一项时点名道姓**：用户得知道该去看哪张照片。

    多项时横幅只有一行，说「其中一项：磁盘满了」比列三个文件名有用得多 ——
    完整列表在日志里。
    """
    one = [Failure.at(tmp_path / "京都" / "一张.jpg", "磁盘满了，腾出点空间再试。")]
    many = one + [Failure.at(tmp_path / "奈良" / "两张.jpg", "没有权限（Permission denied）。")]

    assert banners.first_reason(one) == "「一张.jpg」：磁盘满了，腾出点空间再试"
    assert banners.first_reason(many).startswith("其中一项：")
    assert banners.first_reason([]) == "原因不明"


def test_the_first_reason_drops_the_trailing_full_stop(tmp_path: Path) -> None:
    """去掉句末的句号 —— 横幅那句外面还会接一句「⌘Z 撤销不了」，两个句号读着别扭。"""
    text = banners.first_reason([Failure.at(tmp_path / "a.jpg", "磁盘满了。")])

    assert not text.endswith("。")


def test_a_paste_that_renamed_a_collision_says_so(tmp_path: Path) -> None:
    """目标里已经有同名文件时会自动改名，**这件事要在横幅上说明**。

    不说清楚的话，相册里凭空多出一张「一张 2.jpg」会让人以为程序认错了文件。
    """
    result = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a 2.jpg"),)
    )

    text = banners.paste_banner(result, "京都")

    assert "京都" in text
    assert "改名为「a 2.jpg」" in text


def test_a_paste_where_everything_already_was_still_reports_what_it_made(
    tmp_path: Path,
) -> None:
    """「本来就在这一层」不再是空操作（ADR 0044），所以横幅说的是**造出来的那几份**。

    早先这一形状是「全被跳过」，横幅得压着不说「已粘贴」；现在没有跳过这回事了 ——
    用户按了 ⌘V 就该看到「做了什么」，而不是「什么都没做」。
    """
    result = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),)
    )

    text = banners.paste_banner(result, "京都")

    assert "已粘贴" in text
    assert "改名为「a的副本.jpg」" in text


def test_the_duplicate_banner_names_the_copy_it_made(tmp_path: Path) -> None:
    """单项时把新名字点出来：用户刚点的就是那一项，看不见新名字等于没做完。"""
    result = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),)
    )

    text = banners.duplicate_banner(result)

    assert text == "已复制 1 份副本（a的副本.jpg）。"


def test_the_duplicate_banner_counts_when_there_are_many(tmp_path: Path) -> None:
    """多项只报个数 —— 逐个列出来一行横幅放不下，而失败项已经由那句带出来了。"""
    result = PasteResult(
        pasted=(
            PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),
            PastedFile(source=tmp_path / "b.jpg", created=tmp_path / "b的副本.jpg"),
        )
    )

    text = banners.duplicate_banner(result)

    assert text == "已复制 2 份副本。"


def test_the_duplicate_banner_never_promises_undo(tmp_path: Path) -> None:
    """**撤销一次复制就是删文件**（删除只许用户亲手触发），所以这句话不许出现「⌘Z 可以撤销」。"""
    result = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),)
    )

    assert "⌘Z" not in banners.duplicate_banner(result)


def test_the_duplicate_banner_says_about_the_journal_when_it_could_not_write_it(
    tmp_path: Path,
) -> None:
    """记不上账不等于没做成，但用户得知道 ⌘Z 这条路在这里不存在（写回必须先记账再动文件那条）。"""
    result = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),),
        journaled=False,
    )

    assert "没能记下操作日志" in banners.duplicate_banner(result)


def test_a_move_that_only_copied_does_not_offer_undo(tmp_path: Path) -> None:
    """只复制的那一批**没有任何东西进过撤销栈**，说「⌘Z 可以撤销」是许了个不存在的承诺
    （ADR 0044）。这一条量的是「⌘Z 撤不回副本」这件事必须被说出来，而不是被藏起来。
    """
    result = MoveResult(
        copied=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),)
    )

    text = banners.transfer_banner(result, "京都", "移动")

    assert "⌘Z" not in text
    assert "已复制一份" in text


def test_a_move_that_did_both_still_offers_undo(tmp_path: Path) -> None:
    """真的移走了东西时那句话照旧要说 —— 那是用户敢用「移动」的前提（ADR 0019 决策 3）。"""
    result = MoveResult(
        moved=(MovedItem(source=tmp_path / "b.jpg", created=tmp_path / "京都" / "b.jpg"),),
        copied=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "a的副本.jpg"),),
    )

    text = banners.transfer_banner(result, "京都", "移动")

    assert "⌘Z 可以撤销" in text
    assert "已移动 1 项" in text
    assert "已复制一份" in text


def test_the_trash_banner_knows_when_an_album_went_as_a_whole(tmp_path: Path) -> None:
    """移走的是整个相册时要说「相册和里面的内容」—— 用户要知道是一起没的。"""
    album = _album(tmp_path / "京都")
    photo_only = _photo(tmp_path / "奈良" / "两张.jpg")

    album_text = banners.trash_banner(
        TrashResult(moved=(album, _photo(tmp_path / "京都" / "一.jpg")))
    )
    photo_text = banners.trash_banner(TrashResult(moved=(photo_only,)))

    assert "相册「京都」和里面的内容" in album_text
    assert "相册" not in photo_text


def test_trashing_nothing_says_why_rather_than_pretending_it_worked(tmp_path: Path) -> None:
    """**什么都没移走时要说出原因**，而不是报一次「已移到废纸篓」。"""
    result = TrashResult(failures=(Failure.at(tmp_path / "a.jpg", "磁盘满了，腾出点空间再试。"),))

    text = banners.trash_banner(result)

    assert text.startswith("没有移走任何东西：")
    assert "磁盘满" in text


def test_the_undo_banner_distinguishes_rename_from_move(tmp_path: Path) -> None:
    """撤销出来的两种结果说法不同（改名 / 移动），别混成一句。

    撤销移动**没有 entries**：挪回来的东西不一定在当前这一层，界面不知道该往网格里
    插哪一行（交给监听器去发现），所以横幅只能说发生了什么。
    """
    renamed = banners.undo_banner(
        UndoResult(action=ACTION_RENAME, entries=(_photo(tmp_path / "原名.jpg"),))
    )
    moved = banners.undo_banner(UndoResult(action=ACTION_MOVE, entries=()))

    assert "恢复为「原名.jpg」" in renamed
    assert "挪回原来的相册" in moved


def test_album_name_is_empty_when_there_is_no_album(tmp_path: Path) -> None:
    """混着照片时取第一个相册的；一个相册都没有就说不出名字（空串）。"""
    mixed = [_photo(tmp_path / "京都" / "一张.jpg"), _album(tmp_path / "奈良")]
    photos = [_photo(tmp_path / "京都" / "一张.jpg"), _photo(tmp_path / "奈良" / "两张.jpg")]

    assert banners.album_name(mixed) == "奈良"
    assert banners.album_name(photos) == ""


def test_a_transfer_that_only_partly_happened_says_so(tmp_path: Path) -> None:
    """**部分成功要如实说**：横幅只给一条，但要说清移了几项、哪一项没移成。"""
    result = MoveResult(
        moved=(MovedItem(source=tmp_path / "a.jpg", created=tmp_path / "京都" / "a.jpg"),),
        failures=(Failure.at(tmp_path / "b.jpg", "磁盘满了，腾出点空间再试。"),),
    )

    text = banners.transfer_banner(result, "京都", verb="移动")

    assert "京都" in text
    assert "b.jpg" in text


def test_moving_promises_that_it_can_be_undone(tmp_path: Path) -> None:
    """移动比粘贴多一句「⌘Z 可以撤销」—— **这是用户敢用它的前提**（ADR 0019）。"""
    result = MoveResult(
        moved=(MovedItem(source=tmp_path / "a.jpg", created=tmp_path / "京都" / "a.jpg"),)
    )
    paste = PasteResult(
        pasted=(PastedFile(source=tmp_path / "a.jpg", created=tmp_path / "京都" / "a.jpg"),)
    )

    assert "撤销" in banners.transfer_banner(result, "京都", verb="移动")
    assert "撤销" not in banners.transfer_banner(paste, "京都", verb="复制")


def test_the_cover_banner_names_both_the_photo_and_its_album() -> None:
    """用户是在**相册内部**按的右键：只说「已设为封面」他得自己回去确认设的是哪个相册。"""
    text = banners.album_cover_banner("京都", "一张.jpg")

    assert text == "已把「一张.jpg」设为「京都」的封面。"


def test_the_cover_banner_never_promises_an_undo() -> None:
    """**不提撤销**：这一条写的是 Gallery 自己的配置文件，不在 ⌘Z 那一摞栈里
    （ADR 0028 决策 5）。说「⌘Z 可以撤销」就是在骗用户。"""
    assert "撤销" not in banners.album_cover_banner("京都", "一张.jpg")
    assert "撤销" not in banners.album_cover_cleared_banner("京都")


def test_clearing_the_cover_says_what_it_fell_back_to() -> None:
    """取消之后得说清它回到了**什么行为**上 —— 「第一张照片」，而不是「已取消」。"""
    assert banners.album_cover_cleared_banner("京都") == "「京都」恢复用里面的第一张照片当封面。"


# ---- 编辑元数据（ADR 0030 / 0031 / 0032）------------------------------------------------


def _outcome(
    *,
    written: bool = True,
    journaled: bool = True,
    capture_changed: bool = False,
    place_changed: bool = True,
) -> MetadataWriteOutcome:
    tags = [
        *(CAPTURE_TIME_TAGS if capture_changed else ()),
        *(PLACE_TAGS if place_changed else ()),
    ]
    return MetadataWriteOutcome(
        entry=PhotoRef(path=Path("/库/一张.jpg"), size=1, mtime_ns=2),
        plan=MetadataPlan(assignments=tuple(Assignment(tag, "x") for tag in tags)),
        journaled=journaled,
        written=written,
    )


def test_the_metadata_edit_banner_is_one_line_and_nothing_else() -> None:
    """**只有这一句。**

    「接下来会发生什么」是对话框里那行小字的事：保存之前就说清了，用户是在知道后果的
    前提下按的保存。横幅再说一遍是重复，而且重复的那一遍还在动作**之后**才到 ——
    那时候它已经不能改变任何决定了。
    """
    assert metadata_edit_banner(_outcome(), "一张.jpg") == "已更新「一张.jpg」的位置。⌘Z 可以撤销。"


def test_the_banner_says_when_it_cannot_be_undone() -> None:
    """这一句不是重复：它不是「发生了什么」，而是「你刚才按 ⌘Z 会发生什么」。"""
    text = metadata_edit_banner(_outcome(journaled=False), "一张.jpg")

    assert text == "已更新「一张.jpg」的位置。⌘Z 撤销不了：没能记下操作日志。"


def test_the_banner_says_nothing_changed_when_nothing_did() -> None:
    assert (
        metadata_edit_banner(_outcome(written=False), "一张.jpg")
        == "「一张.jpg」的元数据没有改动。"
    )


@pytest.mark.parametrize(
    ("capture_changed", "place_changed", "what"),
    [
        (True, False, "拍摄时间"),
        (False, True, "位置"),
        (True, True, "拍摄时间与位置"),
    ],
)
def test_the_banner_says_which_part_actually_changed(
    capture_changed: bool, place_changed: bool, what: str
) -> None:
    """**说清改了哪一部分**，不是笼统的「已更新元数据」。

    同一个对话框里可以只改时间、只改位置、或者两样都改，而**没改的那一部分照片上一个
    字节都没动** —— 用户回头翻那张照片时得知道该去核对什么（ADR 0032）。
    """
    text = metadata_edit_banner(
        _outcome(capture_changed=capture_changed, place_changed=place_changed), "一张.jpg"
    )

    assert text.startswith(f"已更新「一张.jpg」的{what}。")


def test_undo_banner_for_a_place_mentions_the_fields_it_refused_to_touch() -> None:
    """撤销时「只撤了一部分」必须说清 —— 那不是重复，那是用户需要知道的结果。"""
    result = UndoResult(
        action=ACTION_METADATA,
        entries=(PhotoRef(path=Path("/库/一张.jpg"), size=1, mtime_ns=3),),
        failures=(Failure(name="XMP:City", message="这一项在 Gallery 之外被改过。"),),
    )

    assert "只撤销了一部分" in undo_banner(result)
    assert "1 项" in undo_banner(result)


def test_undo_banner_for_a_clean_place_undo_says_it_is_back() -> None:
    result = UndoResult(
        action=ACTION_METADATA, entries=(PhotoRef(path=Path("/库/一张.jpg"), size=1, mtime_ns=3),)
    )

    assert undo_banner(result) == "已撤销，「一张.jpg」的位置恢复原样。"


def test_external_banner_says_it_opened_in_the_app() -> None:
    outcome = OpenOutcome(where=WHERE_APP)
    assert (
        banners.external_app_opened(outcome, "Google 日历") == "已在 Google 日历中打开当前这一页。"
    )


def test_external_banner_says_it_opened_in_the_browser() -> None:
    """**开在浏览器里也要说出来**：那是浏览器里的一个网页，标签栏还在，
    与那个独立窗口完全不是一回事；说成同一句话，下次他就以为出了故障。"""
    outcome = OpenOutcome(where=WHERE_BROWSER)
    assert banners.external_app_opened(outcome, "Google 日历") == "已在浏览器中打开 Google 日历。"


def test_external_banner_keeps_the_reason_of_a_fallback() -> None:
    """降级时那句「为什么」是主语 —— 横幅照实说，别把它吞成一句「已打开」。"""
    outcome = OpenOutcome(
        where=WHERE_BROWSER,
        detail="没认出那个应用的窗口，已改用浏览器打开",
    )
    assert banners.external_app_opened(outcome, "Google 日历") == (
        "没认出那个应用的窗口，已改用浏览器打开。"
    )


# ---- 事件（ADR 0043）----------------------------------------------------------


def _event(event_id: int = 1, title: str = "京都五日") -> Event:
    return Event(
        id=event_id,
        kind="trip",
        created_at=0,
        modified_at=0,
        values={"title": title},
    )


def test_a_new_event_banner_does_not_promise_undo() -> None:
    """**新建不在撤销栈里**（撤销它就是删），所以这一句不承诺 ⌘Z —— 说了就是骗人。"""
    outcome = EventWriteOutcome(event=_event(), journaled=True)

    text = banners.event_created_banner(outcome)

    assert "京都五日" in text
    assert "⌘Z" not in text


def test_a_failed_journal_on_an_edit_says_undo_is_gone() -> None:
    """记账失败必须说出来 —— 与元数据那一族同一套规矩。"""
    outcome = EventWriteOutcome(event=_event(), journaled=False)

    assert "⌘Z 撤销不了" in banners.event_updated_banner(outcome)


def test_an_unchanged_edit_says_nothing_happened() -> None:
    """面板存了一份与库里逐字段一样的内容时，说「已更新」就是撒谎。"""
    outcome = EventWriteOutcome(event=_event(), journaled=True, changed=False)

    assert "没有改动" in banners.event_updated_banner(outcome)


def test_deleting_an_event_says_how_many_photos_lost_the_record() -> None:
    """**连带删掉的照片关联要一起说**：那是用户看不见的损失，不说他不会知道。"""
    outcome = EventWriteOutcome(
        event=_event(),
        journaled=True,
        unbound=(Path("/库/a.jpg"), Path("/库/b.jpg")),
    )

    text = banners.event_deleted_banner(outcome)

    assert "2 张照片" in text
    assert "⌘Z" in text


def test_binding_says_how_many_were_new() -> None:
    """**新记进去几张**说清 —— 已经记着的那些不是这次的成果。"""
    assert "3 张" in banners.photos_bound_banner(3, "京都五日")
    assert banners.photos_bound_banner(0, "京都五日").startswith("这些照片已经在")


def test_a_long_event_name_is_elided_on_the_banner() -> None:
    """横幅里的事件名超过固定长度就中间省略 —— 免得一个名字把横幅拉成一条。"""
    long_name = "超长的旅行名字" * 5
    outcome = EventWriteOutcome(event=_event(title=long_name), journaled=True)

    text = banners.event_created_banner(outcome)

    assert shorten_name(long_name) in text
    assert long_name not in text


def test_binding_by_a_date_range_says_why_there_are_so_many() -> None:
    """按日期范围是几百张的批量操作，用户该知道它为什么这么多。"""
    assert banners.photos_bound_banner(312, "京都五日", by_range=True).startswith("按日期范围把")


def test_unbinding_reports_the_count() -> None:
    assert "2 张" in banners.photos_unbound_banner(2, "京都五日")
    assert "本来就不在" in banners.photos_unbound_banner(0, "京都五日")


def test_undoing_a_delete_says_it_came_back() -> None:
    """撤的是删除就说「回来了」，撤的是改动就说「改回去」—— 两句话不是同一句。"""
    from gallery.core.metadata.journal import ACTION_EVENT, ACTION_EVENT_DELETE

    restored = UndoResult(action=ACTION_EVENT_DELETE, entries=(), event=_event(), journaled=True)
    changed = UndoResult(action=ACTION_EVENT, entries=(), event=_event(), journaled=True)

    assert "恢复" in banners.event_undo_banner(restored, "事件")
    assert "改回去" in banners.event_undo_banner(changed, "事件")
    assert "京都五日" in banners.event_undo_banner(changed, "事件")


def test_a_long_event_name_is_elided_in_the_undo_banner() -> None:
    """撤销那一句里的事件名同样截短 —— 与其余横幅共用一个长度。"""
    from gallery.core.metadata.journal import ACTION_EVENT

    long_name = "超长的旅行名字" * 5
    result = UndoResult(
        action=ACTION_EVENT, entries=(), event=_event(title=long_name), journaled=True
    )

    text = banners.event_undo_banner(result, "旅行")

    assert shorten_name(long_name) in text
    assert long_name not in text
