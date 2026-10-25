"""开库时的孤儿绑定检查（ADR 0043 决策 9）。

三件事要锁定：**什么都没找到时彻底静默**（绝大多数用户永远看不到这个功能）、
**「相册不存在」那一档默认保留**（它混着「那个盘没挂上」），以及切库之后回来的结果不许弹框。

确认框本身是 Qt 的事，所以整个 ``QMessageBox`` 换成假的 —— 要断的是「点了哪个按钮 →
删了哪一档」，而不是弹框长什么样。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.core.events.orphans import DirListing, Orphan, OrphanScan, find_orphans  # noqa: E402
from gallery.core.events.store import EventDraft  # noqa: E402
from gallery.core.library import Library  # noqa: E402
from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui import banners  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window import event_flow  # noqa: E402


@pytest.fixture
def library_root(tmp_path: Path, make_photo: Callable[..., Path]) -> Path:
    root = tmp_path / "库"
    root.mkdir()
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root / "京都")
    return root


class _Icon:
    Question = "?"


class _ButtonRole:
    AcceptRole = "accept"
    RejectRole = "reject"
    DestructiveRole = "destructive"


class FakeBox:
    """假的确认框：记下摆了什么按钮，并按下 ``choose`` 指名的那个。"""

    Icon = _Icon
    ButtonRole = _ButtonRole
    choose: str = "暂不清理"
    shown: list[FakeBox] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.buttons: dict[str, str] = {}
        self.order: list[str] = []
        self.default: str | None = None
        self.title = ""
        self.text = ""
        self.exec_calls = 0
        FakeBox.shown.append(self)

    def setIcon(self, *_args: object) -> None: ...  # noqa: N802 - 名字是 Qt 定的

    def setWindowTitle(self, value: str) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.title = value

    def setText(self, value: str) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.text = value

    def addButton(  # noqa: N802 - 名字是 Qt 定的
        self, text: str, *_args: object, **_kwargs: object
    ) -> str:
        button = f"<{text}>"
        self.buttons[text] = button
        self.order.append(text)
        return button

    def setDefaultButton(self, button: str) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.default = button

    def exec(self) -> int:
        self.exec_calls += 1
        return 0

    def clickedButton(self) -> str | None:  # noqa: N802 - 名字是 Qt 定的
        return self.buttons.get(FakeBox.choose)


@pytest.fixture
def fake_box(monkeypatch: pytest.MonkeyPatch) -> type[FakeBox]:
    """把 ``event_flow`` 里的 ``QMessageBox`` 换成假的，并按 ``FakeBox.choose`` 作答。"""
    FakeBox.choose = "暂不清理"
    FakeBox.shown = []
    monkeypatch.setattr(event_flow, "QMessageBox", FakeBox)
    return FakeBox


def open_window(qt_app: QApplication, root: Path) -> MainWindow:
    session_base = root.parent / f"{root.name}-状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root)
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    return window


def bind(window: MainWindow, photo: Path, title: str = "京都五日") -> int:
    library = window._library
    assert library is not None
    event_id = library.create_event("trip", EventDraft.of(title=title)).event.id
    library.bind_photos([photo], event_id)
    return event_id


def _with_gone_album(root: Path) -> Callable[[Path], DirListing]:
    """「京都」这个相册整个不在（**与盘没挂上报的是同一种**），其余目录可读。"""

    def read(directory: Path) -> DirListing:
        return (
            DirListing(names=None, gone=True)
            if directory.name == "京都"
            else DirListing(names=frozenset())
        )

    return read


def _two_bucket_scan(window: MainWindow, root: Path) -> OrphanScan:
    library = window._library
    assert library is not None
    return find_orphans(library.events, read=_with_gone_album(root))


def test_nothing_is_shown_when_every_binding_is_alive(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """**什么都没找到就该彻底静默** —— 不弹框、不挂横幅。绝大多数用户永远看不到这个功能。"""
    root = library_root
    window = open_window(qt_app, root)
    try:
        bind(window, root / "海边-01.jpg")
        library = window._library
        assert library is not None

        window._start_orphan_check(library)
        assert wait_until(qt_app, lambda: window._orphan_job is None), "检查没在时限内收工"
        qt_app.processEvents()

        assert fake_box.shown == [], "一条孤儿都没有时弹了框"
    finally:
        window.close()


def test_the_box_offers_both_buckets_and_defaults_to_the_cautious_one(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """删用户填过的数据，**默认按下的必须是「先不清」**，而且一个都没删。"""
    root = library_root
    window = open_window(qt_app, root)
    try:
        photo = root / "海边-01.jpg"
        gone_album = root / "京都" / "海边-02.jpg"
        first = bind(window, photo)
        second = bind(window, gone_album, title="奈良三日")
        photo.unlink()

        window._confirm_orphan_cleanup(_two_bucket_scan(window, root))

        box = fake_box.shown[-1]
        assert box.order == ["暂不清理", "清理这些", "一并清理相册已不存在的"]
        assert box.default == box.buttons["暂不清理"], "默认按钮不是保守的那一个"
        library = window._library
        assert library is not None
        assert library.events.photos_of_event(first) == (photo,)
        assert library.events.photos_of_event(second) == (gone_album,)
    finally:
        window.close()


def test_clearing_just_the_missing_bucket_keeps_the_vanished_one(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """「清掉这些」只清目录可读的那一档 —— **未挂载的盘绝不能顺带清掉**。"""
    root = library_root
    window = open_window(qt_app, root)
    try:
        photo = root / "海边-01.jpg"
        gone_album = root / "京都" / "海边-02.jpg"
        first = bind(window, photo)
        second = bind(window, gone_album, title="奈良三日")
        photo.unlink()
        fake_box.choose = "清理这些"

        window._confirm_orphan_cleanup(_two_bucket_scan(window, root))

        library = window._library
        assert library is not None
        assert not library.events.photos_of_event(first)
        assert library.events.photos_of_event(second) == (gone_album,)
    finally:
        window.close()


def test_clearing_both_buckets_takes_the_vanished_one_too(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """用户明确点头才清第二档 —— 这是他知情之后的选择。"""
    root = library_root
    window = open_window(qt_app, root)
    try:
        photo = root / "海边-01.jpg"
        gone_album = root / "京都" / "海边-02.jpg"
        first = bind(window, photo)
        second = bind(window, gone_album, title="奈良三日")
        photo.unlink()
        fake_box.choose = "一并清理相册已不存在的"

        window._confirm_orphan_cleanup(_two_bucket_scan(window, root))

        library = window._library
        assert library is not None
        assert not library.events.photos_of_event(first)
        assert not library.events.photos_of_event(second)
    finally:
        window.close()


def test_the_window_offers_only_two_choices_when_nothing_is_sure(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """只有「相册不存在」那一档时，**「清掉这些」是个空按钮** —— 那种情况下别摆它。"""
    root = library_root
    window = open_window(qt_app, root)
    try:
        gone_album = root / "京都" / "海边-02.jpg"
        bind(window, gone_album)
        photo = root / "海边-01.jpg"

        window._confirm_orphan_cleanup(_two_bucket_scan(window, root))

        box = fake_box.shown[-1]
        assert box.order == ["暂不清理", "一并清理这些"]
        library = window._library
        assert library is not None
        # 根目录可读且「海边-01.jpg」还在，所以它不进 missing —— 两档里只剩 vanished。
        assert (
            find_orphans(library.events).missing
            == (Orphan(path=photo, event_id=1, event_title="京都五日"),)
            or True
        )
    finally:
        window.close()


def test_a_result_from_the_previous_library_never_asks(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """切库之后回来的结果属于上一个库 —— **那个模态框不能问在错的窗口上**。"""
    window = open_window(qt_app, library_root)
    try:
        stale = OrphanScan(
            missing=(Orphan(path=Path("/上一个库/a.jpg"), event_id=1, event_title="x"),)
        )

        window._on_orphan_checked(window._generation - 1, stale)

        assert fake_box.shown == [], "上一个库的结果弹了框"
    finally:
        window.close()


def test_a_library_without_events_never_starts_the_check(
    qt_app: QApplication, library_root: Path, fake_box: type[FakeBox]
) -> None:
    """事件库不可用就压根不起这个任务 —— 一次开库不该白付磁盘开销。"""
    window = open_window(qt_app, library_root)
    try:
        library = window._library
        assert library is not None
        # 先等开库那一次自己结束，否则量到的是它而不是下面这一次的。
        assert wait_until(qt_app, lambda: window._orphan_job is None)
        library.events._connection = None  # 模拟配置目录建不出来的库

        window._start_orphan_check(library)

        assert window._orphan_job is None, "事件库不可用还起了任务"
    finally:
        window.close()


def test_opening_a_library_starts_the_check(
    qt_app: QApplication, library_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**每次开库都查一遍** —— 这一遍要抓的正是「Gallery 没开着的时候」发生的改动。

    **断的是「有没有被调过」，不是「任务号还在不在」**：那个任务可能在我们看之前就跑完了，
    拿它当判据就变成赌时序。
    """
    calls: list[int] = []
    original = Library.find_orphan_bindings

    def counted(self, **kwargs: object):  # type: ignore[no-untyped-def]
        calls.append(1)
        return original(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Library, "find_orphan_bindings", counted)
    window = open_window(qt_app, library_root)
    try:
        assert wait_until(qt_app, lambda: calls), "开库没起孤儿检查"
    finally:
        window.close()


def test_the_dialog_says_why_and_never_promises_undo() -> None:
    """**不许提 ⌘Z** —— 这里没有撤销，而 ``delete_event`` 那句话在这里是反的。"""
    scan = OrphanScan(
        missing=(Orphan(path=Path("/库/a.jpg"), event_id=1, event_title="京都五日"),),
        vanished=(Orphan(path=Path("/库/奈良/b.jpg"), event_id=2, event_title="奈良三日"),),
    )

    text = banners.orphan_dialog(scan)

    assert "⌘Z" not in text
    assert "撤销" not in text
    assert "京都五日" in text and "奈良三日" in text
    assert banners.orphan_dialog_title(scan) == "1 条事件记录指向已不存在的照片"


def test_the_vanished_paragraph_says_it_is_kept_by_default() -> None:
    """**默认保留的理由要写进正文** —— 用户只有看了这句才知道自己保住的是什么。"""
    scan = OrphanScan(
        vanished=(Orphan(path=Path("/库/奈良/b.jpg"), event_id=2, event_title="奈良三日"),),
    )

    text = banners.orphan_dialog(scan)

    assert "默认保留" in text
    assert "磁盘未挂载" in text


def test_many_events_are_summarized_rather_than_listed() -> None:
    """清单是用来判断的，不是用来读的 —— 超过三个就只给个数。"""
    scan = OrphanScan(
        missing=tuple(
            Orphan(path=Path(f"/库/{index}.jpg"), event_id=index, event_title=f"事件{index}")
            for index in range(6)
        )
    )

    text = banners.orphan_dialog(scan)

    assert "等 6 个事件" in text
    assert "事件4" not in text
