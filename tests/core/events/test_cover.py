"""事件的封面：它恒为「绑定里的一张照片」，并跟着改路径的那五条路一起走（ADR 0048）。

这一份用例盯的是**改路径**。封面是一条指向照片的路径，所以绑定要处理的那五件事
（应用内改名、应用内移动、撤销这两样、监听器报上来的外部改名与移动）它一件都躲不掉 ——
而它躲掉的那一种症状是「卡片悄悄换了一张照片」，用户不会知道自己选过封面（ADR 0028
决策 4 记的就是相册封面那个毛病，那边选择不跟，这边跟，理由在 ADR 0048 决策 4）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.errors import CoverNotBoundError
from gallery.core.events.store import EventDraft, follow_moves
from gallery.core.library import Library
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.undo import undo_last_write


@pytest.fixture
def library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True)
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root / "京都")
    return Library.open(root)


def _event(library: Library, title: str = "京都五日") -> int:
    return library.create_event("trip", EventDraft(values={"title": title})).event.id


def _cover_of(library: Library, event_id: int) -> Path | None:
    return library.event(event_id).cover_path  # type: ignore[union-attr]


# ---- 不变式：封面必须被绑定 ------------------------------------------------------


def test_a_bound_photo_can_become_the_cover(library: Library) -> None:
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)

    outcome = library.set_event_cover(event_id, photo)

    assert outcome.changed
    assert _cover_of(library, event_id) == photo


def test_a_photo_outside_the_event_is_refused(library: Library) -> None:
    """卡片只能画**组内**那一张（缩略图流水线拿的是 ``PhotoRef``），所以组外的封面画不出来。

    与其让界面上出现一张永远出不来图的卡片，不如当场说一句为什么。
    """
    event_id = _event(library)
    library.bind_photos([library.root / "海边-01.jpg"], event_id)

    with pytest.raises(CoverNotBoundError):
        library.set_event_cover(event_id, library.root / "京都" / "海边-02.jpg")

    assert _cover_of(library, event_id) is None


def test_clearing_the_cover_puts_the_group_back_to_its_first_photo(library: Library) -> None:
    event_id = _event(library)
    photos = [library.root / "海边-01.jpg", library.root / "京都" / "海边-02.jpg"]
    library.bind_photos(photos, event_id)
    library.set_event_cover(event_id, photos[1])

    assert library.clear_event_cover(event_id)

    assert _cover_of(library, event_id) is None


def test_clearing_a_cover_that_was_never_set_changes_nothing(library: Library) -> None:
    """没设过就是 ``False``** —— 界面据此不摆「清除封面」那一行，而不是摆一个按了没反应的行。"""
    event_id = _event(library)
    library.bind_photos([library.root / "海边-01.jpg"], event_id)

    assert not library.clear_event_cover(event_id)


def test_setting_the_same_cover_twice_is_not_an_edit(library: Library) -> None:
    """逐字段一样就不记：没有改动就没什么可撤的，而「记一条改回同样的值」只在栈上白占一格
    （与 :meth:`EventStore.update` 同一套）。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    assert not library.set_event_cover(event_id, photo).changed


def test_one_cover_change_is_worth_exactly_one_undo(library: Library) -> None:
    """换封面可 ⌘Z（与相册封面相反，ADR 0048 决策 6）：事件本体本来就在撤销栈上。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    undo_last_write(library.root, events=library.events)

    assert _cover_of(library, event_id) is None


def test_undoing_a_cover_change_brings_the_previous_one_back(library: Library) -> None:
    """连着换两次封面，撤一次回到**上一次那一张**，而不是回到「没设过」。

    这就是 journal 里必须带**完整前像**的原因（与 ADR 0043 决策 5 同一条）——
    只记「这次改成什么」的话，撤销就回不到中间那一张。
    """
    first, second = library.root / "海边-01.jpg", library.root / "京都" / "海边-02.jpg"
    event_id = _event(library)
    library.bind_photos([first, second], event_id)
    library.set_event_cover(event_id, first)
    library.set_event_cover(event_id, second)

    undo_last_write(library.root, events=library.events)

    assert _cover_of(library, event_id) == first


# ---- 改路径：封面跟着走 ----------------------------------------------------------


def test_renaming_a_photo_carries_its_cover_over(library: Library) -> None:
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    assert _cover_of(library, event_id) == library.root / "新的名字.jpg"


def test_undoing_a_rename_carries_the_cover_back(library: Library) -> None:
    """撤销是一次改名，于是封面也搬回去 —— 只做绑定那一份的话，撤销之后卡片就换了张照片。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)
    library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    undo_last_write(library.root, events=library.events)

    assert _cover_of(library, event_id) == photo


def test_renaming_an_album_carries_the_cover_under_it(library: Library) -> None:
    """相册改名改的是里面每一张的绝对路径，所以封面（在那里面）也要跟着换前缀 —— 一次就够。"""
    event_id = _event(library)
    album = library.root / "京都"
    photo = album / "海边-02.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    library.rename_entry(FolderRef.from_path(album), "奈良")

    assert _cover_of(library, event_id) == library.root / "奈良" / "海边-02.jpg"


def test_moving_a_photo_into_an_album_carries_its_cover_over(library: Library) -> None:
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    library.move_into(library.root / "京都", [photo])

    assert _cover_of(library, event_id) == library.root / "京都" / "海边-01.jpg"


def test_undoing_a_move_carries_the_cover_back(library: Library) -> None:
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)
    library.move_into(library.root / "京都", [photo])

    undo_last_write(library.root, events=library.events)

    assert _cover_of(library, event_id) == photo


def test_an_external_rename_reported_by_the_watcher_carries_the_cover(
    library: Library,
) -> None:
    """**只有监听器知道**访达里的改名（ADR 0043 决策 8 那一行），而它同样要带着封面走。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)

    follow_moves(library.events, [(photo, library.root / "海边-09.jpg", False)])

    assert _cover_of(library, event_id) == library.root / "海边-09.jpg"


def test_a_photo_shared_by_two_events_is_the_cover_of_both(library: Library) -> None:
    """一张照片可以同时属于好几种事件，于是它可以是**两个**事件的封面。

    所以搬封面不能按事件逐个去改 —— 一句 ``WHERE cover_path = ?`` 要改掉全部，
    而逐个改的写法在「第二个事件的封面没跟着搬」时是静默的（用户看到的是两个事件
    里有一个悄悄换了张照片）。
    """
    trip = _event(library, "京都五日")
    show = _event(library, "岚山演出")
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], trip)
    library.bind_photos([photo], show)
    library.set_event_cover(trip, photo)
    library.set_event_cover(show, photo)

    library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    moved = library.root / "新的名字.jpg"
    assert _cover_of(library, trip) == moved
    assert _cover_of(library, show) == moved


# ---- 解绑与孤儿清理：同一批里清掉封面 --------------------------------------------


def test_removing_the_cover_photo_from_the_event_clears_the_cover(library: Library) -> None:
    """封面恒为绑定里的一张（ADR 0048 决策 1）—— 移出来的那一刻就要清掉。

    不清的话卡片会指向一张已经不属于这次事件的照片，而它的缩略图仍然画得出来
    （那一张还在库里），于是用户看到的是「这个事件的照片少了一张，封面却还在」。
    """
    event_id = _event(library)
    cover = library.root / "海边-01.jpg"
    other = library.root / "京都" / "海边-02.jpg"
    library.bind_photos([cover, other], event_id)
    library.set_event_cover(event_id, cover)

    library.unbind_photos([cover], event_id)

    assert _cover_of(library, event_id) is None


def test_unbinding_another_photo_leaves_the_cover_alone(library: Library) -> None:
    """清封面只清**指向被移出来那些路径的**：动别的照片时封面必须留着。"""
    event_id = _event(library)
    cover = library.root / "海边-01.jpg"
    other = library.root / "京都" / "海边-02.jpg"
    library.bind_photos([cover, other], event_id)
    library.set_event_cover(event_id, cover)

    library.unbind_photos([other], event_id)

    assert _cover_of(library, event_id) == cover


def test_unbinding_a_photo_does_not_clear_another_events_cover(library: Library) -> None:
    """那张照片可能是**另一个**事件的封面 —— 解绑只动这一个事件（ADR 0048 决策 1 的那张表）。"""
    first, second = _event(library, "京都五日"), _event(library, "奈良三日")
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], first)
    library.bind_photos([photo], second)
    library.set_event_cover(second, photo)

    library.unbind_photos([photo], first)

    assert _cover_of(library, second) == photo


def test_forgetting_an_orphan_clears_the_cover_that_pointed_at_it(library: Library) -> None:
    """孤儿清理清掉绑定的那一批里，封面也一并清掉（照片没了，它不该还当封面）。

    **返回值仍然只是「清掉了几条绑定」** —— 封面退回「组内第一张」是用户看得见的结果，
    而那个数是横幅上那句话（ADR 0048 决策 5）。
    """
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.set_event_cover(event_id, photo)
    photo.unlink()

    removed = library.events.forget_bindings([photo])

    assert removed == 1
    assert _cover_of(library, event_id) is None


# ---- 分组：卡片画哪一张 ---------------------------------------------------------


def test_the_group_card_shows_the_chosen_photo(library: Library) -> None:
    """指定的那张就是卡片画的那张 —— 不管它在当前排序里排第几（默认会是第一张）。"""
    event_id = _event(library)
    photos = [library.root / "海边-01.jpg", library.root / "京都" / "海边-02.jpg"]
    library.bind_photos(photos, event_id)
    library.set_event_cover(event_id, photos[1])

    refs = library.events.events_for_photos(photos)
    assert refs[photos[1]][0].cover_path == str(photos[1])


def test_a_group_without_a_cover_says_so_rather_than_pointing_at_one(library: Library) -> None:
    """没设封面时 :attr:`EventRef.cover_path` 是 ``None`` 而不是第一张 —— 「有没有封面」
    是界面要用来决定「清除封面」那一行摆不摆的，混进第一张就分不开了。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)

    refs = library.events.events_for_photos([photo])

    assert refs[photo][0].cover_path is None
