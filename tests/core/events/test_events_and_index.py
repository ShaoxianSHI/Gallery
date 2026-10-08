"""事件库与索引的连接（ADR 0043 决策 1）：挂得上去、分得开、清缓存伤不到它。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from gallery.core import paths
from gallery.core.events.store import EventDraft, EventStore
from gallery.core.library import Library


def _library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True, exist_ok=True)
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root)
    return Library.open(root)


def test_the_events_live_in_their_own_file(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """事件一个字节都不进 ``index.db`` —— 索引的表结构版本一对不上就整表重建，
    而事件重扫照片重建不出来（ADR 0043 决策 1）。"""
    library = _library(tmp_path, make_photo)
    library.create_event("trip", EventDraft(values={"title": "京都五日"}))

    assert paths.events_file(library.root).exists()
    assert isinstance(library.facet_index.attached, EventStore)


def test_clearing_the_cache_does_not_touch_the_events(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**「``.gallery/`` 里除 ``config.toml`` 外都是缓存」这条硬性约束有了一个例外**（ADR 0043）。

    ``clear_cache`` 是「把缓存扔了重扫」那一个入口，它永远不许碰 events.db。这条用例改坏
    就会红 —— 它是这个例外唯一的守卫。
    """
    library = _library(tmp_path, make_photo)
    event = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    photos = [Path(library.root / "海边-01.jpg"), Path(library.root / "海边-02.jpg")]
    library.bind_photos(photos, event.id)

    library.clear_cache()

    survived = library.all_events()
    assert library.events_available
    assert [item.title for item in survived] == [event.title]
    assert set(library.events.photos_of_event(event.id)) == set(photos)


def test_rebuilding_the_index_does_not_touch_the_events(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """升 ``SCHEMA_VERSION`` 会 ``DROP`` 索引那两张表 —— 重建脚本只许列它自己的表名。"""
    library = _library(tmp_path, make_photo)
    library.create_event("trip", EventDraft(values={"title": "京都五日"}))
    library.facet_index.close()

    # 下一次打开时索引版本对不上，于是整表重建。
    connection = sqlite3.connect(paths.index_file(library.root))
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    reopened = Library.open(library.root)

    assert [event.title for event in reopened.all_events()] == ["京都五日"]


def test_the_index_is_still_usable_when_events_cannot_be_attached(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """挂不上（只读卷 / 网络盘）时**索引其余功能照常**，事件那一族安静地不可用（ADR 0043 决策 1）。

    这就是「自动降级 + 横幅说明」那条规矩的库内一半：不弹窗，也不让整个浏览瘫掉。事件库与
    索引同源（都住在 ``.gallery/``），所以「事件不可用」与「索引退成内存」是同一件事的两面。
    """
    library = _library(tmp_path, make_photo)
    # 沙箱里造不出真的只读卷，照 ``test_facade`` 那处的做法把判据掰过去再重开连接。
    library._config_dir_ready = False
    library.close_index()

    assert library.facet_index.path is None  # 索引没落盘
    assert library.events_available is False
    assert library.all_events() == ()

    # **浏览不受影响**：它压根不走索引，也不走事件库。
    photos = list(library.iter_photos(should_cancel=lambda: False, report=lambda *_args: None))
    assert len(photos) == 2


def test_every_write_makes_the_session_cache_fresh_again(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**每条写入口都要让 :meth:`Library.all_events` 那份缓存作废**（ADR 0043 决策 7）。

    这条用例是那次作废的**唯一**守卫。原先那四处 ``self._events_cache = None`` 只写在
    ``facade.py`` 的一句注释里（「写入口只有下面那几个，每次写完就地作废」）——而漏一处
    **不报错**：库写进去了，界面拿着的还是旧清单，症状是「刚建的事件不在菜单里」，
    要关掉重开这个库才看得见。

    现在作废挂在 :attr:`EventStore.on_change` 上（唯一写入方），于是这条用例走的是**行为**：
    每个写入口之后都断言 ``all_events()`` 已经是新的。新加一条写路径而忘了接上钩子，
    它就在那一步红。
    """
    library = _library(tmp_path, make_photo)
    assert library.all_events() == (), "前提：清单先是空的，而这一句已经把缓存填上了"

    created = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    assert [event.title for event in library.all_events()] == ["京都五日"], "新建之后没作废"

    library.update_event(created.id, EventDraft(values={"title": "京都七日"}))
    assert [event.title for event in library.all_events()] == ["京都七日"], "改完之后没作废"

    library.delete_event(created.id)
    assert library.all_events() == (), "删掉之后没作废"

    library.undo_last_write()
    assert [event.title for event in library.all_events()] == ["京都七日"], "撤销删除之后没作废"

    library.undo_last_write()
    assert [event.title for event in library.all_events()] == ["京都五日"], "撤销修改之后没作废"


def test_an_update_that_changes_nothing_still_leaves_the_cache_alone(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """没改动就不该扔缓存 —— 顺手扔掉只是白白多查一次库，判据是那条 ``changed``。"""
    library = _library(tmp_path, make_photo)
    created = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.all_events()
    assert library._events_cache is not None, "前提：缓存已经填上了"

    outcome = library.update_event(created.id, EventDraft(values={"title": "京都五日"}))

    assert not outcome.changed
    assert library._events_cache is not None, "没有改动却把缓存扔了"
