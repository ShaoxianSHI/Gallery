"""那条栈本身：压、弹、换根、筛选随帧走。

**这一份不碰 Qt**（它就是纯数据），所以能一次把所有形状走完 —— 而界面层那些用例只能走真实
路径，绕不到「栈里同时有五种帧」这种情形。

钉的是**性质**而不是某几个具体值：``can_pop`` 恒等于「栈顶存在」，``pop`` 永远拿掉栈顶，
而 ``narrowing`` 与 ``frames`` 一一对应。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.ui.window.level_stack import (
    AlbumFrame,
    CalendarFrame,
    GroupFrame,
    LevelStack,
    PhotoFrame,
    RootKind,
    SearchFrame,
    label_of,
)


def _photo(name: str = "a.jpg") -> PhotoRef:
    return PhotoRef(path=Path(f"/lib/{name}"), size=1, mtime_ns=1)


def _group(label: str = "Canon") -> GroupRef:
    return GroupRef(facet="camera", key="canon", label=label, photos=(_photo(),))


# ---- 栈深与「退不退得回去」 ------------------------------------------------------


def test_a_fresh_stack_is_only_the_root() -> None:
    """空栈只有根帧 —— 而「只有根帧」就是「退不动」。"""
    stack = LevelStack(root=RootKind.ALBUMS)
    assert stack.frames == ()
    assert stack.top is None
    assert stack.depth == 0
    assert stack.can_pop() is False


def test_popping_the_last_frame_leaves_the_root() -> None:
    """弹到只剩根帧就该停 —— 退不动就安静地不动（ADR 0040 决策 11）。"""
    stack = LevelStack(root=RootKind.ALBUMS).push(AlbumFrame(Path("/lib/travel")))
    assert stack.can_pop() is True

    after = stack.pop()
    assert after.frames == ()
    assert after.can_pop() is False, "弹到底之后还在说「退得回去」"
    # 再弹一次**不炸、也不变**。
    assert after.pop() == after


def test_can_pop_is_just_whether_a_frame_is_there() -> None:
    """「退不退得回去」只有一处判据 —— 加新层级不用改它。"""
    for root in RootKind:
        stack = LevelStack(root=root)
        assert stack.can_pop() is (stack.depth > 0)
        stack = stack.push(GroupFrame(_group()))
        assert stack.can_pop() is (stack.depth > 0)
        assert stack.can_pop() is True


# ---- 压与弹的顺序 ---------------------------------------------------------------


def test_pop_gives_back_the_frame_below() -> None:
    """弹栈顶一帧，底下那一层原样露出来。"""
    stack = (
        LevelStack(root=RootKind.ALBUMS)
        .push(AlbumFrame(Path("/lib/travel")))
        .push(GroupFrame(_group()))
    )
    assert len(stack.frames) == 2

    after = stack.pop()
    assert len(after.frames) == 1
    assert isinstance(after.frames[0], AlbumFrame)


def test_all_five_kinds_stack_in_order() -> None:
    """五种帧能叠在一起，按压入顺序从底到顶排 —— 「相册→分组→照片」那种链。"""
    stack = (
        LevelStack(root=RootKind.ALBUMS)
        .push(AlbumFrame(Path("/lib/travel")))
        .push(GroupFrame(_group()))
        .push(PhotoFrame(Path("/lib/a.jpg")))
    )
    assert stack.depth == 3
    assert isinstance(stack.frames[0], AlbumFrame)
    assert isinstance(stack.frames[1], GroupFrame)
    assert isinstance(stack.frames[2], PhotoFrame)
    # 弹三次才见根帧 —— **次数就是层级数**，这正是「每加一种层级不用改判据」的意思。
    assert stack.pop().pop().pop().frames == ()


def test_calendar_frames_only_sit_on_the_calendar_root() -> None:
    """日历格下面不可能是相册 —— 那是**类型上的互斥**，不靠 if 判（ADR 0041 决策 2）。

    这里量的是「日历模式下的栈顶是日历格」；反过来那种状态构造不出来，因为
    :meth:`with_root` 换根时会把上面的一切清掉。
    """
    stack = LevelStack(root=RootKind.CALENDAR).push(
        CalendarFrame(photos=(_photo(),), label="3 天前")
    )
    assert isinstance(stack.top, CalendarFrame)
    assert stack.pop().root is RootKind.CALENDAR


# ---- 换栈底 ---------------------------------------------------------------------


def test_switching_the_root_drops_everything_above_it() -> None:
    """换浏览方式 = 清栈换根，落在各自底部（用户明确要的：切走再切回来不回到原位置）。"""
    stack = (
        LevelStack(root=RootKind.ALBUMS)
        .push(AlbumFrame(Path("/lib/travel")))
        .push(GroupFrame(_group()))
    )
    assert stack.depth == 2

    after = stack.with_root(RootKind.CALENDAR)
    assert after.root is RootKind.CALENDAR
    assert after.frames == (), "换浏览方式却把分组 / 相册留下了 —— 那等于让它在栈里悬空"
    assert after.can_pop() is False, "换完浏览方式还在栈底之外有东西"


def test_a_fresh_root_has_no_narrowing() -> None:
    """换根之后筛选条件也不在** —— 「筛选随帧走」的自然结果。"""
    stack = (
        LevelStack(root=RootKind.FLAT)
        .push(AlbumFrame(Path("/lib/travel")), narrowing=("条件A",))
        .push(SearchFrame(conditions=("条件B",)))
    )
    assert stack.with_root(RootKind.FLAT).narrowing == ()


# ---- 筛选随帧 -------------------------------------------------------------------


def test_narrowing_travels_with_its_own_frame() -> None:
    """每一帧带自己那份筛选，弹出别的帧不动它。"""
    stack = (
        LevelStack(root=RootKind.ALBUMS)
        .push(AlbumFrame(Path("/lib/travel")), narrowing=("相册上的筛选",))
        .push(SearchFrame(conditions=("搜出来的",)))
    )
    assert stack.narrowing_at(0) == ("相册上的筛选",)
    assert stack.narrowing_at(1) == ()

    # 弹掉搜索帧之后，相册那一帧的筛选**恢复成压入之前的样子**。
    back = stack.pop()
    assert back.narrowing_at(0) == ("相册上的筛选",)
    assert back.narrowing == (("相册上的筛选",),)


def test_popping_a_frame_takes_its_narrowing_with_it() -> None:
    """弹掉哪一帧，它那份筛选就跟着消失 —— 不会留在别的帧上。"""
    stack = LevelStack(root=RootKind.ALBUMS).push(
        AlbumFrame(Path("/lib/travel")), narrowing=("会被弹掉的那份",)
    )
    assert stack.narrowing == (("会被弹掉的那份",),)
    assert stack.pop().narrowing == (), "弹掉了帧，它的筛选还留着"


def test_changing_one_frame_narrowing_leaves_the_others() -> None:
    """改某一帧的筛选只动那一帧。"""
    stack = (
        LevelStack(root=RootKind.ALBUMS)
        .push(AlbumFrame(Path("/lib/a")), narrowing=("A",))
        .push(AlbumFrame(Path("/lib/b")), narrowing=("B",))
    )
    after = stack.with_narrowing(1, ("B2",))
    assert after.narrowing == (("A",), ("B2",))
    assert after.frames == stack.frames, "改筛选却把层也换了"


def test_with_narrowing_rejects_an_index_off_the_stack() -> None:
    """越界要报错，不要静默改到别的帧上。"""
    stack = LevelStack(root=RootKind.ALBUMS)
    with pytest.raises(IndexError):
        stack.with_narrowing(0, ("x",))


# ---- 结构自检 -------------------------------------------------------------------


def test_a_mismatched_narrowing_is_rejected_at_construction() -> None:
    """``frames`` 与 ``narrowing`` 不一一对应时**当场报错**。

    **在这里断**而不是靠每个调用点记得传对：少传一个的症状是「弹出某一帧之后筛选莫名其妙留在
    了别的帧上」，而那极难从界面上看出是哪一次构造错了。
    """
    with pytest.raises(ValueError):
        LevelStack(root=RootKind.ALBUMS, frames=(AlbumFrame(Path("/lib/a")),), narrowing=())


def test_push_without_narrowing_fills_a_slot() -> None:
    """压帧不写筛选时那一格是空的，而不是「不写进列表」—— 否则下标就对不上了。"""
    stack = (
        LevelStack(root=RootKind.FLAT).push(AlbumFrame(Path("/lib/a"))).push(GroupFrame(_group()))
    )
    assert stack.narrowing == ((), ())


# ---- 就地替换 -------------------------------------------------------------------


def test_replace_top_swaps_the_frame_without_going_deeper() -> None:
    """在同一层里翻上下一张**不是**往深里走 —— 不然 Esc 要按几十次才退得出去。"""
    stack = LevelStack(root=RootKind.FLAT).push(AlbumFrame(Path("/lib/a")))
    after = stack.replace_top(AlbumFrame(Path("/lib/b")))
    assert after.depth == stack.depth, "replace_top 却加深了栈"
    assert isinstance(after.top, AlbumFrame)
    assert after.top.path == Path("/lib/b")


def test_replace_top_on_an_empty_stack_pushes() -> None:
    """空栈上「换栈顶」就是压一帧，不报错。"""
    stack = LevelStack(root=RootKind.FLAT).replace_top(AlbumFrame(Path("/lib/a")))
    assert stack.depth == 1


# ---- 弹到某个条件 ---------------------------------------------------------------


def test_pop_until_stops_at_the_first_frame_that_matches() -> None:
    """「退到相册视图的根」这类动作：一次弹干净。"""
    stack = (
        LevelStack(root=RootKind.CALENDAR)
        .push(CalendarFrame(photos=(), label="那一格"))
        .push(GroupFrame(_group()))
        .push(PhotoFrame(Path("/lib/a.jpg")))
    )

    def at_root(s: LevelStack) -> bool:
        return s.depth == 0

    after = stack.pop_until(at_root)
    assert after.frames == ()
    assert after.pop_until(at_root) == after, "已经在根上了还继续弹"


def test_pop_until_leaves_a_matching_frame_alone() -> None:
    """已经满足条件时**不弹** —— 否则「退到根」会把已经到根的那一帧也弹没。"""
    stack = LevelStack(root=RootKind.ALBUMS).push(AlbumFrame(Path("/lib/a")))
    same = stack.pop_until(lambda s: s.depth == 1)
    assert same == stack


# ---- 界面上的名字 ----------------------------------------------------------------


def test_labels_come_from_the_frame_itself() -> None:
    """相册 / 分组 / 日历格各自叫什么。"""
    assert label_of(AlbumFrame(Path("/lib/travel/2024"))) == "2024"
    assert label_of(GroupFrame(_group("Canon"))) == "Canon"
    assert label_of(CalendarFrame(photos=(), label="3 天前")) == "3 天前"
    assert label_of(PhotoFrame(Path("/lib/a.jpg"))) == "a.jpg"
    assert label_of(None) == "", "空栈没有名字可报"


def test_the_search_frame_has_no_short_label() -> None:
    """搜索结果那一段写着「搜索结果（N）」，**不是**这里给的一段短标签。

    量的是「它回空字符串」—— 由调用处决定整句话怎么写（要带数量、要读作用域），而这里回一个
    占位的非空串只会让那句文案悄悄多出一截没头没尾的字。
    """
    assert label_of(SearchFrame(conditions=())) == ""
