"""窗口要哪几张缩略图：什么时候报、什么时候**不**报。

两件事是踩过的坑，都写在用例的名字里：

- 视图**还没重排**时不能报空窗口。``set_window`` 是整份替换，一次「空的」就会把
  刚排好的几十个请求全丢掉 —— 从日历点一格进网格时连着发生两次（换页、模型换掉）。
- 日历那一页不在前面时不必重新分桶：模型每次重排都会走到那一步，而正在看的可能是日历
  点出来的那一层网格，重算一遍是白做，还会把日历已经出好的图扔掉。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from waiting import wait_until, window_is_idle  # noqa: E402

from gallery.core.library.config import (
    BROWSE_MODE_CALENDAR,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata import batch  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.thumbs.pipeline import ThumbnailPipeline  # noqa: E402

DATES = {
    "三月一张.jpg": "2024:03:16 10:00:00",
    "四月一张.jpg": "2024:04:16 09:00:00",
    "四月两张.jpg": "2024:04:16 18:00:00",
}
NAMES = tuple(DATES)

#: 把假的 ``clear_cache`` 卡在 ``Event`` 上时最多等多久（秒）。**带上限是为了让
#: 「万一它同步跑在主线程上」变成一次失败而不是挂死。** 正常路径上主线程放行得很快。
RELEASE_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            raw: dict[str, object] = {}
            when = DATES.get(Path(path).name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: None
) -> MainWindow:
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    prepare_library(root)
    session_base = tmp_path / "会话"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_CALENDAR)
    win = MainWindow(session_base=session_base, initial_library=root)
    win.resize(900, 600)
    win.show()
    qt_app.processEvents()
    assert wait_until(qt_app, lambda: window_is_idle(win)), "扫描没有收工"
    qt_app.processEvents()
    return win


@pytest.fixture
def window_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int]]:
    """记下每次 ``set_window`` 报了「几张可见 + 几张预取 + 什么档位」。"""
    calls: list[tuple[int, int, int]] = []
    original = ThumbnailPipeline.set_window

    def record(
        self: ThumbnailPipeline,
        visible: Sequence[PhotoRef],
        prefetch: Sequence[PhotoRef] = (),
        *,
        target_edge: int = 0,
    ) -> None:
        calls.append((len(visible), len(tuple(prefetch)), target_edge))
        original(self, visible, prefetch, target_edge=target_edge)

    monkeypatch.setattr(ThumbnailPipeline, "set_window", record)
    return calls


def test_a_grid_that_has_not_laid_out_yet_keeps_the_requests_already_queued(
    qt_app: QApplication, window: MainWindow, window_calls: list[tuple[int, int, int]]
) -> None:
    """**模型里有行、可见行却是空的** —— 那是视图还没重排，不是「这一层没东西」。

    这时报空窗口等于把刚排好的几十个请求全丢掉（``set_window`` 是整份替换），
    剩下的只能等下一次 ``viewport_changed`` 才重新排上。
    """
    window.set_browse_mode("flat")
    assert wait_until(qt_app, lambda: window_is_idle(window))
    qt_app.processEvents()
    assert window._model.rowCount() == len(NAMES)

    window._grid.visible_rows = lambda **kwargs: []  # type: ignore[method-assign]
    window_calls.clear()
    window._refresh_thumbnail_window()
    assert window_calls == [], "视图尚未重排就报了空窗口，把排好的请求清掉了"

    # 真空了（空相册、换了空层）照旧要报 —— 那一批确实不需要了
    window._model.clear()
    window_calls.clear()
    window._refresh_thumbnail_window()
    assert window_calls == [(0, 0, window._model.icon_size.width())]


def test_the_calendar_is_not_re_bucketed_while_its_page_is_hidden(
    qt_app: QApplication, window: MainWindow
) -> None:
    """日历那一页不在前面时不重新分桶（模型每次重排都会走到那一步）。

    重算一遍除了白做，还会 ``set_photos`` → 清空日历那份缩略图仓库 ——
    正看着日历点出来的那一层网格，回头却发现日历要重出图。
    """
    page = window._calendar_page
    window._feed_calendar()
    buckets_before = dict(page.current_view()._by_day)
    # 前置条件：桶里真有东西。两边都空的字典也相等，那样这条就是在断「还是空」。
    assert buckets_before, "日历那一页压根没分桶，下面那句比较断不了任何东西"

    # 摆一个别的页在前面，重排一次：不该动日历
    window._stack.setCurrentWidget(window._grid_page)
    window._apply_ordering()
    qt_app.processEvents()
    assert page.current_view()._by_day == buckets_before


def test_the_calendar_reports_again_after_its_pictures_are_dropped(
    qt_app: QApplication, window: MainWindow, window_calls: list[tuple[int, int, int]]
) -> None:
    """**月视图点开一格再退回来，那一片的缩略图还在。**

    退回来时会重扫整库，扫完重新分桶 → ``set_photos`` → 手上那批缩略图全扔掉。
    而「这一批要哪几张」没变，于是「没变就不重报」那次判断把窗口跳过了 —— 界面停在空白上。
    """
    import datetime

    page = window._calendar_page
    page.set_today(datetime.date(2024, 4, 16))
    page.go_today()
    page.set_level("month")
    qt_app.processEvents()
    view = page.current_view()
    photos = list(view.photos_of(datetime.date(2024, 4, 16)))
    assert len(photos) == 2

    # 先让这一层把窗口报出去（页面上现在「已经报过了」）。
    # **前置条件得摆出来**：这一批图解码回来没有取决于后台线程的快慢，拿它去赌就是掷硬币 ——
    # 而「要的都在手上」正是 ``_refresh_window`` **该**跳过的那一种。所以自己把手上的清空。
    page._thumbs.clear()
    window_calls.clear()
    page._refresh_window()
    assert any(visible > 0 for visible, _p, _e in window_calls), window_calls

    # 退回来：重扫 → 重新分桶 → 手上那批图被清空，要的那一批没变
    page._thumbs.clear()
    page.set_photos(
        [r for r in window._model.entries() if isinstance(r, PhotoRef)],
        window._library.cached_facets(  # type: ignore[union-attr]
            [r for r in window._model.entries() if isinstance(r, PhotoRef)]
        ),
    )
    assert page.current_view().photo_count() > 0

    window_calls.clear()
    page._refresh_window()
    assert any(visible > 0 for visible, _p, _e in window_calls), (
        "手上那批缩略图已经清空了，却因为「要的那一批没变」而不重报 —— 界面停在空白上"
    )


def test_an_unchanged_window_with_pictures_in_hand_is_not_reported_again(
    qt_app: QApplication, window: MainWindow, window_calls: list[tuple[int, int, int]]
) -> None:
    """反过来那条：**没变且手上都有**就别重报（队列是整份替换，重排一遍谁都得往后排）。"""
    page = window._calendar_page
    page.set_level("week")
    qt_app.processEvents()
    view = page.current_view()
    for ref in view.visible_refs():
        image = QImage(8, 6, QImage.Format.Format_RGBA8888)
        image.fill(0x336699)
        page.set_thumbnail(ref.path, image, view.thumbnail_edge())

    window_calls.clear()
    page._refresh_window()
    assert window_calls == [], window_calls


def test_opening_a_photo_from_the_calendar_fills_the_grid_window(
    qt_app: QApplication, window: MainWindow, window_calls: list[tuple[int, int, int]]
) -> None:
    """从日历点一格进网格：要报的窗口是**那一格**的，不是底下整库的。

    换页那一刻模型还没换掉，量到的就会是整库的可见行 —— 多要一批，紧接着又被下一次重排的
    空窗口清掉。
    """
    import datetime

    page = window._calendar_page
    page.set_today(datetime.date(2024, 4, 16))
    page.go_today()
    page.set_level("month")
    qt_app.processEvents()
    view = page.current_view()
    photos = list(view.photos_of(datetime.date(2024, 4, 16)))
    assert len(photos) == 2

    window_calls.clear()
    view.select_cell(photos, "4月16日")
    qt_app.processEvents()
    assert window._model.rowCount() == 2
    assert any(visible > 0 for visible, _prefetch, _edge in window_calls), window_calls


def test_clearing_the_thumbnail_cache_runs_off_the_main_thread(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**删除整个缓存目录里的文件不许跑在主线程上。**

    它是一次 O(n) 的 unlink 循环（两万条目约 0.9 秒，按默认那 2 GiB 的上限外推是 2～3 秒），
    而它正好紧跟在模态对话框之后 —— 对话框关掉的那一瞬间窗口整个冻住。

    这里不测「快不快」（那要造两万个文件），只测**它在别的线程上**。

    「调用返回时删除还没做完」这条**不能靠「它还没开始」来证**：提交之后工作线程随时可能
    抢在断言之前跑起来，于是那条断言在快机器上会随机变红。改成一个**不可能误判**的形状：
    把被调的函数卡在一个 ``Event`` 上，``finished`` 只在它**放行之后**才置位 ——
    于是「调用返回时 ``finished`` 是空的」与线程调度无关地成立，
    而它证明的东西还更强一层：**调用方没有等它做完**。

    卡住时带超时，是为了「万一它真的同步跑在主线程上」变成一次**失败**而不是挂死。
    """
    library = window._library
    assert library is not None
    assert wait_until(qt_app, lambda: window_is_idle(window)), "后台任务没收工"
    # **横幅只有一个槽**：扫描 / 地点解析 / 建索引的进度随时会往里写。所以要断言它，
    # 先等进行中的活结束、再把槽收干净（共享状态要先摆前置条件再断言）。
    window._clear_banner()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )

    ran_on: list[str] = []
    release = threading.Event()
    finished = threading.Event()

    original_clear_cache = library.clear_cache

    def record() -> int:
        ran_on.append(threading.current_thread().name)
        release.wait(RELEASE_TIMEOUT_SECONDS)  # 主线程放行之前一直卡在这儿
        result = original_clear_cache()
        finished.set()
        return result

    monkeypatch.setattr(library, "clear_cache", record)
    main_thread = threading.current_thread().name

    window.clear_thumbnail_cache()

    assert not finished.is_set(), "clear_thumbnail_cache 等到了删除做完 —— 那就还在主线程上"
    assert window._cache_job is not None, "没有投递后台任务"

    release.set()
    assert wait_until(qt_app, lambda: window._cache_job is None and window_is_idle(window)), (
        "清空任务没收工"
    )
    qt_app.processEvents()

    assert ran_on and ran_on[0] != main_thread, f"仍在主线程上删：{ran_on}"
    assert "已清除" in window._banner.text()
