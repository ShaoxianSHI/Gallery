"""翻页不闪：**相邻那一周的图先解着**，按 → / ← 时手上已经有了。

不预取的话，每一段新日子都是「切过去才开始解」—— 第一次看总有一下占位灰（真机上
一张原图解几百毫秒），而**切走再切回来就不灰**了（图已经在手上）。这一条量的是
「切过去的那一刻手上有没有图」，不留任何等待时间。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until, window_is_idle  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_CALENDAR,
    SessionState,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 连续三周、每天 08:00 与 20:00 各一张 —— 翻页时前后都有人。
WEEK_ONE = datetime.date(2024, 4, 7)  # 周日
DATES = {
    f"{offset:02d}-{hour}.jpg": (
        (WEEK_ONE + datetime.timedelta(days=offset)).strftime("%Y:%m:%d") + f" {hour:02d}:00:00"
    )
    for offset in range(21)
    for hour in (8, 20)
}
NAMES = tuple(DATES)
ANCHOR = datetime.date(2024, 4, 10)


@pytest.fixture
def fake_places(monkeypatch: pytest.MonkeyPatch) -> None:
    def place_for(
        self: object, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace:
        return ResolvedPlace(city="西安", region="陕西省", country="中国")

    monkeypatch.setattr(LazyPlaceResolver, "place_for", place_for)
    monkeypatch.setattr(LazyPlaceResolver, "ready", property(lambda _self: True))


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
def calendar_window(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> MainWindow:
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    prepare_library(root)
    session_base = tmp_path / "会话"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_CALENDAR), base=session_base
    )
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    page = window._calendar_page
    page.set_today(ANCHOR)
    page.go_today()
    page.set_level("week")
    assert wait_until(
        qt_app, lambda: window_is_idle(window) and page.photo_count() > 0, timeout=60.0
    ), "夹具没就绪"
    assert wait_until(qt_app, lambda: not page.current_view()._reveal.isActive())
    return window


def test_the_neighbouring_week_is_already_decoded_when_stepping(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """停在某一周时，**前后一周先解着**；按 → 过去不该还要现解。"""
    window = calendar_window
    page = window._calendar_page
    view = page.current_view()
    assert wait_until(
        qt_app,
        lambda: all(view.photo(ref) is not None for ref in view.visible_refs()),
        timeout=15.0,
    ), "当前这一周还没出图"

    prefetch = list(view.prefetch_refs())
    assert prefetch, "相邻一周没有要预取的（前提不成立）"
    assert wait_until(
        qt_app,
        lambda: all(view.photo(ref) is not None for ref in view.prefetch_refs()),
        timeout=15.0,
    ), "相邻一周的图没有被预取回来"

    page.step(1)  # 翻到下一周：**不留等待时间**
    nxt = page.current_view()
    missing = [ref.name for ref in nxt.visible_refs() if nxt.photo(ref) is None]
    assert not missing, f"翻过去还要现解这几张（会闪一下）：{missing}"
