"""视频在界面上的接线（ADR 0090 / 0091）：双击进播放页、翻图能翻到、放映跳过它。

**不真的去开应用**：``desktop.open_with_default_app`` 被换成记录调用的假实现。
播放器本身不在这里验（见 ``tests/ui/viewer/test_video.py``），这一份只盯「哪条路认识视频」。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.external import apps  # noqa: E402
from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.core.metadata.report import (  # noqa: E402
    MetadataField,
    MetadataGroup,
    MetadataReport,
)
from gallery.ui.main_window import MainWindow  # noqa: E402


def _photo(root: Path, name: str) -> Path:
    path = root / name
    image = Image.new("RGB", (16, 12), (40, 120, 200))
    try:
        image.save(path)
    finally:
        image.close()
    return path


@pytest.fixture
def root(tmp_path: Path, make_video: Callable[..., Path]) -> Path:
    library = tmp_path / "库"
    library.mkdir(parents=True)
    _photo(library, "a.jpg")
    make_video("b.avi", frames=100, directory=library)
    _photo(library, "c.jpg")
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描")
    yield instance
    instance.close()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []

    def fake_open(path: Path) -> str | None:
        calls.append(Path(path))
        return None

    monkeypatch.setattr("gallery.ui.desktop.open_with_default_app", fake_open)
    return calls


def _row(window: MainWindow, name: str) -> int:
    row = window._model.row_of_path(window._library.root / name)  # type: ignore[union-attr]
    assert row >= 0, f"网格里没有 {name}"
    return row


def _open(window: MainWindow, name: str) -> None:
    window._on_entry_activated(window._model.index(_row(window, name), 0))


def _on_video_page(window: MainWindow) -> bool:
    return window._video.isVisible() and window._video.current_ref is not None


def test_double_clicking_a_video_opens_the_player(window: MainWindow) -> None:
    _open(window, "b.avi")

    assert window._in_detail_page() is True
    assert _on_video_page(window)
    current = window._video.current_ref
    assert current is not None
    assert current.path.name == "b.avi"


def test_double_clicking_a_photo_still_opens_the_photo_view(window: MainWindow) -> None:
    _open(window, "a.jpg")

    assert window._in_detail_page() is True
    assert not _on_video_page(window)


def test_the_open_command_takes_the_same_route(window: MainWindow) -> None:
    window._grid.select_row(_row(window, "b.avi"))

    window.open_current_entry()

    assert _on_video_page(window)


def test_a_video_has_no_metadata_editor(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频没有可写的 EXIF。把 exiftool 装上的情形也摆出来，判据只该是「它是视频」。"""
    library = window._library
    assert library is not None
    monkeypatch.setattr(library, "can_write_metadata", lambda: True)
    window._grid.select_row(_row(window, "b.avi"))

    assert window.metadata_edit_candidate() is None

    window._grid.select_row(_row(window, "a.jpg"))
    assert window.metadata_edit_candidate() is not None


def test_step_detail_walks_into_videos_too(window: MainWindow) -> None:
    """← / → 走的是这一层的顺序：视频也有页，不该被跳过。"""
    _open(window, "a.jpg")

    window.step_detail(1)

    current = window._model.photo_at(window._current_row)
    assert current is not None
    assert current.path.name == "b.avi"
    assert _on_video_page(window)


def test_the_slideshow_leaves_videos_out(window: MainWindow) -> None:
    window.start_slideshow()

    assert [path.name for path in window._slideshow.paths] == ["a.jpg", "c.jpg"]


def test_a_calendar_cell_with_one_video_opens_the_video_page(window: MainWindow) -> None:
    library = window._library
    assert library is not None
    video = next(ref for ref in library.iter_photos() if ref.path.name == "b.avi")

    window._on_calendar_cell_requested([video], "一格")

    assert window._in_detail_page() is True
    assert _on_video_page(window)


def test_escape_from_a_video_page_stops_the_player(window: MainWindow) -> None:
    _open(window, "b.avi")

    window._video.request_close.emit()

    assert window._in_detail_page() is False
    assert window._video.current_ref is None


def test_open_in_default_app_still_works_on_a_video_page(
    window: MainWindow, opened: list[Path]
) -> None:
    _open(window, "b.avi")
    window._grid.select_row(_row(window, "b.avi"))

    window.open_current_in_default_app()

    assert opened == [window._library.root / "b.avi"]  # type: ignore[union-attr]


def test_zoom_actions_have_nothing_to_do_on_a_video_page(window: MainWindow) -> None:
    _open(window, "b.avi")
    window._update_action_states()
    assert window._fit_action.isEnabled() is False

    _open(window, "a.jpg")
    window._update_action_states()
    assert window._fit_action.isEnabled() is True


def test_image_viewers_are_not_offered_for_videos(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频不归「预览 / ImageGlass」：它们打不开它，摆出来只会让人以为软件坏了。"""
    monkeypatch.setattr(window, "_installed", lambda app: True)
    _open(window, "b.avi")
    assert window._viewer_ready(apps.PREVIEW) is False

    _open(window, "a.jpg")
    assert window._viewer_ready(apps.PREVIEW) is True


def test_the_panel_keeps_the_edit_button_off_for_videos(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频能看元数据，但写不了：面板那个按钮对视频必须一直灰着。"""
    library = window._library
    assert library is not None
    monkeypatch.setattr(library, "can_write_metadata", lambda: True)
    window.toggle_metadata(True)  # 面板默认是收着的；这条盯的是它在视频页上的状态

    _open(window, "b.avi")
    assert window._panel.metadata_editable is False

    _open(window, "a.jpg")
    assert window._panel.metadata_editable is True


def test_the_panel_shows_a_video_report(
    window: MainWindow, qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频页也要读报告：exiftool 的 QuickTime 标签在报告层已经映射成中文分组。"""
    report = MetadataReport(
        path=Path("片子.mp4"),
        groups=(MetadataGroup("视频", (MetadataField("时长", "0:01:30"),)),),
        all_fields=(),
        source="exiftool",
    )
    monkeypatch.setattr(
        "gallery.ui.window.metadata_flow.read_metadata",
        lambda path, *, allow_fallback=True: report,
    )
    window.toggle_metadata(True)

    _open(window, "b.avi")

    assert wait_until(
        qt_app,
        lambda: (
            window._panel.report is not None
            and any(group.title == "视频" for group in window._panel.report.groups)
        ),
    )
