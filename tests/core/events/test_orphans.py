"""孤儿绑定的判定（ADR 0043 决策 9）。

**这个文件最要紧的是「读不了」那几条。** 把「我读不了这个目录」当成「这里什么都没有」，
后果是用户在盘没挂上或没权限的时候，眼睁睁看着自己填的事件记录被清掉 —— 而且不可逆。
所以每一档都要有一条用例，且它们必须**真的分得开**（都断成同一个集合的话，这些用例就白写了）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.events.orphans import (
    CANCEL_EVERY,
    DirListing,
    OrphanScan,
    _read_directory,
    find_orphans,
    forget_orphans,
)
from gallery.core.events.store import EventDraft, EventStore, Orphan
from gallery.core.library import Library


@pytest.fixture
def library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True)
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root / "京都")
    return Library.open(root)


def _bind(library: Library, photos: list[Path], title: str = "京都五日") -> int:
    event_id = library.create_event("trip", EventDraft.of(title=title)).event.id
    library.bind_photos(photos, event_id)
    return event_id


def _empty() -> DirListing:
    """可读，且**什么都没有** —— 这正是「这里真的没有那张照片」。"""

    return DirListing(names=frozenset())


def _gone() -> DirListing:
    """目录确实不存在。**未挂载的盘也报这个。**"""

    return DirListing(names=None, gone=True)


def _unreadable() -> DirListing:
    """没权限 / 网络临时不可用那一档：**不是结论**。"""

    return DirListing(names=None, gone=False)


def _faked(readers: dict[Path, DirListing]) -> Callable[[Path], DirListing]:
    """按目录给不同的读结果。**没登记的目录读成「空的」**。"""

    def read(directory: Path) -> DirListing:
        return readers.get(directory, _empty())

    return read


def test_a_deleted_photo_in_a_readable_album_is_an_orphan(library: Library) -> None:
    """目录可读就是权威答案：清单里没有它，它没了。"""
    root = library.root
    _bind(library, [root / "海边-01.jpg", root / "京都" / "海边-02.jpg"])
    (root / "海边-01.jpg").unlink()
    read = _faked(
        {
            root: _empty(),  # 海边-01.jpg 没了
            root / "京都": DirListing(names=frozenset({"海边-02.jpg"})),  # 还在
        }
    )

    scan = find_orphans(library.events, read=read)

    assert [orphan.path.name for orphan in scan.missing] == ["海边-01.jpg"]
    assert not scan.vanished
    assert scan.event_titles() == ("京都五日",)


def test_an_unreadable_album_reports_nothing_at_all(library: Library) -> None:
    """**没权限 / 网络临时不可用：不知道，不是结论。** 一条都不报，更谈不上删。

    与上面那条的分别只在「目录读不读得到」—— 少判一次就是用户在盘没挂上时丢数据。
    """
    root = library.root
    _bind(library, [root / "海边-01.jpg", root / "京都" / "海边-02.jpg"])
    read = _faked(
        {
            root: DirListing(names=frozenset({"海边-01.jpg"})),  # 可读，还在
            root / "京都": _unreadable(),
        }
    )

    scan = find_orphans(library.events, read=read)

    assert not scan.found


def test_an_unmounted_volume_is_its_own_bucket_and_never_a_plain_orphan(
    library: Library,
) -> None:
    """**未挂载的盘报的也是「目录不在」** —— 与相册被删分不出来，所以必须单独一档。

    默认不删是这一档存在的全部理由：混进 ``missing`` 就是一键抹掉几千条记录。
    """
    root = library.root
    _bind(library, [root / "京都" / "海边-02.jpg"])
    read = _faked({root: _empty(), root / "京都": _gone()})

    scan = find_orphans(library.events, read=read)

    assert not scan.missing, "未挂载的盘绝不能落进可以放心清掉的那一档"
    assert [orphan.path.name for orphan in scan.vanished] == ["海边-02.jpg"]
    assert scan.paths(include_vanished=False) == ()
    assert scan.total(include_vanished=False) == 0


def test_an_unreadable_library_root_checks_nothing(library: Library) -> None:
    """库根读不了就整个不查 —— 那说明这个库整体有问题，此刻报出来的每一条都可能是假的。

    **这个前置条件只有在「根读不了、而某个子目录读得了」时才看得见**：只造一个「全都读不了」的库，
    每一档自己就会跳过，于是这条用例会**因为错的原因**绿过去 —— 少了这个条件判断也照样绿。
    """
    root = library.root
    _bind(library, [root / "京都" / "海边-02.jpg"])
    asked: list[Path] = []

    def read(directory: Path) -> DirListing:
        asked.append(directory)
        # 根读不了，子相册却读得了而且是空的 —— 少掉这个判断的话下面那条会被报成孤儿。
        return _unreadable() if directory == root else _empty()

    assert not find_orphans(library.events, read=read).found
    assert asked == [root], "库根读不了就不该再问别的目录"


def test_the_root_is_not_read_a_second_time(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """库根既是「整个不查」那个前置条件、又可能是绑定所在的目录 —— **同一份结果要复用**。"""
    root = library.root
    _bind(
        library,
        [make_photo("海边-03.jpg", directory=root), make_photo("海边-04.jpg", directory=root)],
    )
    reads: list[Path] = []

    def read(directory: Path) -> DirListing:
        reads.append(directory)
        return _empty()

    find_orphans(library.events, read=read)

    assert reads == [root], f"库根读了 {len(reads)} 次"


def test_bindings_that_are_all_alive_find_nothing(library: Library) -> None:
    """**什么都没找到就该彻底静默** —— 绝大多数用户永远看不到这个功能。"""
    root = library.root
    _bind(library, [root / "海边-01.jpg", root / "京都" / "海边-02.jpg"])
    read = _faked(
        {
            root: DirListing(names=frozenset({"海边-01.jpg"})),
            root / "京都": DirListing(names=frozenset({"海边-02.jpg"})),
        }
    )

    assert not find_orphans(library.events, read=read).found


def test_a_library_without_events_checks_nothing(library: Library) -> None:
    """配置目录建不出来的库上压根没有事件，而扫一遍不该成为开库的固定开销。"""
    assert not find_orphans(None).found
    assert not find_orphans(EventStore(library.root)).found


def test_one_directory_is_read_once_however_many_bindings_it_has(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """**按目录分组而不是逐个 stat** —— 400 项 617 ms → 25 ms 那次实测的同一个道理。

    断的是「同一个目录只出现一次」，而这条与上面那条**必须分开**：那条断的是库根别问两次，
    这条断的是 N 条绑定共用一次 scandir。写在一起的话第二条会盖掉第一条。
    """
    root = library.root
    album = root / "京都"
    _bind(
        library,
        [make_photo(f"海边-{index:02d}.jpg", directory=album) for index in range(20)],
    )
    reads: list[Path] = []

    def read(directory: Path) -> DirListing:
        reads.append(directory)
        return _empty()

    find_orphans(library.events, read=read)

    assert reads == [root, album], f"同一个目录读了 {len(reads)} 次"


def test_a_cancelled_scan_finds_nothing(library: Library, make_photo: Callable[..., Path]) -> None:
    """查完之前不该有任何可见动作 —— 用户切库或关窗，这一轮整个丢掉。"""
    root = library.root
    _bind(
        library,
        [make_photo(f"海边-{index:02d}.jpg", directory=root) for index in range(CANCEL_EVERY + 5)],
    )

    scan = find_orphans(library.events, should_cancel=lambda: True, read=_faked({}))

    assert not scan.found


def test_forgetting_removes_every_binding_of_a_gone_photo(library: Library) -> None:
    """一张照片可以同时属于两次旅行 —— **按 path 删，两条都得走**。"""
    root = library.root
    photo = root / "海边-01.jpg"
    _bind(library, [photo], title="京都五日")
    _bind(library, [photo], title="奈良三日")
    photo.unlink()

    scan = find_orphans(library.events, read=_faked({root: _empty()}))

    assert forget_orphans(library.events, scan, include_vanished=False) == 2
    assert not library.events.events_for_photos([photo])


def test_forgetting_keeps_the_vanished_bucket_by_default(library: Library) -> None:
    """用户没点头就不许动「相册不存在」那一档。"""
    root = library.root
    photo = root / "京都" / "海边-02.jpg"
    event_id = _bind(library, [photo])
    scan = find_orphans(library.events, read=_faked({root / "京都": _gone()}))

    assert forget_orphans(library.events, scan, include_vanished=False) == 0
    assert library.events.photos_of_event(event_id) == (photo,)

    assert forget_orphans(library.events, scan, include_vanished=True) == 1
    assert not library.events.photos_of_event(event_id)


def test_more_bindings_than_one_chunk_are_all_deleted(
    library: Library, make_photo: Callable[..., Path]
) -> None:
    """``IN (…)`` 有变量数上限，分批删 —— 超出一批的那些也得删掉。"""
    from gallery.core.events.store import CHUNK

    root = library.root
    photos = [make_photo(f"海边-{index:04d}.jpg", directory=root) for index in range(CHUNK + 20)]
    _bind(library, photos)
    scan = find_orphans(library.events, read=_faked({root: _empty()}))

    assert forget_orphans(library.events, scan, include_vanished=False) == CHUNK + 20
    assert not library.events.all_bindings()


def test_the_real_reader_tells_gone_from_unreadable(tmp_path: Path) -> None:
    """默认那个 reader 真的按错误码分档 —— 上面的用例全靠注入，注入会掩盖真实实现写错。"""
    present = tmp_path / "在"
    present.mkdir()
    (present / "照片.jpg").touch()

    readable = _read_directory(present)
    assert readable.readable and readable.names == frozenset({"照片.jpg"})
    assert not readable.gone

    gone = _read_directory(tmp_path / "没有这个目录")
    assert gone.gone and not gone.readable

    # 「路径存在但不是目录」是 ENOTDIR，**不是 ENOENT** —— 不能算「不在」。
    assert not _read_directory(present / "照片.jpg").gone


def test_the_prompt_names_the_events_that_lost_photos(library: Library) -> None:
    """提示框要说得清**是谁**少了照片，而不只是「少了几条」。"""
    root = library.root
    _bind(library, [root / "海边-01.jpg"], title="京都五日")
    _bind(library, [root / "京都" / "海边-02.jpg"], title="奈良三日")

    scan = find_orphans(
        library.events,
        read=_faked({root: _empty(), root / "京都": DirListing(names=frozenset({"海边-02.jpg"}))}),
    )

    assert set(scan.event_titles()) == {"京都五日"}


def test_an_unnamed_event_still_gets_a_word_in_the_prompt() -> None:
    """旧库里没填名称的事件（``Event.display_name`` 也为它留了后路），提示框不能显示成空串。"""
    scan = OrphanScan(
        missing=(Orphan(path=Path("/库/a.jpg"), event_id=1, event_title=""),),
    )

    assert scan.event_titles() == ("未命名",)


def test_an_empty_scan_says_so_and_deletes_nothing(library: Library) -> None:
    """空结果上那三个方法都得站得住 —— 空集合与「不知道」在这个模块里是同一种谨慎。"""
    assert not OrphanScan().found
    assert OrphanScan().total() == 0
    assert forget_orphans(library.events, OrphanScan(), include_vanished=True) == 0
