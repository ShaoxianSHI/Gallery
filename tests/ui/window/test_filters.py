"""筛选 / 检索那一族的**写入口与下标漂移**钉在这一份（ADR 0052 的实施）。

这一族真正的病是 ``_editing_condition`` **会漂移的下标**：删掉第 ``i`` 枚条件之后后面全部要前移，
而不跟着挪的话「正在改的那一条」就变成了另一条 —— 症状是下一次回车静默替换掉一条用户没在改的
条件，而界面上只表现为「刚敲的那条没生效」。

所以这里钉两件事：**挪下标的地方只有一处**，以及**那一处的三种归宿都对**。
界面上那几条行为用例（``test_filter_flow.py``）量的是「用户看到什么」，这里量的是
「哪一处代码有权动这个下标」—— 后者任何行为用例都盯不住。
"""

from __future__ import annotations

from gallery.core.library.config import SEARCH_SCOPE_LEVEL, SEARCH_SCOPE_LIBRARY
from gallery.core.library.search import SearchCondition, make_condition, parse_input
from gallery.ui.window.filters import FilterState


def _condition(text: str) -> SearchCondition:
    """一条够用的条件：这一族测的是**下标怎么挪**，不测条件本身算得对不对。

    走真的解析而不是硬造 ``SearchCondition``：字段名写错会当场得到 ``None``，
    而那会让下面每一条断言都在「条件根本没加上」的状态下空转。
    """
    parsed = parse_input(text)
    condition = None if parsed is None else make_condition(parsed)
    assert condition is not None, f"造不出条件：{text!r}"
    return condition


def _filled(count: int) -> FilterState:
    state = FilterState()
    for index in range(count):
        state.submit(_condition(f"{index}.jpg"))
    return state


def test_removing_a_condition_is_the_only_thing_that_moves_the_editing_index() -> None:
    """**只有 :meth:`FilterState.remove` 会挪编辑态的下标** —— 全仓库就这一处。

    这是本模块存在的理由。原来「挪下标」是 ``_on_condition_removed`` 里那三行手工判断，
    而条件一共四处会被改动（加一条、撤一条、撤最后一条、清空），新增一条改动路径的人
    不会想到要连带挪下标 —— 漏了不报错，只是下一次回车顶掉了另一条条件。

    **其余三处改条件时编辑态一律置 ``None``**：它们要么清空整份（``clear`` / ``restore``），
    要么撤的不是正在改的那一条（``pop_last`` / ``submit``），下标在那之后**没有意义**。
    """
    state = _filled(3)
    state.begin_edit(2)
    state.remove(1)

    # 撤掉的是它**前面**那一条 → 后面的下标减一。
    assert state.editing == 1, "编辑态的下标没跟着前移，正在改的那一条变成了另一条"


def test_removing_the_condition_being_edited_clears_the_editing_state() -> None:
    """撤掉的正是正在改的那一条 → 编辑态置 ``None``：那条已经不在了，没有「正在改第几条」。"""
    state = _filled(3)
    state.begin_edit(1)
    state.remove(1)

    assert state.editing is None
    assert len(state.conditions) == 2


def test_removing_a_condition_after_the_one_being_edited_leaves_it_alone() -> None:
    """撤掉它**后面**那一条 → 下标不动（前面的都没挪）。"""
    state = _filled(3)
    state.begin_edit(0)
    state.remove(2)

    assert state.editing == 0
    assert len(state.conditions) == 2


def test_removing_a_condition_never_moves_the_editing_index_backwards() -> None:
    """连着撤两次：编辑态下标要一路跟着退，而不是只退一次。

    这是「只挪一位」那个实现最容易漏的一半 —— 逐次退化成一次性退位看着一样，
    而连撤两次时第二笔就少了。
    """
    state = _filled(4)
    state.begin_edit(3)
    state.remove(0)
    state.remove(0)

    assert state.editing == 1


def test_an_out_of_range_position_changes_nothing() -> None:
    """越界的位置**什么都不做**并返回 ``False`` —— 不是静默地删掉最后一条。"""
    state = _filled(2)
    state.begin_edit(0)

    assert state.remove(5) is False
    assert state.remove(-1) is False
    assert len(state.conditions) == 2
    assert state.editing == 0, "一次失败的删除不该把编辑态也带走"


def test_clearing_the_conditions_also_drops_the_editing_state_and_the_total() -> None:
    """清空时**编辑态与命中数一起清** —— 三处调用点原本都写这两句。

    条件空了而编辑态还指着第 0 枚的话，下一次回车会把一条**不存在**的条件「就地替换」；
    命中数留着的话面包屑会写「搜索结果（128）」而其实一条都没有。
    """
    state = _filled(2)
    state.begin_edit(0)
    state.set_total(128)

    state.clear()

    assert state.conditions == []
    assert state.editing is None
    assert state.total is None


def test_submitting_while_editing_replaces_in_place_and_appends_otherwise() -> None:
    """回车提交：**正在改哪一条就替换那一条**，否则追加 —— 顺序与长度都不跳。"""
    state = _filled(2)
    state.begin_edit(0)
    state.submit(_condition("替换.jpg"))

    assert [c.value for c in state.conditions] == ["替换.jpg", "1.jpg"], "就地替换换了位置"
    assert state.editing is None

    state.submit(_condition("新加.jpg"))
    assert [c.value for c in state.conditions] == ["替换.jpg", "1.jpg", "新加.jpg"]


def test_submitting_with_a_stale_editing_index_appends_instead_of_losing_the_input() -> None:
    """编辑态指着的位置已经被别的路径拿掉时，**追加而不是丢弃**用户刚敲的条件。

    静默丢弃更糟：用户看得见自己敲的字，回车之后它就没了，而界面上没有任何解释。
    """
    state = _filled(1)
    state.begin_edit(0)
    state.remove(0)
    state._editing = 0  # 模拟「另一条路径留下了过期下标」

    state.submit(_condition("敲的.jpg"))

    assert [c.value for c in state.conditions] == ["敲的.jpg"]


def test_restoring_a_frame_puts_the_conditions_back_only_while_filtering() -> None:
    """装回某一帧自己那份筛选：**只在「筛选」那一档装**（ADR 0041 决策 2）。

    此刻是整库检索的话条件属于搜索帧，装回去就成了「弹掉了搜索帧，条件却还在」。
    """
    narrowing = tuple(_condition(f"{index}.jpg") for index in range(2))

    filtering = FilterState(SEARCH_SCOPE_LEVEL)
    filtering.restore(narrowing)
    assert len(filtering.conditions) == 2, "筛选作用域下该把那一帧的筛选装回来"

    searching = FilterState(SEARCH_SCOPE_LIBRARY)
    searching.restore(narrowing)
    assert searching.conditions == [], "整库检索时不该装：那属于搜索帧而不是底下那一层"


def test_the_two_scopes_stay_apart_however_many_conditions_there_are() -> None:
    """「有条件」推不出「在检索」—— 两个作用域在两种条件下都必须分得开。

    这是 ADR 0040 决策 1 的原话：整库检索**接管**当前层而筛选只**收窄**它，两者在这一层
    上的表现完全不同（网格页 vs 原来的月视图）。
    """
    for count in (0, 1, 3):
        library = _filled(count)
        level = _filled(count)
        library.set_scope(SEARCH_SCOPE_LIBRARY)
        level.set_scope(SEARCH_SCOPE_LEVEL)
        assert library.is_library_search() is (count > 0)
        assert library.is_filtering() is False
        assert level.is_filtering() is (count > 0)
        assert level.is_library_search() is False


def test_a_snapshot_is_not_the_live_list() -> None:
    """交给后台任务的那一份**不可变**：任务在跑的时候用户还能改条件。

    传列表过去的话，用户在筛选进行当中加一条条件就会让那次查询看到半新半旧的一份，
    而症状是「筛出来的结果和胶囊上的条件对不上」。
    """
    state = _filled(2)
    snapshot = state.snapshot()

    state.remove(0)

    assert len(snapshot) == 2, "快照跟着后面的改动变了"
