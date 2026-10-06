"""改名要带着事件绑定一起走（ADR 0043 决策 8）。

红线 1 唯一豁免的那一次改动会改路径，而绑定按路径记 —— 索引靠「大小 + 修改时间」自动过期、
用户无感，事件绑定不能这么兜。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.events.store import EventDraft, like_pattern
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
