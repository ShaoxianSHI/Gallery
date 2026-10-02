"""实时监听的纯逻辑：事件 → 变化、按路径合并、去抖、生命周期。

监听本身**不做任何读写**，所以这里全部在 ``tmp_path`` 里用假观察者跑：不挂真的
watchdog 线程，也不等真的文件系统通知（那会把测试变成一场耐力赛）。被测的是三件事：

- **认得出变化**：四类事件各映射成什么，移进移出库外分别算新增还是消失；
- **合并得对**：访达拷 500 张照片必须变成一批，而不是 500 次界面改动；
- **看得见的过滤**：隐藏项、配置目录、不支持的扩展名、库外的路径一律不报。
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from watchdog.events import (
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MOVED,
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)
from watchdog.observers.api import BaseObserver

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.library.watcher import (
    ChangeKind,
    LibraryChange,
    LibraryWatcher,
    coalesce,
    summarize,
)

#: 默认静默期：足够长，测试期间不会自己到期（要自己 flush）。
MANUAL_DEBOUNCE_MS = 10_000


class ClosedEvent(FileModifiedEvent):
    """macOS 上「有人关了这个文件」—— 只说明读了一下，与相册无关。"""

    event_type = EVENT_TYPE_CLOSED


class MovedLookalike(FileCreatedEvent):
    """形状是创建事件，类型却写着 MOVED：watchdog 不会这么发，界面也不该猜。"""

    event_type = EVENT_TYPE_MOVED


class FakeObserver:
    """只记住被挂了哪些路径的假观察者：不真的监听，也不占线程。

    与 :class:`~watchdog.observers.api.BaseObserver` 鸭子相容即可（监听器只用到
    ``schedule`` / ``start`` / ``stop`` / ``join`` 这四个）。
    """

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, bool]] = []
        self.handler: object | None = None
        self.started = 0
        self.stopped = 0
        self.joined = 0

    def schedule(
        self, event_handler: object, path: str, *, recursive: bool = False, **_: object
    ) -> None:
        self.handler = event_handler
        self.scheduled.append((path, recursive))

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def join(self, timeout: float | None = None) -> None:
        self.joined += 1
        del timeout


def make_watcher(
    root: Path,
    *,
    recursive: bool = True,
    debounce_ms: int = MANUAL_DEBOUNCE_MS,
    max_hold_ms: int = 30_000,
) -> tuple[LibraryWatcher, FakeObserver, list[tuple[LibraryChange, ...]]]:
    """造一个监听器与它收下的一批批变化。

    默认的静默期长得不会自然到期：映射类测试都显式 :meth:`flush` 取那一批，
    这样「一次发几个事件」和「收到几批」之间没有时序上的巧合。
    """
    observer = FakeObserver()
    batches: list[tuple[LibraryChange, ...]] = []
    watcher = LibraryWatcher(
        root,
        recursive=recursive,
        on_change=batches.append,
        debounce_ms=debounce_ms,
        max_hold_ms=max_hold_ms,
        observer_factory=lambda: observer,
    )
    return watcher, observer, batches


def _typed(event_type: str, path: str) -> FileModifiedEvent:
    """造一个事件类型被改写过的「文件变了」：用来测类型不认识时的处理。"""

    event = FileModifiedEvent(path)
    event.event_type = event_type
    return event


def send(observer: FakeObserver, event: object) -> None:
    """把一个事件递给观察者上挂着的那个 handler。"""
    assert observer.handler is not None, "监听器还没开始"
    observer.handler.on_any_event(event)  # type: ignore[attr-defined]


# ---- 合并 -------------------------------------------------------------------


def test_a_new_photo_stays_new_after_it_is_modified(tmp_path: Path) -> None:
    """拷贝时照片先落盘再写内容：界面只要「新增」一条，不必先插再刷。"""
    path = tmp_path / "a.jpg"

    batch = coalesce(
        [
            LibraryChange(ChangeKind.ADDED, path),
            LibraryChange(ChangeKind.MODIFIED, path),
        ]
    )

    assert batch == (LibraryChange(ChangeKind.ADDED, path),)


def test_created_then_deleted_is_nothing(tmp_path: Path) -> None:
    """拷到一半取消、编辑器留下的临时文件：不值得让界面动一下。"""
    path = tmp_path / "临时.jpg"

    batch = coalesce(
        [
            LibraryChange(ChangeKind.ADDED, path),
            LibraryChange(ChangeKind.REMOVED, path),
        ]
    )

    assert batch == ()


def test_deleted_then_recreated_is_a_modification(tmp_path: Path) -> None:
    """网格上那一行一直在，只是里面的东西换了 —— 别把它摘了再插回来。"""
    path = tmp_path / "a.jpg"

    batch = coalesce(
        [
            LibraryChange(ChangeKind.REMOVED, path),
            LibraryChange(ChangeKind.ADDED, path),
        ]
    )

    assert batch == (LibraryChange(ChangeKind.MODIFIED, path),)


def test_a_rename_is_not_split_into_a_delete_and_an_add(tmp_path: Path) -> None:
    """访达改名的两端由 watchdog 合成一条事件：这里不该再把它拆开重排。"""
    before = tmp_path / "旧名.jpg"
    after = tmp_path / "新名.jpg"

    batch = coalesce(
        [
            LibraryChange(ChangeKind.ADDED, before),
            LibraryChange(ChangeKind.MOVED, before, dest_path=after),
            LibraryChange(ChangeKind.MODIFIED, after),
        ]
    )

    assert batch == (LibraryChange(ChangeKind.MOVED, before, dest_path=after),)


def test_a_batch_keeps_one_change_per_path(tmp_path: Path) -> None:
    """访达拷 500 张照片 = 500 个事件，必须合成一批 500 条，而不是别的东西。"""
    changes = [LibraryChange(ChangeKind.ADDED, tmp_path / f"{index}.jpg") for index in range(500)]

    batch = coalesce(changes)

    assert len(batch) == 500
    assert batch == tuple(changes)


def test_summary_says_what_happened(tmp_path: Path) -> None:
    """给日志看的一句话：新增 2 项、消失 1 项。"""
    batch = (
        LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),
        LibraryChange(ChangeKind.ADDED, tmp_path / "b.jpg"),
        LibraryChange(ChangeKind.REMOVED, tmp_path / "c.jpg"),
    )

    assert summarize(batch) == "新增 2 项、消失 1 项"


# ---- 事件映射与过滤 -----------------------------------------------------------


def test_events_become_the_four_kinds(tmp_path: Path) -> None:
    """文件与目录的创建 / 删除 / 改动都认得出来。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(tmp_path / "a.jpg")))
    send(observer, DirCreatedEvent(str(tmp_path / "相册")))
    send(observer, FileModifiedEvent(str(tmp_path / "a.jpg")))  # 拷进来还在写内容
    send(observer, FileDeletedEvent(str(tmp_path / "b.jpg")))
    send(observer, DirModifiedEvent(str(tmp_path / "相册")))
    watcher.flush()

    # 「a.jpg 新增」与「a.jpg 内容变了」合并成一条新增，目录自己的「变了」不算变化。
    assert batches == [
        (
            LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),
            LibraryChange(ChangeKind.ADDED, tmp_path / "相册", is_dir=True),
            LibraryChange(ChangeKind.REMOVED, tmp_path / "b.jpg"),
        )
    ]


def test_opening_and_closing_a_file_is_not_a_change(tmp_path: Path) -> None:
    """只是有人读了一下这个文件：macOS 上事件多得是，报上去界面就白重画。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, ClosedEvent(str(tmp_path / "a.jpg")))
    watcher.flush()

    assert batches == []


def test_moving_out_of_the_library_is_a_removal(tmp_path: Path) -> None:
    """拖去别的相册 = 拖去废纸篓之外的任何地方：这一格里它没有了。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileMovedEvent(str(tmp_path / "a.jpg"), str(tmp_path.parent / "a.jpg")))
    watcher.flush()

    assert batches == [(LibraryChange(ChangeKind.REMOVED, tmp_path / "a.jpg"),)]


def test_moving_into_the_library_is_an_addition(tmp_path: Path) -> None:
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileMovedEvent(str(tmp_path.parent / "a.jpg"), str(tmp_path / "a.jpg")))
    watcher.flush()

    assert batches == [(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),)]


def test_renaming_inside_the_library_is_a_move(tmp_path: Path) -> None:
    """界面可以原地换掉那一格：改名不是「删一张再加一张」。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileMovedEvent(str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")))
    watcher.flush()

    assert batches == [
        (
            LibraryChange(
                ChangeKind.MOVED, tmp_path / "a.jpg", dest_path=tmp_path / "b.jpg", is_dir=False
            ),
        )
    ]


@pytest.mark.parametrize(
    "name",
    [
        ".DS_Store",  # 隐藏文件
        f"{CONFIG_DIR_NAME}/cache/thumbs/a.jpg",  # 我们自己写缓存的那一层
        "读我.txt",  # 不是照片
        "raw.cr2",  # RAW 还不在支持范围里
    ],
)
def test_paths_the_scanner_never_shows_are_never_reported(tmp_path: Path, name: str) -> None:
    """「跳过什么」只有一份规则：扫描看不见的，监听也不该报。"""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(target)))
    watcher.flush()

    assert batches == []


def test_paths_outside_the_library_are_never_reported(tmp_path: Path) -> None:
    """逐层浏览与监听都不许越出用户给的文件夹。"""
    outside = tmp_path.parent / f"{tmp_path.name}-外面"
    outside.mkdir(exist_ok=True)
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(outside / "a.jpg")))
    watcher.flush()

    assert batches == []


def test_a_symlinked_folder_is_not_followed(tmp_path: Path) -> None:
    """链接到库外的目录不跟：跟进去既可能走出库，也可能无限递归。"""
    elsewhere = tmp_path.parent / f"{tmp_path.name}-目标"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "a.jpg").write_bytes(b"x")
    link = tmp_path / "链接"
    link.symlink_to(elsewhere)
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(link / "a.jpg")))
    watcher.flush()

    assert batches == []


# ---- 去抖与生命周期 -----------------------------------------------------------


def test_a_burst_of_events_arrives_as_one_batch(tmp_path: Path) -> None:
    """访达拷一批照片是连着来的事件；去抖之后只应该有一次界面改动。"""
    watcher, _observer, batches = make_watcher(tmp_path, debounce_ms=30)
    watcher.start()

    for index in range(5):
        watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / f"{index}.jpg"))  # noqa: SLF001
    time.sleep(0.2)

    assert len(batches) == 1
    assert len(batches[0]) == 5


def test_a_change_already_handled_is_not_delivered_twice(tmp_path: Path) -> None:
    watcher, _observer, batches = make_watcher(tmp_path, debounce_ms=20)
    watcher.start()

    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001
    time.sleep(0.2)
    watcher.flush()  # 空的一批不该凭空造出第二次回调

    assert len(batches) == 1


def test_a_continuous_stream_is_handed_over_anyway(tmp_path: Path) -> None:
    """一直在变也要一批批地交：慢慢拷进来的大目录不该等到最后才出现。"""
    watcher, _observer, batches = make_watcher(tmp_path, debounce_ms=40, max_hold_ms=60)
    watcher.start()

    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001
    time.sleep(0.1)
    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "b.jpg"))  # noqa: SLF001

    assert batches, "攒到上限就该交出去"
    assert batches[0] == (LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),)


def test_the_callback_must_not_take_the_lock_with_it(tmp_path: Path) -> None:
    """回调里若回头问监听器一句，也不该死锁（它在锁外被调用）。"""
    watcher, _observer, batches = make_watcher(tmp_path)
    watcher.start()

    def on_change(changes: tuple[LibraryChange, ...]) -> None:
        watcher.stop()  # 回调期间销毁监听器是最容易被忽略的一种
        batches.append(changes)

    watcher._on_change = on_change  # noqa: SLF001
    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001
    watcher.flush()

    assert len(batches) == 1


def test_starting_watches_the_root_with_the_configured_recursion(tmp_path: Path) -> None:
    """用户说不递归，就别去翻他的子文件夹。"""
    watcher, observer, _batches = make_watcher(tmp_path, recursive=False)
    watcher.start()

    assert observer.scheduled == [(str(tmp_path), False)]
    assert observer.started == 1


def test_starting_twice_does_nothing(tmp_path: Path) -> None:
    watcher, observer, _batches = make_watcher(tmp_path)
    watcher.start()
    watcher.start()

    assert observer.started == 1
    assert watcher.running is True


def test_stopping_stops_the_thread(tmp_path: Path) -> None:
    """漏掉这一步，那个线程会继续往一个已经销毁的窗口发信号。"""
    watcher, observer, _batches = make_watcher(tmp_path)
    watcher.start()
    watcher.stop()

    assert observer.stopped == 1
    assert observer.joined == 1
    assert watcher.running is False


def test_pending_changes_are_dropped_when_stopping(tmp_path: Path) -> None:
    watcher, _observer, batches = make_watcher(tmp_path, debounce_ms=10_000)
    watcher.start()
    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001

    watcher.stop()
    time.sleep(0.05)

    assert batches == []


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 的 FSEvents 原生递归，不需要补挂")
def test_a_new_folder_gets_its_own_watch(tmp_path: Path) -> None:
    """inotify 不递归：新出现的文件夹必须逐个补挂，否则它里面的照片收不到事件。"""
    (tmp_path / "相册" / "更深").mkdir(parents=True)
    watcher, observer, _batches = make_watcher(tmp_path)
    watcher.start()
    before = len(observer.scheduled)

    watcher._ingest(  # noqa: SLF001
        LibraryChange(ChangeKind.ADDED, tmp_path / "相册", is_dir=True)
    )

    added = [path for path, _recursive in observer.scheduled[before:]]
    assert added == [str(tmp_path / "相册"), str(tmp_path / "相册" / "更深")]


def test_the_callback_runs_off_the_main_thread(tmp_path: Path) -> None:
    """监听器住在自己的线程上：界面据此用排队连接把它搬回主线程（ADR 0013）。"""
    watcher, _observer, _batches = make_watcher(tmp_path, debounce_ms=20)
    watcher.start()
    threads: list[int] = []
    watcher._on_change = lambda _changes: threads.append(threading.get_ident())  # noqa: SLF001

    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001
    time.sleep(0.2)

    assert threads, "定时器到期就该把这一批交出去"
    assert threads[0] != threading.get_ident()


@pytest.mark.parametrize("factory_name", ["start_watch"])
def test_watching_does_not_write_anything(tmp_path: Path, factory_name: str) -> None:
    """监听是纯读的：跑一遍之后库里一个字节都不该多出来。"""
    del factory_name
    (tmp_path / "a.jpg").write_bytes(b"x")
    before = sorted(path.name for path in tmp_path.iterdir())

    watcher, observer, _batches = make_watcher(tmp_path)
    watcher.start()
    send(observer, FileCreatedEvent(str(tmp_path / "a.jpg")))
    send(observer, FileDeletedEvent(str(tmp_path / "a.jpg")))
    watcher.flush()
    watcher.stop()

    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_a_failure_in_the_callback_is_reported_not_raised(tmp_path: Path) -> None:
    """回调炸了要有人知道，但不能让它顺着监听线程一路抛出去。"""
    messages: list[str] = []
    watcher, observer, _batches = make_watcher(tmp_path)

    def explode(_changes: tuple[LibraryChange, ...]) -> None:
        raise RuntimeError("界面出事了")

    watcher._on_change = explode  # noqa: SLF001
    watcher._on_error = messages.append  # noqa: SLF001
    watcher.start()

    send(observer, FileCreatedEvent(str(tmp_path / "a.jpg")))
    watcher.flush()

    assert len(messages) == 1
    assert "日志" in messages[0]


def test_an_unknown_event_type_is_ignored(tmp_path: Path) -> None:
    """将来 watchdog 加了新的事件类型：不认得就别乱报，宁可漏过。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, _typed(EVENT_TYPE_DELETED, str(tmp_path / "a.jpg")))
    watcher.flush()

    assert batches == [(LibraryChange(ChangeKind.REMOVED, tmp_path / "a.jpg"),)]


def test_events_are_reported_in_the_order_they_happened(tmp_path: Path) -> None:
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, DirCreatedEvent(str(tmp_path / "相册")))
    send(observer, FileCreatedEvent(str(tmp_path / "相册" / "a.jpg")))
    watcher.flush()

    assert [change.path.name for change in batches[0]] == ["相册", "a.jpg"]


def test_moved_events_are_only_reported_when_they_are_moves(tmp_path: Path) -> None:
    """``MOVED`` 一定走 ``FileSystemMovedEvent``：形态不对就别猜。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, MovedLookalike(str(tmp_path / "a.jpg")))
    watcher.flush()

    assert batches == []


def test_a_watcher_survives_a_root_that_is_replaced(tmp_path: Path) -> None:
    """watchdog 报的是路径：库根被摘掉也不该让监听器崩掉。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, DirDeletedEvent(str(tmp_path)))
    watcher.flush()

    assert watcher.running is True
    # 库根本身不是网格上的一条，所以这一条什么也不做（监听器仍然好好地活着）。
    assert batches == []


def test_a_factory_that_explodes_leaves_nothing_running(tmp_path: Path) -> None:
    """观察者造不出来（例如系统句柄用尽）时不能留下半个状态。"""

    def boom() -> BaseObserver:
        raise OSError("句柄用尽")

    batches: list[tuple[LibraryChange, ...]] = []
    watcher = LibraryWatcher(tmp_path, on_change=batches.append, observer_factory=boom)

    with pytest.raises(OSError):
        watcher.start()

    assert watcher.running is False
    assert batches == []


def test_the_watcher_only_reports_photos_it_can_show(tmp_path: Path) -> None:
    """端到端一点点：真实文件 + 假观察器，断言交出去的那一批正好是网格该有的条目。"""
    (tmp_path / "a.jpg").write_bytes(b"x")
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(tmp_path / "a.jpg")))
    watcher.flush()

    assert batches[0] == (LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),)


@pytest.mark.parametrize("recursive", [True, False])
def test_stopping_twice_is_harmless(tmp_path: Path, recursive: bool) -> None:
    watcher, _observer, _batches = make_watcher(tmp_path, recursive=recursive)
    watcher.start()
    watcher.stop()
    watcher.stop()

    assert watcher.running is False


def test_a_directory_change_alone_is_not_reported(tmp_path: Path) -> None:
    """目录的「变了」几乎总是某个子项变了 —— 那件事自己会报事件，别把界面刷一遍。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    watcher._ingest(  # noqa: SLF001
        LibraryChange(ChangeKind.MODIFIED, tmp_path / "相册", is_dir=True)
    )
    watcher.flush()

    assert batches == []


def test_batch_order_is_stable_even_after_a_merge(tmp_path: Path) -> None:
    """合并之后仍然按发生顺序交：界面据此插行，用户看到的顺序才对。"""
    changes = [
        LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),
        LibraryChange(ChangeKind.ADDED, tmp_path / "b.jpg"),
        LibraryChange(ChangeKind.MODIFIED, tmp_path / "a.jpg"),
    ]

    assert coalesce(changes) == (
        LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"),
        LibraryChange(ChangeKind.ADDED, tmp_path / "b.jpg"),
    )


def test_repeated_identical_changes_collapse(tmp_path: Path) -> None:
    path = tmp_path / "a.jpg"
    assert coalesce(
        [
            LibraryChange(ChangeKind.MODIFIED, path),
        ]
        * 3
    ) == (LibraryChange(ChangeKind.MODIFIED, path),)


def test_removed_then_modified_is_still_removed(tmp_path: Path) -> None:
    """文件都没了，之后的「改了」是残留事件：不能让界面去找一个不存在的文件。"""
    path = tmp_path / "a.jpg"

    assert coalesce(
        [LibraryChange(ChangeKind.REMOVED, path), LibraryChange(ChangeKind.MODIFIED, path)]
    ) == (LibraryChange(ChangeKind.REMOVED, path),)


def test_coalesce_accepts_any_iterable(tmp_path: Path) -> None:
    """传生成器也一样：上层不必先把一批变化攒成列表。"""
    changes = (LibraryChange(ChangeKind.ADDED, tmp_path / f"{i}.jpg") for i in range(3))

    assert len(coalesce(changes)) == 3


def test_touches_lists_both_ends_of_a_rename(tmp_path: Path) -> None:
    before, after = tmp_path / "旧名.jpg", tmp_path / "新名.jpg"

    assert LibraryChange(ChangeKind.MOVED, before, dest_path=after).touches == (before, after)
    assert LibraryChange(ChangeKind.ADDED, before).touches == (before,)


def test_a_recreated_folder_is_reported_as_new(tmp_path: Path) -> None:
    """删掉整个相册又拷回来：它得重新出现在网格上。"""
    album = tmp_path / "相册"
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, DirCreatedEvent(str(album)))
    watcher.flush()

    assert batches == [(LibraryChange(ChangeKind.ADDED, album, is_dir=True),)]


def test_moving_a_folder_inside_the_library_is_a_move(tmp_path: Path) -> None:
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(
        observer,
        FileMovedEvent(str(tmp_path / "相册"), str(tmp_path / "另一处" / "相册")),
    )
    watcher.flush()

    assert batches == [
        (
            LibraryChange(
                ChangeKind.MOVED,
                tmp_path / "相册",
                dest_path=tmp_path / "另一处" / "相册",
                is_dir=False,
            ),
        )
    ]


def test_a_change_below_a_hidden_folder_is_never_reported(tmp_path: Path) -> None:
    """``.Trash`` / ``._`` 资源分叉里的东西，扫描看不见，监听也不该报。"""
    hidden = tmp_path / ".隐藏"
    hidden.mkdir()
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, FileCreatedEvent(str(hidden / "a.jpg")))
    watcher.flush()

    assert batches == []


def test_the_watcher_can_be_driven_from_any_thread(tmp_path: Path) -> None:
    """watchdog 在自己的线程上报事件；这里确认它真的能接住（不是只在主线程上假装能用）。"""
    watcher, _observer, batches = make_watcher(tmp_path, debounce_ms=10)
    watcher.start()

    def produce() -> None:
        watcher._ingest(  # noqa: SLF001
            LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg")
        )

    thread = threading.Thread(target=produce)
    thread.start()
    thread.join()
    time.sleep(0.2)

    assert len(batches) == 1


def test_the_factory_is_only_used_once(tmp_path: Path) -> None:
    calls: list[int] = []
    observer = FakeObserver()

    def factory() -> BaseObserver:
        calls.append(1)
        return observer

    watcher = LibraryWatcher(tmp_path, on_change=lambda _changes: None, observer_factory=factory)
    watcher.start()
    watcher.stop()
    watcher.start()

    assert len(calls) == 2
    assert observer.started == 2


@pytest.mark.parametrize("kind", [ChangeKind.ADDED, ChangeKind.REMOVED, ChangeKind.MODIFIED])
def test_each_kind_reaches_the_batch(tmp_path: Path, kind: ChangeKind) -> None:
    watcher, _observer, batches = make_watcher(tmp_path)
    watcher.start()
    path = tmp_path / "a.jpg"

    watcher._ingest(LibraryChange(kind, path))  # noqa: SLF001
    watcher.flush()

    assert batches == [(LibraryChange(kind, path),)]


def test_a_callback_factory_can_drive_the_watcher_without_watchdog(tmp_path: Path) -> None:
    """监听器的回调就是一个普通函数：测试与将来别的调用方都靠这一条驱动它。"""
    received: list[tuple[LibraryChange, ...]] = []
    batches_sink: Callable[[tuple[LibraryChange, ...]], None] = received.append

    watcher = LibraryWatcher(tmp_path, on_change=batches_sink)
    watcher._ingest(LibraryChange(ChangeKind.ADDED, tmp_path / "a.jpg"))  # noqa: SLF001
    watcher.flush()

    assert len(received) == 1


def test_an_event_for_the_library_root_itself_is_ignored(tmp_path: Path) -> None:
    """库根本身不是网格上的一条。macOS 上 FSEvents 一开始监听就会先报它一条。"""
    watcher, observer, batches = make_watcher(tmp_path)
    watcher.start()

    send(observer, DirCreatedEvent(str(tmp_path)))
    watcher.flush()

    assert batches == []


def test_reported_paths_are_written_the_way_the_library_writes_them(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """路径必须与扫描产出的写法一致，否则界面按路径索引的东西全都对不上。

    真实情形是 macOS 的 ``/var`` 链到 ``/private/var``：测试临时目录也一样
    （``tmp_path`` 未必是解析过的形式），这里正是那种情形。
    """
    library = tmp_path / "库"
    library.mkdir()
    watcher, observer, batches = make_watcher(library)
    watcher.start()

    send(observer, FileCreatedEvent(str(library / "a.jpg")))
    watcher.flush()

    assert batches == [(LibraryChange(ChangeKind.ADDED, library.resolve() / "a.jpg"),)]
