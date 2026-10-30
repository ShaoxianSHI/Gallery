"""放映：从当前层级开始、自动轮播、淡入淡出，以及那颗三角按钮。

**为什么单独一个文件**：这些行为横跨标题条、大图页与层级栈，而它们各自已经有一组
用例；这里只看「按了放映之后，照片是不是按网格里的顺序一张张过去」。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QAbstractAnimation, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    LibraryConfig,
    prepare_library,
    save_config,
)
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.viewer.detail import FADE_IN_MS, FADE_OUT_MS  # noqa: E402
from gallery.ui.window.slideshow_flow import AUTO_ADVANCE_MS  # noqa: E402
from gallery.ui.window.state.slideshow import SlideshowState  # noqa: E402
from gallery.ui.window.widgets.external_button import BUTTON_GAP  # noqa: E402
from gallery.ui.window.widgets.play_button import (  # noqa: E402
    PLAY_BUTTON_HEIGHT,
    PLAY_BUTTON_WIDTH,
    TRIANGLE_SCALE,
)

#: 这一层的照片（名字决定网格里的先后）。
PHOTOS = ("a.jpg", "b.jpg", "c.jpg")


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    """一个三张照片的库，停在「全部照片」的库根，元信息面板偏好是开着。"""
    root = tmp_path / "照片"
    root.mkdir()
    for name in PHOTOS:
        make_photo(name, directory=root)
    set_library_ui(root, browse_mode=BROWSE_MODE_FLAT, metadata_visible=True)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描")
    assert instance._model.rowCount() == len(PHOTOS)
    yield instance
    instance.close()


def _start(window: MainWindow) -> None:
    window.start_slideshow()
    assert window._slideshow.active is True


def _current_name(window: MainWindow) -> str:
    ref = window._model.photo_at(window._current_row)
    assert ref is not None
    return ref.name


class _RecordingMusic:
    """替掉真正的播放器：只记录放映流程让它做了什么。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def play(self, path: Path) -> None:
        self.calls.append(("play", path))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    def dispose(self) -> None:
        self.calls.append(("dispose",))


def _recording_music(window: MainWindow) -> _RecordingMusic:
    music = _RecordingMusic()
    window._slideshow_music = music  # type: ignore[assignment]
    return music


def _song(window: MainWindow) -> Path:
    song = window._library.root / "歌.mp3"
    song.write_bytes(b"\x00")
    window._library.set_slideshow_music(song)
    return song


# ---- 那颗按钮 ----------------------------------------------------------------


def test_the_play_button_sits_left_of_the_external_one(window: MainWindow) -> None:
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    assert window._play_button.isVisible() is True
    play = window._play_button.geometry()
    external = window._external_button.geometry()

    assert play.width() == PLAY_BUTTON_WIDTH
    assert play.height() == PLAY_BUTTON_HEIGHT
    assert play.left() + play.width() + BUTTON_GAP == external.left()


def test_the_play_button_draws_a_big_centered_triangle(window: MainWindow) -> None:
    """三角是自绘的：占够圆圈的高度，光心落在按钮中心附近。"""
    button = window._play_button
    polygon = button._play_polygon()
    body = button._body_rect()
    bounds = polygon.boundingRect()

    assert polygon.count() == 3
    assert bounds.height() >= min(button.width(), button.height()) * TRIANGLE_SCALE - 0.5
    assert abs(bounds.center().x() - body.center().x()) <= 1.0
    assert abs(bounds.center().y() - body.center().y()) <= 1.0


def test_the_button_is_away_on_the_calendar_page(window: MainWindow, qt_app: QApplication) -> None:
    """日历那一页没有「这一层照片网格」可放，按钮收起来。"""
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    window._play_button.setVisible(True)  # 先摆出来，确认是这一页把它收掉的
    window._sync_play_button()
    assert window._play_button.isVisible() is True

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    qt_app.processEvents()
    assert window._play_button.isVisible() is False


# ---- 从哪儿开始 --------------------------------------------------------------


def test_it_starts_from_the_first_photo_of_the_level(window: MainWindow) -> None:
    _start(window)

    assert window._stack.currentWidget() is window._detail_page
    assert _current_name(window) == PHOTOS[0]
    assert window._slideshow_timer.isActive() is True
    assert window._panel.isHidden() is True, "放映时不开元信息页"


def test_it_starts_from_the_photo_the_detail_page_is_showing(
    window: MainWindow, qt_app: QApplication
) -> None:
    row = window._model.row_of_path(window._library.root / PHOTOS[1])
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image)

    _start(window)

    assert _current_name(window) == PHOTOS[1]


def test_the_first_photo_does_not_fade_in_from_an_old_one(
    window: MainWindow, qt_app: QApplication
) -> None:
    """从网格开始放映时，首张不跟大图页上一次留下的画面淡入。"""
    row = window._model.row_of_path(window._library.root / PHOTOS[2])
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image)
    window.show_grid()
    assert window._detail.has_image is True, "大图页还留着上一次的画面"

    _start(window)

    assert window._detail._fade_layer is None
    assert _current_name(window) == PHOTOS[0]


def test_a_level_without_photos_says_so(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> None:
    """全是相册或全是分组时用横幅说明，不进入大图页。"""
    root = tmp_path / "只有相册"
    album = root / "子相册"
    album.mkdir(parents=True)
    make_photo("a.jpg", directory=album)
    prepare_library(root)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    window = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    window.show()
    wait_for_idle(qt_app, window, what="扫描")
    assert window._model.rowCount() == 1

    window.start_slideshow()

    assert window._banner.isVisible()
    assert window._banner.text() == "当前层级中没有照片。"
    assert window._slideshow.active is False
    assert window._stack.currentWidget() is not window._detail_page
    window.close()


# ---- 背景音乐（ADR 0109）-----------------------------------------------------


def test_a_slideshow_plays_the_libraries_song(window: MainWindow) -> None:
    song = _song(window)
    music = _recording_music(window)

    _start(window)

    assert music.calls == [("play", song)]


def test_without_a_song_nothing_is_played(window: MainWindow) -> None:
    music = _recording_music(window)

    _start(window)

    assert not [call for call in music.calls if call[0] == "play"]


def test_stopping_stops_the_song(window: MainWindow) -> None:
    _song(window)
    music = _recording_music(window)
    _start(window)
    music.calls.clear()

    window.stop_slideshow()

    assert music.calls == [("stop",)]


def test_pause_and_resume_follow_the_slideshow(window: MainWindow) -> None:
    _song(window)
    music = _recording_music(window)
    _start(window)
    music.calls.clear()

    window._play_button.click()
    assert music.calls == [("pause",)]

    window._play_button.click()
    assert music.calls == [("pause",), ("resume",)]


def test_a_new_slideshow_plays_the_song_again(window: MainWindow) -> None:
    song = _song(window)
    music = _recording_music(window)
    _start(window)
    window.stop_slideshow()

    _start(window)

    assert [call for call in music.calls if call[0] == "play"] == [("play", song), ("play", song)]


def test_a_missing_song_is_reported_and_the_slideshow_goes_on(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """找不到文件只让这一次没有声音，**放映本身不能停**。"""
    root = tmp_path / "照片"
    root.mkdir()
    for name in PHOTOS:
        make_photo(name, directory=root)
    set_library_ui(root, browse_mode=BROWSE_MODE_FLAT)
    save_config(root, LibraryConfig(slideshow_music="没有这首歌.mp3"))
    window = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    window.show()
    wait_for_idle(qt_app, window, what="扫描")
    try:
        window.start_slideshow()

        assert wait_until(qt_app, lambda: "未能播放背景音乐" in window._banner.text(), timeout=5)
        assert window._slideshow.active is True
        assert window._slideshow_timer.isActive() is True
    finally:
        window.close()


# ---- 怎么前进 ----------------------------------------------------------------


def test_auto_advance_moves_to_the_next_photo_in_grid_order(
    window: MainWindow,
) -> None:
    _start(window)
    assert window._slideshow_timer.interval() == AUTO_ADVANCE_MS

    window._advance_slideshow()

    assert _current_name(window) == PHOTOS[1]


def test_arrow_keys_step_and_wrap_around(window: MainWindow) -> None:
    _start(window)
    window._slideshow.index = len(PHOTOS) - 1

    window.step_detail(1)

    assert _current_name(window) == PHOTOS[0]


def test_a_step_fades_the_old_photo_out_before_the_new_one_appears(window: MainWindow) -> None:
    """先整张淡出，再整张淡入：旧图没退干净之前，新图一直是透明的。"""
    _start(window)

    window.step_detail(1)
    detail = window._detail

    assert detail._fade_layer is not None
    assert detail._item.opacity() == 0.0
    detail._fade_animation.setCurrentTime(FADE_OUT_MS // 2)
    assert detail._fade_layer is not None
    assert detail._fade_layer.opacity() > 0.0
    assert detail._item.opacity() == 0.0, "旧图尚未消失，新图不该提前出现"

    detail._fade_animation.setCurrentTime(FADE_OUT_MS)
    assert detail._fade_layer is None
    assert detail._fade_in_animation.state() == QAbstractAnimation.State.Running
    assert detail._item.opacity() == 0.0

    detail._fade_in_animation.setCurrentTime(FADE_IN_MS)
    assert detail._item.opacity() == 1.0
    assert _current_name(window) == PHOTOS[1]


def test_space_pauses_and_resumes(window: MainWindow, qt_app: QApplication) -> None:
    _start(window)
    window._detail.setFocus()
    qt_app.processEvents()

    QTest.keyClick(window._detail, Qt.Key.Key_Space)
    assert window._slideshow.paused is True
    assert window._slideshow_timer.isActive() is False

    QTest.keyClick(window._detail, Qt.Key.Key_Space)
    assert window._slideshow.paused is False
    assert window._slideshow_timer.isActive() is True


def test_the_button_pauses_and_resumes(window: MainWindow) -> None:
    _start(window)

    window._play_button.click()
    assert window._slideshow.paused is True

    window._play_button.click()
    assert window._slideshow.paused is False


def test_space_still_steps_when_not_playing(window: MainWindow) -> None:
    """没在放映时空格仍是「下一张」，与原来一样。"""
    row = window._model.row_of_path(window._library.root / PHOTOS[0])
    window._open_detail(window._model.index(row, 0))

    window._on_space_pressed()

    assert _current_name(window) == PHOTOS[1]


# ---- 怎么结束 ----------------------------------------------------------------


def test_escape_stops_and_returns_to_the_grid(window: MainWindow, qt_app: QApplication) -> None:
    _start(window)
    assert window._panel.isHidden() is True
    window._detail.setFocus()
    qt_app.processEvents()

    QTest.keyClick(window._detail, Qt.Key.Key_Escape)
    qt_app.processEvents()

    assert window._slideshow.active is False
    assert window._slideshow_timer.isActive() is False
    assert window._stack.currentWidget() is window._grid_page
    assert window._panel.isHidden() is False, "结束放映要把面板按偏好放回来"


def test_leaving_the_detail_page_stops_the_timer(window: MainWindow) -> None:
    _start(window)

    window.go_up()

    assert window._slideshow.active is False
    assert window._slideshow_timer.isActive() is False


# ---- 纯状态 ------------------------------------------------------------------


def test_the_snapshot_wraps_in_both_directions(tmp_path: Path) -> None:
    paths = tuple(tmp_path / name for name in PHOTOS)
    state = SlideshowState()
    state.start(paths, 0)

    assert state.step(-1) == paths[-1]
    assert state.step(1) == paths[0]
    assert state.current() == paths[0]


def test_stop_forgets_the_snapshot(tmp_path: Path) -> None:
    state = SlideshowState()
    state.start(tuple(tmp_path / name for name in PHOTOS), 1)

    state.stop()

    assert state.active is False
    assert state.current() is None
