"""「这一层该选中哪一格」与「大图页指着哪一行」钉在这一份（ADR 0053 的实施）。

这一族真正的病是**一个只为另一个字段服务的布尔字段**：``_focus_restored`` 记的是
「``_focus`` 摆过了没有」，而换层时必须把它清回去 —— 漏一处的症状是**新的一层永远选不中**
（界面上一格都不高亮，不报任何错）。所以 :meth:`FocusState.take_focus` 自己把「已经摆过」
记上并作废那一格，调用点想用第二次就拿不到第二次。

所以这里钉三件事：**一层只给一次**、**换层把该重置的一起重置**、
以及**那六处「换层了没人正看着任何一张」是同一个意思**。
"""

from __future__ import annotations

from gallery.ui.window.focus import FocusState
from gallery.ui.window.navigation import ENTRY_KIND_PATH, EntryKey


#: 层标识在这一族里只当**不透明的标记**用：这一份测的是「换没换层」这件事的处理，
#: 不测层标识怎么算出来的（那是 :func:`gallery.ui.window.navigation.level_key` 的事）。
def _level(name: str) -> tuple[str, ...]:
    return ("flat", name)


def _entry(name: str) -> EntryKey:
    return (ENTRY_KIND_PATH, f"/photos/{name}.jpg")


def test_the_armed_cell_is_handed_out_exactly_once() -> None:
    """**一层只摆一次** —— 摆过之后再要就是 ``None``。

    这是本模块存在的理由：那一格摆完之后就是用户自己的选择了，后台重排（来了新照片、
    换排序）不该把它拽回去 —— 那种重排由 ``_reorder_level`` 的锚点负责。
    """
    state = FocusState()
    state.arm(_level("a"), None, _entry("海边"))

    assert state.take_focus() == _entry("海边")
    assert state.take_focus() is None, "第二次还给了：后台重排把用户的选择拽回去了"


def test_arming_a_new_level_hands_out_again() -> None:
    """换一层之后**重新 armed**：新的一层该选中的是它自己那一格。"""
    state = FocusState()
    state.arm(_level("a"), None, _entry("海边"))
    state.take_focus()

    state.arm(_level("b"), None, _entry("礁石"))

    assert state.take_focus() == _entry("礁石"), "新的一层选不中（armed 没被重新 armed）"


def test_the_explicit_focus_wins_over_the_remembered_cell() -> None:
    """显式指定的那一格优先（往上一层退时给的那个相册 / 那个组）。

    「不是同一层」时 remembered 本来就是 ``None``，所以这一条真正量的是**优先级**：
    两个都在的时候该用显式的那个。
    """
    state = FocusState()
    state.arm(_level("a"), _entry("停着的那一格"), _entry("刚离开的那个"))

    assert state.take_focus() == _entry("刚离开的那个")


def test_the_remembered_cell_is_used_when_no_focus_is_given() -> None:
    """不给显式的一格就用 remembered —— 「还是这一层重排一遍」时该跟着停着的那一格走。"""
    state = FocusState()
    state.arm(_level("a"), _entry("停着的那一格"), None)

    assert state.take_focus() == _entry("停着的那一格")


def test_nothing_is_armed_means_nobody_gets_selected() -> None:
    """两者都没有 → 谁也不选（换了层时用户看到的是上一趟的高亮，那更糟）。"""
    state = FocusState()
    state.arm(_level("a"), None, None)

    assert state.take_focus() is None


def test_the_level_key_decides_whether_the_grid_is_showing_the_same_level() -> None:
    """「换了一层」与「还是这一层重排一遍」在屏幕上长得一样，只有比层标识才分得开。

    没有 arm 过（``level_key`` 是 ``None``）时**一律答不是** —— 「压根没有」与
    「摆的是别的层」在「该继承上一趟的选中吗」这件事上是同一个答案。
    """
    state = FocusState()
    assert state.is_same_level(_level("a")) is False, "还没 arm 就答「是」，会把上一层的选中搬过来"

    state.arm(_level("a"), None, None)
    assert state.is_same_level(_level("a")) is True
    assert state.is_same_level(_level("b")) is False


def test_clearing_the_current_row_means_nobody_is_being_looked_at() -> None:
    """换层的那个动作：大图页不再指着任何一行。"""
    state = FocusState(current=7)
    state.clear_current()

    assert state.current == -1


def test_forgetting_the_level_drops_both_the_cell_and_the_key() -> None:
    """关库 / 换库：上一个库的选中与层标识都要作废。

    不作废的话用户在新库里会看到上一座库的相册被选中 —— 而那时候网格上压根没有那个东西。
    """
    state = FocusState()
    state.arm(_level("a"), None, _entry("海边"))

    state.forget_level()

    assert state.take_focus() is None, "上一个库的那一格还能被取出来"
    assert state.is_same_level(_level("a")) is False, "上一个库的层标识还认得"


def test_the_pending_cell_is_handed_out_once_and_then_forgotten() -> None:
    """重排之后要回到的那一格：**取一次就没了**（重排只发生一次）。

    留着的话下一次重排会拿着一个早就作废的键回到一个用户已经离开的地方。
    """
    state = FocusState()
    state.remember_pending(_entry("2024 年"))

    assert state.take_pending() == _entry("2024 年")
    assert state.take_pending() is None
    assert state.pending is None


def test_remembering_nothing_is_not_the_same_as_remembering_a_cell() -> None:
    """「哪儿也不回」也要能记下来 —— 那与「没记过」在界面上是同一件事。"""
    state = FocusState()
    state.remember_pending(None)

    assert state.take_pending() is None
    assert state.pending is None
