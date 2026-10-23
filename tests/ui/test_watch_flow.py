"""实时监听的界面流程（README 路线图 M4，ADR 0013）。

用户真正会经历的那些事：在访达里丢进来一张照片、从访达删掉一张、在访达里改个名、
用别的工具改完 EXIF 再回来 —— **都不用重开 Gallery**。这一组测试就把监听器换成假的，
好让这条链同步跑完：真等文件系统通知，那是一场耐力赛。

假监听器仍然经过真正的桥（:class:`~gallery.ui.window.watch.WatchBridge`）与排队连接，
所以「回调在别的线程上到达」这件事本身也是被测到的。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_for_level, wait_until

from gallery.core.events.store import EventDraft  # noqa: E402
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.search import make_condition, parse_input  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.library.watcher import (  # noqa: E402
    ChangeKind,
    LibraryChange,
)
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.core.metadata.report import MetadataReport  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window.watch import WatchBridge  # noqa: E402
from gallery.ui.window.watch_flow import WATCH_ECHO_TTL  # noqa: E402  （常量跟着监听那一段搬了家）

#: 等待异步扫描 / 缩略图完成的时限。


class FakeWatcher:
    """假的监听器：记下自己有没有被启动，并能随时塞一批变化进来。"""

    def __init__(self) -> None:
        self.running = False
        self.emit: Callable[[tuple[LibraryChange, ...]], None] | None = None

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def push(self, *changes: LibraryChange) -> None:
        """送一批变化进界面（等价于 watchdog 那边收到了这些事件）。"""
        assert self.emit is not None, "监听还没开始"
        self.emit(changes)


def make_starter(captured: list[FakeWatcher]):  # type: ignore[no-untyped-def]
    """造一个注入给窗口的「开监听」方式，并把它造出来的假监听器记下来。"""

    def starter(_library, on_change, _on_error=None):  # type: ignore[no-untyped-def]
        watcher = FakeWatcher()
        watcher.emit = on_change
        captured.append(watcher)
        return watcher

    return starter


def open_window(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_FLAT,
) -> tuple[MainWindow, FakeWatcher]:
    # 会话状态写在库外：放在库里面，相册模式下它自己就会变成一个相册卡片。
    session_base = root.parent / f"{root.name}-状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode)
    prepare_library(root)  # 没有配置目录时那个模态框在测试里没人点
    watchers: list[FakeWatcher] = []
    window = MainWindow(
        session_base=session_base,
        initial_library=root,
        watch_starter=make_starter(watchers),
    )
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    assert wait_until(qt_app, lambda: window._model.rowCount() > 0), "扫描没有在时限内完成"
    assert watchers, "打开库就该开始监听"
    return window, watchers[0]


def deliver(
    qt_app: QApplication, window: MainWindow, watcher: FakeWatcher, *changes: LibraryChange
):
    """送一批变化，并等界面把它处理完（排队连接要转一下事件循环）。"""
    watcher.push(*changes)
    qt_app.processEvents()


def row_of(window: MainWindow, path: Path) -> int:
    row = window._model.row_of_path(path)
    assert row >= 0, f"{path.name} 不在网格里"
    return row


def names(window: MainWindow) -> list[str]:
    return [entry.name for entry in window._model.entries()]


def _album_names(window: MainWindow) -> list[str]:
    """这一层里**相册卡片**的名字（照片不算）。"""
    return [entry.name for entry in window._model.entries() if isinstance(entry, FolderRef)]


def _settle_level(qt_app: QApplication, window: MainWindow) -> None:
    """等这一层重排完（换方向是同步的，等的是后台那一遍列层）。"""
    assert wait_until(qt_app, lambda: window._scan_job is None), "这一层还没列完"
    qt_app.processEvents()


def _enter(qt_app: QApplication, window: MainWindow, directory: Path) -> None:
    """走进一个相册（用户双击那一格），并等那一层列完。"""
    window.enter_folder(directory)
    wait_for_idle(qt_app, window, what=f"列出「{directory.name}」那一层")


def _go_up_to(qt_app: QApplication, window: MainWindow, present: Path) -> None:
    """退回上一层（用户按 Esc），并等 ``present`` 那一格真的回到屏幕上。"""
    window.go_up()
    wait_for_level(
        window,
        qt_app,
        lambda: window._model.row_of_path(present) >= 0,
        what=f"退回上一层后看到「{present.name}」",
    )


def _bind_one(window: MainWindow, photo: Path) -> int:
    """建一个事件并把 ``photo`` 记进去，返回事件 id。"""
    library = window._library
    assert library is not None and library.events_available
    event_id = library.create_event("trip", EventDraft.of(title="京都五日")).event.id
    assert library.bind_photos([photo], event_id) == 1
    return event_id


#: 测试里的修改时间基准（2023-11-14 前后）。具体值无所谓，只要先后是确定的。
MTIME_BASE = 1_700_000_000.0

#: 挪来挪去的那张照片，以及挪走 / 留下那两张：颜色各不相同，靠像素就能认出卡片上挂着谁。
#: 相册封面用扫描顺序的第一张，前缀因此写成 ``1-`` / ``2-``，不依赖汉字怎么排。
MOVED_NAME = "挪来挪去.jpg"
MOVED_COLOR = (40, 160, 60)
LEAVING_NAME = "1-第一张.jpg"
STAYING_NAME = "2-第二张.jpg"
LEAVING_COLOR = (200, 40, 30)
STAYING_COLOR = (30, 60, 200)


def touch(path: Path, step: float) -> None:
    """把照片的修改时间钉到 :data:`MTIME_BASE` + ``step``。

    默认排序按**修改时间**（``DEFAULT_SORT_FACET``），**升序**（ADR 0049）——
    ``step`` 越大越新，于是它排在越后面。顺序不能靠「谁先被创建」：文件系统给的是
    真实时间，那不是断言能说清楚的东西。
    """
    os.utime(path, (MTIME_BASE + step, MTIME_BASE + step))


# ---- 新增 ---------------------------------------------------------------------


def test_a_photo_dropped_in_shows_up_without_reopening(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """访达里丢进来一张照片：网格上立刻多一格，不用重开库。"""
    touch(make_photo("原来那张.jpg"), 0)
    window, watcher = open_window(qt_app, tmp_path)
    try:
        newcomer = make_photo("刚丢进来.jpg")
        touch(newcomer, 1)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert window._model.rowCount() == 2
        # 默认按修改时间升序：先在的那张在前。
        assert names(window) == ["原来那张.jpg", "刚丢进来.jpg"]
    finally:
        window.close()


def test_a_photo_dropped_in_is_searchable_right_away(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """丢进来的照片**这一次检索**就要搜得到，不必重开库。

    没有检索条件时监听器过去不更新索引的文件表，而打开库那一遍只在表为空时才跑 —— 于是新照片
    在搜索里永远不存在，重开也补不上（表已经非空）。
    """
    touch(make_photo("原来那张.jpg"), 0)
    window, watcher = open_window(qt_app, tmp_path)
    try:
        wait_for_idle(qt_app, window, also=("_index_job",), what="建立检索索引")
        newcomer = make_photo("刚丢进来.jpg")
        touch(newcomer, 1)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        library = window._library
        assert library is not None
        found = library.search_photos([make_condition(parse_input("刚丢进来"))])
        assert [ref.name for ref in found.refs] == ["刚丢进来.jpg"]
    finally:
        window.close()


def test_a_new_photo_lands_in_the_current_album_behind_a_symlink(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """库根带软链接时，丢进来的照片也要落到**当前这一层**的网格上。

    监听器报回来的是**解析过**的真路径（watchdog 就这么给，macOS 上 ``/var`` 链到
    ``/private/var``）。当前这一层若存着调用方给的原写法，「是不是在这一层」就永远
    说不成 —— 照片确实落到了磁盘上，网格却当没看见，而且不报错。
    """
    real = tmp_path / "真实"
    album = real / "京都"
    album.mkdir(parents=True)
    touch(make_photo("原来那张.jpg", directory=album), 0)
    link = tmp_path / "链接"
    link.symlink_to(real, target_is_directory=True)
    window, watcher = open_window(qt_app, link, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        # 用**软链接那种写法**进相册：用户从自己链过去的目录打开库时就是这样。
        window.enter_folder(link / "京都")
        assert wait_until(qt_app, lambda: window._scan_job is None)
        qt_app.processEvents()
        assert window._current_dir == album.resolve()

        newcomer = make_photo("刚丢进来.jpg", directory=album)
        touch(newcomer, 1)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert names(window) == ["原来那张.jpg", "刚丢进来.jpg"]
    finally:
        window.close()


def test_a_new_photo_lands_in_its_sorted_place_not_at_the_end(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """插在正确的位置：排在中间的照片就该待在中间，而不是追加到末尾。"""
    for step, name in enumerate(("a.jpg", "c.jpg")):
        touch(make_photo(name), float(step))
    window, watcher = open_window(qt_app, tmp_path)
    try:
        middle = make_photo("b.jpg")
        touch(middle, 0.5)  # 比 a 新、比 c 旧

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, middle))

        assert names(window) == ["a.jpg", "b.jpg", "c.jpg"]
    finally:
        window.close()


def test_in_flat_mode_a_photo_from_a_subfolder_is_sorted_among_all(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """平铺模式整棵子树都摆在屏幕上：新照片插进整层的正确位置，而不是追加到末尾。"""
    touch(make_photo("b.jpg", directory=tmp_path / "旅行"), 0)
    touch(make_photo("d.jpg"), 1)
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_FLAT)
    try:
        assert names(window) == ["b.jpg", "d.jpg"]
        nested = make_photo("c.jpg", directory=tmp_path / "旅行")
        touch(nested, 0.5)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, nested))

        assert names(window) == ["b.jpg", "c.jpg", "d.jpg"]
    finally:
        window.close()


def test_album_cards_follow_the_reverse_toggle(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """勾上「逆序」要把**相册卡片**也翻过来，而它们永远排在照片前面（ADR 0049）。

    相册卡片不进排序字段（文件夹没有拍摄时间），但方向跟着那一档走 —— 否则同一层里
    「照片翻了、相册没翻」，读起来像两样东西没对齐。
    """
    names_in = ["Bermuda", "Amsterdam", "Cairo"]
    for name in names_in:
        (tmp_path / name).mkdir()
        (tmp_path / name / "一张.jpg").write_bytes(b"x")
    ascending = sorted(names_in)
    make_photo("散着的一张.jpg")
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        _settle_level(qt_app, window)
        assert _album_names(window) == ascending
        assert names(window)[-1] == "散着的一张.jpg", "相册卡片永远排在照片前面"

        window.set_sort_descending(True)
        _settle_level(qt_app, window)

        assert _album_names(window) == ascending[::-1]
        assert names(window)[-1] == "散着的一张.jpg", "翻方向不该把相册卡片挪到照片后面"
    finally:
        window.close()


def test_a_new_album_shows_up_in_the_album_level(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册模式：新建的文件夹要变成相册卡片（相册永远排在照片前面）。"""
    make_photo("散着的一张.jpg")
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        (tmp_path / "2025 苏州").mkdir()
        (tmp_path / "2025 苏州" / "一张.jpg").write_bytes(b"x")

        deliver(
            qt_app,
            window,
            watcher,
            LibraryChange(ChangeKind.ADDED, tmp_path / "2025 苏州", is_dir=True),
        )

        assert isinstance(window._model.entry_at(0), FolderRef)
        assert names(window) == ["2025 苏州", "散着的一张.jpg"]
    finally:
        window.close()


def test_the_new_photo_really_gets_a_thumbnail(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """新格子不能停在占位框上：缩略图流水线得给它补一张。"""
    make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        newcomer = make_photo("新的.jpg")

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert wait_until(qt_app, lambda: window._model.has_thumbnail(newcomer))
    finally:
        window.close()


def test_a_photo_that_finished_downloading_gets_its_thumbnail_back(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """云盘里下到一半的照片补完之后，缩略图**要回来**。

    现场：OneDrive 之类先把照片以半截的样子放进文件夹（解码失败），下载完成后又报
    一次「创建」—— 同一个路径。这一格如果不换新引用就一直挂着半截时的大小与修改
    时间，缩略图缓存的 key 永远对不上；那份「这张读不出来」的记忆也不会被清掉。于是
    文件明明已经能读了，格子却一直停在黑占位图上，重开之前根本不会自己恢复。
    """
    make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        newcomer = tmp_path / "云盘里的.jpg"
        half = _jpeg_bytes((200, 30, 30))
        newcomer.write_bytes(half[: len(half) // 3])

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))
        assert wait_until(qt_app, lambda: newcomer in window._pipeline._failed, timeout=5), (
            "半截的照片本该读不出来"
        )

        # 下完了；同一个路径又报了一次「创建」。
        newcomer.write_bytes(_jpeg_bytes((30, 200, 30)))
        _touch(newcomer)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert wait_until(qt_app, lambda: window._model.has_thumbnail(newcomer)), (
            "文件已经补完了，缩略图却没回来"
        )
    finally:
        window.close()


def test_a_batch_of_photos_arrives_in_one_go(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """访达拷 500 张是一次拷贝：界面一次性接住，不必一张张闪。"""
    make_photo("a.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        copied = [make_photo(f"拷进来 {index}.jpg") for index in range(20)]

        deliver(
            qt_app,
            window,
            watcher,
            *(LibraryChange(ChangeKind.ADDED, path) for path in copied),
        )

        assert window._model.rowCount() == 21
    finally:
        window.close()


# ---- 删除 ---------------------------------------------------------------------


def test_a_photo_deleted_outside_disappears_from_the_grid(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    keep = make_photo("留下.jpg")
    doomed = make_photo("没了.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, doomed))

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert window._model.rowCount() == 1
        assert names(window) == ["留下.jpg"]
        assert window._model.row_of_path(keep) >= 0
    finally:
        window.close()


def test_deleting_the_selected_photo_moves_the_selection(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """删掉选中那一格之后，选中要落到邻近的行，而不是「什么都没有选中」。"""
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_photo(name)
    doomed = tmp_path / "b.jpg"
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, doomed))

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert window._grid.current_row() >= 0
    finally:
        window.close()


def test_a_whole_album_deleted_takes_every_row_with_it(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("散着的.jpg")
    (tmp_path / "相册").mkdir()
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        assert window._model.rowCount() == 2

        deliver(
            qt_app,
            window,
            watcher,
            LibraryChange(ChangeKind.REMOVED, tmp_path / "相册", is_dir=True),
        )

        assert names(window) == ["散着的.jpg"]
    finally:
        window.close()


def test_a_deleted_photo_leaves_no_row_in_the_metadata_index(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """索引以绝对路径为主键：照片没了那一行得跟着走，否则它会永远留在那儿（ADR 0013）。"""
    doomed = make_photo("没了.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        library = window._library
        assert library is not None
        index = library.facet_index
        index.store([(PhotoRef.from_path(doomed), _facets())])
        assert index.lookup([PhotoRef.from_path(doomed)])

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert index.lookup([PhotoRef.from_path(doomed)]) == {}
    finally:
        window.close()


def test_a_gone_photo_keeps_no_thumbnail_in_memory(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """那几份按路径索引的内存缓存也要丢：路径上已经没有文件了。"""
    doomed = make_photo("没了.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        assert wait_until(qt_app, lambda: window._model.has_thumbnail(doomed))
        window._preview_cache[doomed] = window._preview_cache.get(doomed) or _image()  # type: ignore[assignment]

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert not window._model.has_thumbnail(doomed)
        assert doomed not in window._preview_cache
        assert doomed not in window._metadata_cache
    finally:
        window.close()


# ---- 改名与内容变化 ------------------------------------------------------------


def test_renaming_outside_keeps_the_row_and_the_selection(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """访达里改个名：那一格原地换掉，选中还在 —— 不是「删一张再加一张」。"""
    before = make_photo("旧名.jpg")
    after = tmp_path / "新名.jpg"
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, before))
        before.rename(after)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, before, dest_path=after))

        assert window._model.rowCount() == 1
        assert names(window) == ["新名.jpg"]
        assert window._model.row_of_path(before) == -1
        assert window._grid.current_row() == row_of(window, after)
    finally:
        window.close()


# ---- 外部改名 / 移动要带着事件绑定 --------------------------------------------


def test_renaming_outside_carries_the_event_bindings(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """访达里改个名，事件绑定跟着走（ADR 0043 决策 8）。

    **这是监听这一层唯一知道「访达里改了路径」的地方**：应用内改名走 ``core.rename``，
    而访达里的那一下只能从这里知道。绑定跟着网格一起走，照片就不会「还在、事件没了」。
    """
    before = make_photo("旧名.jpg")
    after = tmp_path / "新名.jpg"
    window, watcher = open_window(qt_app, tmp_path)
    try:
        event_id = _bind_one(window, before)
        before.rename(after)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, before, dest_path=after))

        assert window._library.events.photos_of_event(event_id) == (after,)
    finally:
        window.close()


def test_moving_a_whole_album_outside_carries_every_binding_under_it(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册在访达里被改名 / 挪走：里面每一张照片的绝对路径都变了，绑定逐张换前缀。"""
    make_photo("深处.jpg", directory=tmp_path / "京都" / "子相册")
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        event_id = _bind_one(window, tmp_path / "京都" / "子相册" / "深处.jpg")
        (tmp_path / "京都").rename(tmp_path / "奈良")

        deliver(
            qt_app,
            window,
            watcher,
            LibraryChange(
                ChangeKind.MOVED, tmp_path / "京都", dest_path=tmp_path / "奈良", is_dir=True
            ),
        )

        assert window._library.events.photos_of_event(event_id) == (
            tmp_path / "奈良" / "子相册" / "深处.jpg",
        )
    finally:
        window.close()


def test_bindings_follow_a_move_even_while_the_level_is_frozen(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**延后前置条件只该推迟改界面，不该推迟修数据。**

    日历页 / 大图页 / 扫描进行中时这批变化会累积进 ``_watch_pending``，而它可能被直接丢掉
    （扫描一完成就 ``_watch_pending = None`` 再重扫那一层）。绑定要是跟着界面页面的状态走，
    那正是「照片还在、事件没了」的来路。
    """
    before = make_photo("旧名.jpg")
    after = tmp_path / "新名.jpg"
    window, watcher = open_window(qt_app, tmp_path)
    try:
        event_id = _bind_one(window, before)
        before.rename(after)
        window._show_calendar()  # 不能就地改网格的那一页

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, before, dest_path=after))

        assert window._library.events.photos_of_event(event_id) == (after,)
    finally:
        window.close()


def test_moving_a_photo_into_another_album_shows_it_here_only(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """从子文件夹拖到当前层：当前层多一格（在这一层里它就是新出现的）。"""
    source = make_photo("挪上来.jpg", directory=tmp_path / "另一处")
    destination = tmp_path / "挪上来.jpg"
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        assert window._model.rowCount() == 1, "相册模式下只有「另一处」这个相册"
        source.rename(destination)

        deliver(
            qt_app,
            window,
            watcher,
            LibraryChange(ChangeKind.MOVED, source, dest_path=destination),
        )

        assert names(window) == ["另一处", "挪上来.jpg"]
    finally:
        window.close()


def test_moving_a_photo_out_of_this_level_takes_its_row(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    gone = make_photo("挪走了.jpg")
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        away = tmp_path / "别的库" / "挪走了.jpg"
        away.parent.mkdir(parents=True, exist_ok=True)
        gone.rename(away)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, gone))

        assert window._model.rowCount() == 0
    finally:
        window.close()


# ---- 相册封面 -----------------------------------------------------------------


def test_a_photo_moved_into_an_album_that_is_not_here_still_changes_its_cover(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**用户报的那一条**：站在别的相册里把照片挪进新建的那个相册，退回去它还是文件夹图标。

    「移动到相册」的子菜单列的是**全库所有相册**（见 ``_album_targets``），所以目标相册十有八九
    不在你站着的那一层上。而封面作废曾经只认「当前这一层的直接子目录、且正好是屏幕上的一行」——
    于是那次作废压根没发生，流水线上「这个相册是空的」那份记忆活过换层，退回去重扫时又被它
    当场跳过，卡片就一直停在文件夹图标上（只有改一次缩略图尺寸才会重新问）。
    """
    album = tmp_path / "新建相册"
    album.mkdir()
    elsewhere = tmp_path / "别处"
    photo = make_photo(MOVED_NAME, directory=elsewhere, color=MOVED_COLOR)
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        pin_thumbnail_edge(window)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, album, is_dir=True))
        # 空相册那张卡片确实问过一次封面（正是那份「什么都没有」要被作废）。
        assert wait_until(qt_app, lambda: album in window._pipeline._empty)

        _enter(qt_app, window, elsewhere)
        inside = album / photo.name
        photo.rename(inside)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, photo, dest_path=inside))
        _go_up_to(qt_app, window, album)

        assert wait_until(qt_app, lambda: _shows(window, album, MOVED_COLOR)), (
            "照片已经挪进相册了，退回去那张卡片还挂着文件夹图标"
        )
    finally:
        window.close()


def test_a_photo_deleted_inside_the_album_next_to_this_level_changes_its_cover(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册卡片就在眼前时，里面那张被删了：封面要换成剩下那张。

    这一条今天本来就是好的（照片在相册里 → 不在当前层上 → 走 ``else`` 那一支）。它钉的是
    **封面作废被挪出各分支之后这条仍然成立** —— 那次挪动顺手把三支里的 ``covers.update`` 删了。
    """
    album = tmp_path / "京都"
    album.mkdir()
    doomed = make_photo(LEAVING_NAME, directory=album, color=LEAVING_COLOR)
    make_photo(STAYING_NAME, directory=album, color=STAYING_COLOR)
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        pin_thumbnail_edge(window)
        assert wait_until(qt_app, lambda: _shows(window, album, LEAVING_COLOR))

        doomed.unlink()
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert wait_until(qt_app, lambda: _shows(window, album, STAYING_COLOR)), (
            "封面还挂着已经删掉的那张"
        )
    finally:
        window.close()


def test_an_album_deeper_down_changes_its_own_cover_too(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """封面找的是「里面（**含更深层级**）第一张照片」，所以空的祖先相册也跟着换。

    只作废直接父目录的话：`甲` 的卡片永远停在文件夹图标上，而它其实已经有照片了。
    """
    album = tmp_path / "甲"  # 一个字节照片都没有的相册
    nested = album / "乙"
    nested.mkdir(parents=True)
    photo = make_photo(MOVED_NAME, directory=tmp_path / "别处", color=MOVED_COLOR)
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        pin_thumbnail_edge(window)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, album, is_dir=True))
        assert wait_until(qt_app, lambda: album in window._pipeline._empty)

        _enter(qt_app, window, tmp_path / "别处")
        inside = nested / photo.name
        photo.rename(inside)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, photo, dest_path=inside))
        _go_up_to(qt_app, window, album)

        assert wait_until(qt_app, lambda: _shows(window, album, MOVED_COLOR)), (
            "照片在「甲/乙」里，「甲」的封面却没有换"
        )
    finally:
        window.close()


def test_a_photo_moved_into_the_album_next_to_this_level_changes_its_cover(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """照片就在这一层上、挪进旁边那个相册：库内的挪动报的是**改名**而不是「新增」。"""
    album = tmp_path / "新建相册"
    album.mkdir()
    photo = make_photo(MOVED_NAME, directory=tmp_path, color=MOVED_COLOR)
    window, watcher = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        pin_thumbnail_edge(window)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, album, is_dir=True))
        assert wait_until(qt_app, lambda: album in window._pipeline._empty)

        inside = album / photo.name
        photo.rename(inside)
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MOVED, photo, dest_path=inside))

        assert wait_until(qt_app, lambda: _shows(window, album, MOVED_COLOR)), (
            "照片已经挪进相册了，那张卡片还挂着文件夹图标"
        )
    finally:
        window.close()


def pin_thumbnail_edge(window: MainWindow) -> None:
    """把缩略图档位固定在当前这一档（不再听网格改尺寸）。

    **为什么这一组非钉不可**：空相册那份记忆是**按档位**记的（128 px 问出来是空相册，不代表
    256 px 也是），所以窗口刚打开、网格还在改尺寸的那几轮里，那张卡片会被反反复复重问一遍
    封面 —— 于是「挪进去之后封面出来了」这条断言会在**档位那一遍**里自己变绿，而真机上
    （窗口早摆定了、档位不变）封面根本不会回来。锁定档位，断的才是用户看见的那件事。
    """
    window._grid.icon_edge_changed.disconnect(window._on_grid_edge_changed)


def _shows(window: MainWindow, path: Path, color: tuple[int, int, int]) -> bool:
    """那一格画出来的是不是这张底色的照片（还没解码出来时答 ``False``）。

    **按色阶比，不按整数比**：磁盘上的缩略图缓存存的是 JPEG，解码回来偏几个色阶。
    """
    pixmap = window._model.thumbnail_pixmap(path)
    if pixmap is None:
        return False
    got = pixmap.toImage().pixelColor(0, 0).getRgb()[:3]
    return all(abs(one - want) <= 24 for one, want in zip(got, color, strict=True))


def test_a_photo_changed_elsewhere_refreshes_its_row(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """外部工具覆盖写了一张照片：网格上的引用不能还挂着旧的大小与修改时间。"""
    picture = make_photo("被改过.jpg", size=(64, 48))
    window, watcher = open_window(qt_app, tmp_path)
    try:
        before = window._model.entry_at(row_of(window, picture))
        assert isinstance(before, PhotoRef)
        picture.write_bytes(picture.read_bytes() + b"\x00" * 32)  # 外部工具覆盖写了一次
        _touch(picture)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MODIFIED, picture))

        after = window._model.entry_at(row_of(window, picture))
        assert isinstance(after, PhotoRef)
        assert after.mtime_ns != before.mtime_ns or after.size != before.size
    finally:
        window.close()


def test_a_changed_photo_forgets_the_stale_caches(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """手里那份预览与元数据不再作数：否则面板显示的还是旧值。"""
    picture = make_photo("被改过.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._metadata_cache[picture] = (0, 0, _metadata())  # type: ignore[assignment]
        assert wait_until(qt_app, lambda: window._model.has_thumbnail(picture))

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.MODIFIED, picture))

        assert picture not in window._metadata_cache
        assert not window._model.has_thumbnail(picture)
    finally:
        window.close()


def test_an_empty_level_says_so_again(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """删到一张不剩时，那句「这里是空的」要回来。"""
    only = make_photo("唯一一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, only))

        assert "没有找到可显示的照片" in window._banner.text()
    finally:
        window.close()


def test_a_change_that_arrives_while_a_new_photo_fills_the_level(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """扫到一半又来了变化：先记下来，扫完再补 —— 宁可晚半秒，不要插错位置。"""
    make_photo("a.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        newcomer = make_photo("b.jpg")
        window._scan_job = 999  # 假装还有一次扫描进行中
        try:
            deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))
            assert window._model.rowCount() == 1, "扫描在途时不插行"
            assert window._watch_pending is not None, "这批变化要攒着，不是丢掉"
            assert window._watch_pending[0].path == newcomer
        finally:
            window._scan_job = None
    finally:
        window.close()


def test_changes_that_had_to_wait_are_applied_later(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """累积下来的那批变化不是丢掉，是等条件合适时补上（ADR 0013）。

    这一条专门锁定「补」：之前只测了「先暂存」，暂存的东西没人兑现的话，
    用户看到的就永远是那张少了照片的旧网格。
    """
    make_photo("a.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        newcomer = make_photo("b.jpg")
        window._scan_job = 999  # 假装还有一次扫描进行中
        try:
            deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))
            assert window._model.rowCount() == 1
        finally:
            window._scan_job = None

        window._flush_pending_watch()

        assert window._model.rowCount() == 2
        assert window._watch_pending is None
    finally:
        window.close()


def test_changes_that_arrive_in_the_detail_view_wait_for_the_grid(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """正看着大图时磁盘变了：网格该改，但要等回到网格时再改（大图页没有网格可改）。"""
    picture = make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, picture))
        window._open_detail(window._model.index(row_of(window, picture), 0))
        qt_app.processEvents()
        newcomer = make_photo("刚来的.jpg")

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))
        assert window._model.rowCount() == 1, "大图页上不动网格"
        assert window._watch_pending is not None

        window.show_grid()
        qt_app.processEvents()

        assert window._model.rowCount() == 2
    finally:
        window.close()


# ---- 自家写操作的回环 -----------------------------------------------------------


def test_renaming_here_is_not_applied_twice(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """监听器会把自家的改名原样报回来；同一件事不应用第二遍（ADR 0013）。"""
    picture = make_photo("原名.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, picture))
        window._grid.begin_rename(window._grid.currentIndex())
        qt_app.processEvents()
        window._on_rename_committed(window._grid.current_row(), "改成它.jpg")
        renamed = tmp_path / "改成它.jpg"
        assert renamed.exists()

        # 监听器随后报上来的回环：路径已经换过、文件也在，不该把那一格搞乱。
        deliver(
            qt_app,
            window,
            watcher,
            LibraryChange(ChangeKind.MOVED, picture, dest_path=renamed),
        )

        assert names(window) == ["改成它.jpg"]
        assert window._model.row_of_path(picture) == -1
    finally:
        window.close()


def test_trashing_here_is_not_applied_twice(
    qt_app: QApplication,
    tmp_path: Path,
    fake_trash: Path,
    make_photo: Callable[..., Path],
) -> None:
    """删掉之后监听器报「这个路径不见了」：那一格已经摘过，不必再摘一次。"""
    picture = make_photo("删掉.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, picture))
        window.trash_selected()
        qt_app.processEvents()
        assert window._model.rowCount() == 0

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, picture))

        assert window._model.rowCount() == 0
        assert "已把「删掉.jpg」移到废纸篓" in window._banner.text()
    finally:
        window.close()


def test_restoring_from_the_trash_is_not_inserted_twice(
    qt_app: QApplication,
    tmp_path: Path,
    fake_trash: Path,
    make_photo: Callable[..., Path],
) -> None:
    """取回来的照片，监听器随后也会报「新增」：同一个路径只该有一行。"""
    picture = make_photo("取回.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, picture))
        window.trash_selected()
        qt_app.processEvents()

        window.undo_last_write()
        qt_app.processEvents()

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, picture))

        assert window._model.rowCount() == 1
        assert names(window) == ["取回.jpg"]
    finally:
        window.close()


def test_a_genuinely_new_file_with_the_same_name_is_not_swallowed(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """回环只在短时间内有效：之后用户真的新建一个同名文件，必须正常显示。"""
    picture = make_photo("同名.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window._note_self_write(picture)
        assert picture in window._watch_echoes, "自家写操作要留个回声记号"
        window._watch_echoes = {  # 把记号的时间戳推到过去，等于让它过期
            path: recorded - WATCH_ECHO_TTL - 1 for path, recorded in window._watch_echoes.items()
        }

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, picture))

        assert window._model.rowCount() == 0
    finally:
        window.close()


# ---- 分组视图 -----------------------------------------------------------------


def _enter_folder_group(qt_app: QApplication, window: MainWindow) -> str:
    """按文件夹分组（这条规则不用读 EXIF，测试里跑得动），并进入第一个组。"""
    window.set_group_facet("folder")
    assert wait_until(qt_app, lambda: window._model.group_count() > 0), "分组没有出现"
    group = window._model.group_at(0)
    assert group is not None
    window.enter_group(group)
    # 进入分组后照片是异步铺上来的：等到真有行数了才算进了这个组。
    assert wait_until(qt_app, lambda: window._group_view is not None and window._model.rowCount())
    return group.key


def test_a_photo_deleted_inside_a_group_leaves_the_group(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """组里的照片被删了：那一格立刻消失，组卡片上的张数也跟着走。"""
    doomed = make_photo("没了.jpg")
    make_photo("还在.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        _enter_folder_group(qt_app, window)
        assert window._model.rowCount() == 2

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert window._model.row_of_path(doomed) == -1
        assert window._model.rowCount() == 1
    finally:
        window.close()


def test_a_photo_added_inside_a_group_regroups_and_comes_back(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """组里的成员是上一次分组算出来的快照：新照片要重新分组才归位。

    做的是「整个库重新分组一次，再回到同一个组」，而不是让组卡片自己长出新成员 ——
    后者得在每次分组时都维护一份反向索引，代价与出错面都不是这里该付的。
    """
    make_photo("一张.jpg")
    make_photo("两张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        key = _enter_folder_group(qt_app, window)
        newcomer = make_photo("刚来的.jpg")

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert wait_until(qt_app, lambda: window._model.row_of_path(newcomer) >= 0)
        assert window._group_view is not None and window._group_view.key == key
    finally:
        window.close()


def test_regrouping_to_come_back_keeps_the_photo_you_were_on(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """这一趟是软件自己绕出去的：库里只多了一张照片，不该把人正在看的那张甩掉。

    与「重新进入一层不留选中」是同一件事的两面：那一条说的是**用户自己退出去再点进来**，
    这一条说的是软件为了重新分组绕了一圈又回来 —— 用户从头到尾没离开过。
    """
    make_photo("一张.jpg")
    make_photo("两张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        _enter_folder_group(qt_app, window)
        looking_at = window._model.photo_at(0)
        assert looking_at is not None
        window._grid.select_row(0)

        newcomer = make_photo("刚来的.jpg")
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, newcomer))

        assert wait_until(qt_app, lambda: window._model.rowCount() == 3)
        row = window._model.row_of_path(looking_at.path)
        assert row >= 0, "刚才在看的那张还在组里"
        assert window._grid.genuinely_selected_rows() == [row], "绕一圈回来仍停在同一张上"
    finally:
        window.close()


# ---- 分组**顶层**（不是组内部）------------------------------------------------
#
# 这一段与上面那一段是**两件事**：上面是「已经进了某个组，组里的成员是快照」，
# 那里监听器有专门的一条路（重新分组再回来）。而分组**顶层**摆的是**组卡片**，
# 照片只存在于 ``GroupRef.photos`` 这个元组里 —— 模型的行**不是**这一层的照片列表。


def _group_the_level(qt_app: QApplication, window: MainWindow) -> GroupRef:
    """在**分组顶层**摆出组卡片（不进任何一个组）—— 这一层的行就是卡片，不是照片。"""
    window.set_group_facet("folder")
    assert wait_until(qt_app, lambda: window._model.group_count() > 0), "分组没有出现"
    assert window._group_view is None, "停在顶层，不要进组"
    card = window._model.group_at(0)
    assert isinstance(card, GroupRef)
    return card


def _members_of(window: MainWindow, key: str) -> list[str]:
    """那张卡片此刻摆着哪几张（按文件名）。整层重算的途中模型是空的 —— 那一下给 ``[]``。"""
    for entry in window._model.entries():
        if isinstance(entry, GroupRef) and entry.key == key:
            return sorted(photo.name for photo in entry.photos)
    return []


@pytest.mark.parametrize(
    "browse_mode", [BROWSE_MODE_FLAT, BROWSE_MODE_ALBUMS], ids=["平铺", "相册"]
)
def test_a_new_photo_on_the_grouped_level_does_not_eat_the_existing_ones(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    browse_mode: str,
) -> None:
    """**分组顶层**来了新照片：原来的那些一张都不许少。

    这一条是回归：那一层摆的是组卡片，而照片只存在于 ``GroupRef.photos`` 里，所以
    「往里插一行照片」在这里根本不成立 —— 插进去的那一行让重排只看见新来的几张，
    重建出的卡片里**原来的全没了**（症状是那个组忽然只剩新照片那一张）。
    """
    make_photo("一张.jpg")
    make_photo("两张.jpg")
    window, watcher = open_window(qt_app, tmp_path, browse_mode=browse_mode)
    try:
        card = _group_the_level(qt_app, window)
        before = _members_of(window, card.key)
        assert before == ["一张.jpg", "两张.jpg"]

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, make_photo("刚来的.jpg")))

        assert wait_until(qt_app, lambda: "刚来的.jpg" in _members_of(window, card.key))
        assert _members_of(window, card.key) == ["一张.jpg", "两张.jpg", "刚来的.jpg"]
    finally:
        window.close()


def test_the_grouped_level_never_gets_a_bare_photo_row(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """分组顶层那一层的行**必须全是组卡片** —— 混进一个裸 ``PhotoRef`` 就是上面那条病的
    现场：重排拿到的候选集只剩它，原件就此从界面上消失。"""
    make_photo("一张.jpg")
    make_photo("两张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        _group_the_level(qt_app, window)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, make_photo("刚来的.jpg")))

        assert wait_until(qt_app, lambda: window._model.group_count() > 0)
        stray = [e for e in window._model.entries() if not isinstance(e, GroupRef)]
        assert stray == [], f"卡片层上混进了照片行：{stray}"
    finally:
        window.close()


def test_a_photo_deleted_on_the_grouped_level_does_not_come_back(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """卡片层上删掉的照片**不许复活**。

    这一条钉的是**为什么不能走「把卡片里的照片并回来重新分组」那条更省的路**：
    ``remove_paths`` 只索引非卡片行，卡片里的照片不在那张表里，于是并回来的一份里还留着
    它 —— 屏幕上就出现一张磁盘上已经不在了的照片。

    **文件要真的删掉**：这一层是从磁盘重算的，只报一个事件而磁盘上还在，重算把它找回来是
    对的行为（那正是访达「删了又撤销回来」的样子）。
    """
    doomed = make_photo("没了.jpg")
    make_photo("还在.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        card = _group_the_level(qt_app, window)
        assert _members_of(window, card.key) == ["没了.jpg", "还在.jpg"]

        doomed.unlink()
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.REMOVED, doomed))

        assert wait_until(qt_app, lambda: _members_of(window, card.key) == ["还在.jpg"])
    finally:
        window.close()


def test_the_grouped_level_keeps_the_card_you_were_on(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """重算整层不该把用户刚点的那张组卡片甩掉 —— 那一格停着的地方要靠**标识**找回来。"""
    make_photo("一张.jpg")
    make_photo("两张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        looking_at = _group_the_level(qt_app, window)
        window._grid.select_row(0)

        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, make_photo("刚来的.jpg")))

        assert wait_until(qt_app, lambda: _row_of_card(window, looking_at.key) >= 0)
        assert window._grid.current_row() == _row_of_card(window, looking_at.key), (
            "重算整层之后仍停在刚点的那张卡片上"
        )
    finally:
        window.close()


def _row_of_card(window: MainWindow, key: str) -> int:
    """那张卡片此刻在第几行，没有就 ``-1``（``GroupRef`` 不是磁盘上的东西，没有 ``path``）。"""
    for row, entry in enumerate(window._model.entries()):
        if isinstance(entry, GroupRef) and entry.key == key:
            return row
    return -1


def test_duplicate_on_the_grouped_level_keeps_the_original_in_the_card(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**分组顶层**按 ⌘D：卡片的成员从 1 张变 2 张，原件仍在。

    复制命令与监听器之间的那一次交接就是上一条用例的形状 —— 这一条钉的是用户真正会走的那条
    路（ADR 0044 的「复制」加上 ADR 0013 决策 3 的那条例外）。
    """
    original = make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        card = _group_the_level(qt_app, window)
        assert _members_of(window, card.key) == ["一张.jpg"]
        window._grid.select_row(_row_of_card(window, card.key))

        window.duplicate_selected()
        assert wait_until(qt_app, lambda: window._write_job is None), "复制没有在时限内结束"
        copy = tmp_path / "一张的副本.jpg"
        assert wait_until(qt_app, copy.is_file), "副本没有落盘"
        deliver(qt_app, window, watcher, LibraryChange(ChangeKind.ADDED, copy))

        assert wait_until(
            qt_app, lambda: _members_of(window, card.key) == ["一张.jpg", "一张的副本.jpg"]
        )
        assert original.is_file()
    finally:
        window.close()


# ---- 生命周期 -----------------------------------------------------------------


def test_closing_the_window_stops_the_watch(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """漏停的后果不是「多一个线程」，而是那个线程会往已销毁的窗口发信号。"""
    make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        assert watcher.running is True
    finally:
        window.close()

    assert watcher.running is False


def test_a_batch_arriving_after_the_bridge_is_gone_is_dropped_quietly(
    qt_app: QApplication,
) -> None:
    """桥已经没了的时候来一批变化：安静丢掉，不许把异常抛进监听线程。

    关窗与切库都会和防抖定时器撞上：``stop()`` 取消得了还没到期的那个定时器，拦不住
    **已经开跑**的那一个。这一条钉的正是那个缝 —— 抛出去的 ``RuntimeError`` 会顺着
    监听线程一路逃出去（测试里表现为 ``PytestUnhandledThreadExceptionWarning``，
    产品里就是一个没人接的线程异常）。
    """
    bridge = WatchBridge()
    bridge.deleteLater()
    # ``processEvents`` 不抽 DeferredDelete 队列，必须显式抽 —— 否则 C++ 对象其实还在。
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()

    bridge._on_change((LibraryChange(ChangeKind.ADDED, Path("/tmp/x.jpg")),))  # noqa: SLF001
    bridge._on_failed("出事了")  # noqa: SLF001


def test_switching_libraries_stops_the_old_watch(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """换库之后，上一个库的监听必须已经停掉。"""
    first = tmp_path / "第一个库"
    second = tmp_path / "第二个库"
    first.mkdir()
    second.mkdir()
    make_photo("一张.jpg", directory=first)
    make_photo("两张.jpg", directory=second)
    prepare_library(first)
    prepare_library(second)

    watchers: list[FakeWatcher] = []
    window = MainWindow(
        session_base=tmp_path / "状态",
        initial_library=first,
        watch_starter=make_starter(watchers),
    )
    try:
        assert wait_until(qt_app, lambda: len(watchers) == 1)

        window.open_library(second)
        assert wait_until(qt_app, lambda: len(watchers) == 2)

        assert watchers[0].running is False
        assert watchers[1].running is True
    finally:
        window.close()


def test_changes_after_closing_the_library_are_ignored(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """没有库了就没有「当前这一层」：晚到的一批变化必须被安静地丢掉。"""
    make_photo("一张.jpg")
    window, watcher = open_window(qt_app, tmp_path)
    try:
        window.close()

        watcher.push(LibraryChange(ChangeKind.ADDED, tmp_path / "迟到的.jpg"))

        assert window._library is None
    finally:
        window.close()


# ---- 小工具 -------------------------------------------------------------------


def _touch(path: Path) -> None:
    """把修改时间往后推：内容变了，mtime 也必须跟着变（索引的新鲜度就看它）。"""
    stamp = path.stat().st_mtime_ns + 10**9
    os.utime(path, ns=(stamp, stamp))


def _jpeg_bytes(
    color: tuple[int, int, int] = (200, 120, 40), size: tuple[int, int] = (64, 48)
) -> bytes:
    """现造一张最小合法 JPEG 的字节。

    测试数据一律现场构造：仓库里不放真实照片，也不放大尺寸二进制。
    """
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    image = Image.new("RGB", size, color)
    try:
        image.save(buffer, format="JPEG", quality=90)
    finally:
        image.close()
    return buffer.getvalue()


def _facets() -> PhotoFacets:
    return PhotoFacets(year=2024)


def _metadata() -> MetadataReport:
    return MetadataReport(path=Path("x.jpg"), groups=(), all_fields=(), source="exiftool")


def _image() -> QImage:
    return QImage(4, 4, QImage.Format.Format_RGB32)
