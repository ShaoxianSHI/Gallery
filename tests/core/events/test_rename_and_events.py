"""改了路径的操作要带着事件绑定一起走（ADR 0043 决策 8）。

红线 1 豁免的那一次改名、以及「移动到相册」改的都是绝对路径，而绑定按路径记 —— 索引靠
「大小 + 修改时间」自动过期、用户无感，事件绑定不能这么兜。撤销是同一条链的反向。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.events.store import EventDraft, like_pattern
from gallery.core.library import Library
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.journal import ACTION_MOVE
from gallery.core.metadata.undo import pending_undo_runs, undo_last_write


@pytest.fixture
def library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True)
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root / "京都")
    return Library.open(root)


def _event(library: Library, title: str = "京都五日") -> int:
    return library.create_event("trip", EventDraft(values={"title": title})).event.id


def test_renaming_a_photo_carries_its_events_over(library: Library) -> None:
    """照片改名之后，它属于的那些事件一个都不掉。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)

    result = library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    assert result.renamed
    assert library.events.photos_of_event(event_id) == (library.root / "新的名字.jpg",)


def test_undoing_a_rename_carries_them_back(library: Library) -> None:
    """撤销改名是一次改名，于是绑定也搬回去 —— 只做一半的话 ⌘Z 之后事件就丢了。"""
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    undo_last_write(library.root, events=library.events)

    assert library.events.photos_of_event(event_id) == (photo,)


def test_renaming_an_album_carries_every_photo_under_it(library: Library) -> None:
    """**相册改名会改里面每一张照片的绝对路径**，所以那一整片绑定都要搬 —— 逐张搬既慢又容易漏。"""
    event_id = _event(library)
    album = library.root / "京都"
    library.bind_photos([album / "海边-02.jpg"], event_id)

    library.rename_entry(FolderRef.from_path(album), "奈良")

    assert library.events.photos_of_event(event_id) == (library.root / "奈良" / "海边-02.jpg",)


def test_an_album_rename_does_not_reach_into_a_similarly_named_album(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """「京都」改名不许动「京都远郊」里的照片 —— 下划线与前后缀都会让 ``LIKE`` 伸手太长。

    这就是 :func:`like_pattern` 存在的理由：文件名与目录名里的 ``_`` 太常见，不转义的话它就是
    通配符。
    """
    event_id = _event(library)
    elsewhere = library.root / "京都_远郊"
    elsewhere.mkdir()
    make_photo("山里-01.jpg", directory=elsewhere)
    library.bind_photos([elsewhere / "山里-01.jpg"], event_id)

    library.rename_entry(FolderRef.from_path(library.root / "京都"), "奈良")

    assert library.events.photos_of_event(event_id) == (elsewhere / "山里-01.jpg",)


def test_renaming_without_an_event_library_is_fine(library: Library) -> None:
    """配置目录建不出来的库上压根没有事件 —— 那一步跳过就好，改名照样成立。"""
    photo = library.root / "海边-01.jpg"

    result = library.rename_entry(PhotoRef.from_path(photo), "新的名字.jpg")

    assert result.renamed
    assert (library.root / "新的名字.jpg").exists()


def test_the_like_pattern_escapes_the_two_wildcards() -> None:
    """``%`` 与 ``_`` 都得转义（与 ``core.library.search`` 的 ``_like`` 同一个坑）。"""
    assert like_pattern("/库/京都_01", "/%") == "/库/京都\\_01/%"
    assert like_pattern("/库/100%", "/%") == "/库/100\\%/%"


def test_moving_a_photo_into_an_album_carries_its_events_over(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """「移动到相册」改的也是绝对路径，与改名是同一件事 —— 不搬就等于事件丢了。"""
    make_photo("海边-03.jpg", directory=library.root / "奈良")
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    library.bind_photos([photo], event_id)

    result = library.move_into(library.root / "奈良", [photo])

    assert result.did_anything
    assert library.events.photos_of_event(event_id) == (library.root / "奈良" / "海边-01.jpg",)
    assert library.events.events_for_photos([photo]) == {}


def test_undoing_a_move_carries_them_back(library: Library) -> None:
    """撤销一次移动仍然是一次移动，于是绑定也搬回去 —— 不做这一步，撤销会把事件弄丢。

    **中间那一步的断言不能省**：撤销之后路径与当初一模一样，于是「正向压根没搬」也能让这条
    绿过去 —— 那正是它曾经能通过的原因。只断终态等于没断。
    """
    event_id = _event(library)
    photo = library.root / "海边-01.jpg"
    moved = library.root / "京都" / "海边-01.jpg"
    library.bind_photos([photo], event_id)
    library.move_into(library.root / "京都", [photo])

    assert library.events.photos_of_event(event_id) == (moved,)

    undo_last_write(library.root, events=library.events)

    assert library.events.photos_of_event(event_id) == (photo,)
    assert library.events.events_for_photos([photo])


def test_moving_an_album_carries_every_photo_under_it(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """移相册改的是整棵子树的路径，逐张照片的绑定都要跟着换前缀。"""
    make_photo("海边-06.jpg", directory=library.root / "奈良")
    event_id = _event(library)
    album = library.root / "京都"
    library.bind_photos([album / "海边-02.jpg"], event_id)

    result = library.move_into(library.root / "奈良", [album])

    assert result.did_anything
    assert library.events.photos_of_event(event_id) == (
        library.root / "奈良" / "京都" / "海边-02.jpg",
    )


def test_moving_an_album_records_that_it_was_an_album(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """移完之后源路径已经不存在，那时再问 ``is_dir()`` 恒为假 —— 日志会把相册记成照片，
    而绑定的搬法（按前缀改写整棵子树）正是靠这一位分派。"""
    make_photo("海边-07.jpg", directory=library.root / "奈良")
    library.move_into(library.root / "奈良", [library.root / "京都"])

    record = pending_undo_runs(library.root)[0][0]

    assert record.action == ACTION_MOVE
    assert record.is_dir


def test_a_skipped_move_leaves_the_bindings_alone(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """跳过的那一项（已经在那个相册里）压根没动，绑定不许跟着「搬」一次。"""
    make_photo("海边-04.jpg", directory=library.root / "京都")
    event_id = _event(library)
    photo = library.root / "京都" / "海边-04.jpg"
    library.bind_photos([photo], event_id)

    result = library.move_into(library.root / "京都", [photo])

    assert not result.did_anything
    assert result.skipped
    assert library.events.photos_of_event(event_id) == (photo,)
