"""临时探针：第一次切到某一周时，图是「要晚了」还是「解慢了」。"""

from __future__ import annotations

import datetime
import os
import time
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


def _dates() -> dict[str, str]:
    out: dict[str, str] = {}
    day = datetime.date(2024, 4, 7)
    for offset in range(21):
        stamp = (day + datetime.timedelta(days=offset)).strftime("%Y:%m:%d")
        out[f"{offset:02d}.jpg"] = f"{stamp} 08:00:00"
    return out


DATES = _dates()
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
def window(
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
    win = MainWindow(session_base=session_base, initial_library=root)
    win.resize(1000, 700)
    win.show()
    qt_app.processEvents()
    page = win._calendar_page
    page.set_today(ANCHOR)
    page.go_today()
    page.set_level("week")
    assert wait_until(
        qt_app, lambda: window_is_idle(win) and page.photo_count() > 0, timeout=60.0
    ), "夹具没就绪"
    assert wait_until(qt_app, lambda: not page.current_view()._reveal.isActive())
    return win


def test_probe_cold_week_latency(qt_app: QApplication, window: MainWindow) -> None:
    page = window._calendar_page
    view = page.current_view()
    assert wait_until(
        qt_app,
        lambda: all(view.photo(r) is not None for r in view.visible_refs()),
        timeout=10.0,
    ), "第一周都没出图"

    # 让下一周变成「冷」的：内存里没有它的图
    page.step(1)
    qt_app.processEvents()
    assert wait_until(qt_app, lambda: not view._reveal.isActive())
    for ref in view.visible_refs():
        page._thumbs.forget(ref.path)
    qt_app.processEvents()
    print("\n下一周的图已从内存里拿掉：",
          [view.photo(r) is None for r in view.visible_refs()])

    # 回到上一周（热的），再切过来 —— 模拟「切走再切回来」
    page.step(-1)
    qt_app.processEvents()
    assert wait_until(qt_app, lambda: not view._reveal.isActive())
    for _ in range(20):
        qt_app.processEvents()

    marks: list[tuple[str, float]] = []
    began = time.monotonic()

    def stamp(what: str) -> None:
        marks.append((what, (time.monotonic() - began) * 1000))

    pipeline = window._pipeline
    real_set_window = pipeline.set_window

    def spy_set_window(visible, prefetch=(), *, target_edge=0):  # noqa: ANN001, ANN202
        if page.level() == "week" and visible:
            stamp(f"要图 {len(visible)} 张 edge={target_edge} 预取={len(prefetch)}")
        return real_set_window(visible, prefetch, target_edge=target_edge)

    pipeline.set_window = spy_set_window
    real_ready = window._on_thumbnail_ready

    def spy_ready(path, image, edge):  # noqa: ANN001, ANN202
        stamp(f"交回 {Path(path).name} edge={edge}")
        return real_ready(path, image, edge)

    pipeline.ready.disconnect(real_ready)
    pipeline.ready.connect(spy_ready)

    # 冷的那一周再切回来
    page.step(1)
    qt_app.processEvents()
    stamp("切过来（step 返回）")
    ok = wait_until(
        qt_app,
        lambda: all(view.photo(r) is not None for r in view.visible_refs()),
        timeout=10.0,
    )
    stamp("这一屏全有图" if ok else "10 秒也没全")
    print("时间线（毫秒，从 step 开始算）：")
    for what, when in marks:
        print(f"   {when:8.1f}  {what}")
