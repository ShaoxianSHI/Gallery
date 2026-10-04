"""横幅上那行中文的测试。

这些断言**在 ``banners`` 还是 ``main_window`` 顶部那七个私有函数的时候是写不出来的** ——
测一句措辞就要造一个 ``MainWindow``（要 Qt、要一个真的库、要事件循环转起来），
而「记账失败时那句提示必须出现」是一条纯粹关于措辞的规矩。
把它们抽出来才有了这些用例，这正是拆分的收益本身。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import ACTION_MOVE, ACTION_RENAME
from gallery.core.metadata.paste import PastedFile, PasteResult, Skipped
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MovedItem, MoveResult
from gallery.core.metadata.trash import TrashResult
from gallery.core.metadata.undo import UndoResult
from gallery.ui import banners


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


def test_a_paste_that_skipped_everything_does_not_say_it_succeeded(tmp_path: Path) -> None:
    """全被跳过（本来就在那里）不是成功，横幅不能说「已粘贴」。"""
    result = PasteResult(skipped=(Skipped(source=tmp_path / "a.jpg", message="已经在那里了。"),))

    text = banners.paste_banner(result, "京都")

    assert "已粘贴" not in text
    assert "本来就在" in text


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
    """移动比粘贴多一句「⌘Z 可以撤销」—— **这是用户敢用它的前提**（红线 1，ADR 0019）。"""
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
