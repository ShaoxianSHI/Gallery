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
from gallery.core.library.session import (  # noqa: E402
    EXTERNAL_PAGE_VIDEO,
    SessionState,
    save_session,
)
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


def test_the_photo_filmstrip_is_hidden_on_the_video_page(
    window: MainWindow, qt_app: QApplication
) -> None:
    """画廊条是照片页的（ADR 0091 决策 3）：视频页上它跟着照片视图一起收起来。"""
    _open(window, "a.jpg")
    strip = window._detail._filmstrip
    assert strip.isVisible() is True

    _open(window, "b.avi")
    qt_app.processEvents()

    assert window._detail.isHidden() is True
    assert strip.isVisible() is False


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


def _menu_texts(window: MainWindow) -> list[str]:
    return [action.text() for action in window._external_button._menu.actions()]


def test_iina_is_first_when_it_is_the_only_player(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有 IINA 时视频页第一项是它，照片页上没有它 —— 用户存下来的旧次序里没有它（ADR 0093）。"""
    iina_app = Path("/Applications/IINA.app")
    monkeypatch.setattr(apps, "find_app", lambda app: iina_app if app.id == apps.IINA.id else None)

    _open(window, "b.avi")
    window._update_action_states()

    assert _menu_texts(window)[0] == "IINA"
    default = window._external_button.default_entry()
    assert default is not None and default.id == apps.IINA.id

    calls: list[tuple[list[Path], Path, str]] = []
    monkeypatch.setattr(
        "gallery.ui.desktop.open_with_app",
        lambda paths, *, app, name: calls.append((list(paths), app, name)) or "已在 IINA 中打开。",
    )
    window._open_in_iina()
    assert calls == [([window._library.root / "b.avi"], iina_app, "IINA")]  # type: ignore[union-attr]

    _open(window, "a.jpg")
    window._update_action_states()
    assert "IINA" not in _menu_texts(window)


def test_the_system_player_comes_first_on_a_video_page(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QuickTime Player 与 IINA 都装了时，系统播放器在前（ADR 0095）。

    交出去之前要先暂停应用里的播放器，否则两个播放器会同时出声（ADR 0093）。
    """
    quicktime = Path("/System/Applications/QuickTime Player.app")
    installed = {apps.QUICKTIME.id: quicktime, apps.IINA.id: Path("/Applications/IINA.app")}
    monkeypatch.setattr(apps, "find_app", lambda app: installed.get(app.id))

    _open(window, "b.avi")
    window._update_action_states()

    assert _menu_texts(window)[:2] == ["QuickTime Player", "IINA"]
    default = window._external_button.default_entry()
    assert default is not None and default.id == apps.QUICKTIME.id

    calls: list[tuple[list[Path], Path, str]] = []
    monkeypatch.setattr(
        "gallery.ui.desktop.open_with_app",
        lambda paths, *, app, name: calls.append((list(paths), app, name)) or "已在其中打开。",
    )
    paused: list[bool] = []
    monkeypatch.setattr(window._video, "pause", lambda: paused.append(True))
    window._open_in_quicktime()

    assert calls == [([window._library.root / "b.avi"], quicktime, "QuickTime Player")]  # type: ignore[union-attr]
    assert paused == [True]

    _open(window, "a.jpg")
    window._update_action_states()
    assert "QuickTime Player" not in _menu_texts(window)


def test_the_video_page_keeps_its_own_order_apart_from_the_photo_page(
    window: MainWindow,
) -> None:
    """图片与视频各一份次序（ADR 0094）—— 排视频页不该顺带改掉照片页的默认。"""
    prefs = window._session.preferences.with_external_order(
        EXTERNAL_PAGE_VIDEO, ("quicktime", "iina", "finder")
    )
    window._session = window._session.with_preferences(prefs)

    _open(window, "b.avi")
    window._update_action_states()
    assert window._external_order_ids() == ("quicktime", "iina", "finder")

    _open(window, "a.jpg")
    window._update_action_states()
    assert window._external_order_ids() == ()


def test_a_saved_video_order_without_the_system_player_gets_it_prepended(
    window: MainWindow,
) -> None:
    """存下来的次序是 ADR 0093 之前写的、里面没有 QuickTime：补在最前，不排到最后（ADR 0095）。"""
    prefs = window._session.preferences.with_external_order(EXTERNAL_PAGE_VIDEO, ("finder", "iina"))
    window._session = window._session.with_preferences(prefs)

    _open(window, "b.avi")

    assert window._external_order_ids() == ("quicktime", "finder", "iina")


def test_the_two_maps_are_offered_on_a_video_page(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频有坐标时与照片一样摆「地图」与「Google 地图」——`_map_point` 不看文件类型。"""
    monkeypatch.setattr(window, "_map_point", lambda: (31.2304, 121.4737))
    monkeypatch.setattr(window, "_installed", lambda app: True)

    _open(window, "b.avi")
    window._update_action_states()

    texts = _menu_texts(window)
    assert "地图" in texts and "Google 地图" in texts
