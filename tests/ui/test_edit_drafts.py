"""编辑草稿那份会话内状态（ADR 0027）。

**这里一行 Qt 都没有**，所以用例不必起窗口 —— 草稿是纯数据，界面那一半另有测试。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core.media.edit import Crop, Mirror, Rotate
from gallery.ui.edit_drafts import EditDrafts, draft_is_meaningless

A = Path("/库/一张.jpg")
B = Path("/库/两张.jpg")


def test_nothing_is_a_draft_at_first() -> None:
    drafts = EditDrafts()
    assert drafts.steps(A) == ()
    assert drafts.has(A) is False
    assert drafts.count() == 0
    assert drafts.undo_label() is None


def test_a_step_becomes_a_draft() -> None:
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    assert drafts.steps(A) == (Rotate(90),)
    assert drafts.has(A) is True
    assert drafts.undo_label() == "左转 90°"


def test_a_round_trip_is_not_a_draft() -> None:
    """转了 90° 又转回来 —— **屏幕上看不出变化，就等于没改过**。

    判据是规范化之后的净结果（``core.media.edit.normalized``）。于是 ⌘S 在这种情况下
    不会白白产出一份与原文件一模一样的副本。
    """
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.add(A, Rotate(270))
    assert drafts.has(A) is False
    assert drafts.count() == 0


def test_two_mirrors_cancel_out_too() -> None:
    drafts = EditDrafts()
    drafts.add(A, Mirror())
    drafts.add(A, Mirror())
    assert drafts.has(A) is False


def test_crop_is_never_a_no_op() -> None:
    """两块拼起来等于整张，也仍然算改动 —— 那正是用户要的「什么都没变但我裁过一次」。"""
    drafts = EditDrafts()
    drafts.add(A, Crop((0.0, 0.0, 0.5, 0.5)))
    drafts.add(A, Crop((0.0, 0.0, 1.0, 1.0)))
    assert drafts.has(A) is True


def test_undo_pops_one_step_at_a_time() -> None:
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.add(A, Mirror())

    assert drafts.undo(A) == Mirror()
    assert drafts.steps(A) == (Rotate(90),)
    assert drafts.undo(A) == Rotate(90)
    assert drafts.has(A) is False
    assert drafts.undo(A) is None


def test_undo_label_follows_the_last_step() -> None:
    """菜单上「撤销 左转 90°」那一行说的是**最后动过的那一步**。"""
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    assert drafts.undo_label() == "左转 90°"
    drafts.add(A, Mirror())
    assert drafts.undo_label() == "左右镜像"


def test_undo_last_takes_the_most_recent_photo() -> None:
    """⌘Z 撤的是「整个会话里最后动过的那一张」的最后一步。"""
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.add(B, Mirror())

    path, step = drafts.undo_last()  # type: ignore[misc]
    assert path == B
    assert step == Mirror()
    assert drafts.has(B) is False
    assert drafts.has(A) is True

    assert drafts.undo_last() == (A, Rotate(90))
    assert drafts.undo_last() is None


def test_a_new_step_drops_the_undo_history() -> None:
    """做过新动作之后，「回到更早的状态」这条路不再通着 —— 与访达的重做栈一个道理。

    少了这一条，用户撤两步之后点一下「转」，再按 ⌘Z 会跳到一个他没见过的状态。
    """
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.undo(A)
    drafts.add(A, Mirror())
    assert drafts.steps(A) == (Mirror(),)
    assert drafts.undo(A) == Mirror()


def test_clearing_takes_the_whole_draft_away() -> None:
    """⌘S 存完之后调用它 —— 副本已经在磁盘上，草稿留着只会让人以为还有东西没存。"""
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.clear(A)
    assert drafts.has(A) is False
    drafts.add(B, Mirror())
    drafts.clear_all()
    assert drafts.count() == 0


def test_paths_lists_only_those_that_still_change_something() -> None:
    """「有 N 张照片有未保存的编辑」那句话里的 N —— 转回来的那张不算。"""
    drafts = EditDrafts()
    drafts.add(A, Rotate(90))
    drafts.add(B, Rotate(90))
    drafts.add(B, Rotate(270))
    assert set(drafts.paths()) == {A}
    assert drafts.count() == 1


def test_draft_is_meaningless_is_about_the_net_result() -> None:
    assert draft_is_meaningless(())
    assert draft_is_meaningless((Rotate(90), Rotate(90), Rotate(180)))
    assert not draft_is_meaningless((Rotate(90),))
