"""``gallery.ui.viewer.video`` 的测试：时间格式、键盘、播放器生命周期（ADR 0091）。

素材是现场生成的 AVI/MJPEG（``make_video``），所以「真的出帧了」与「坏文件说得出话」
都能在离屏平台上验，不需要 ffmpeg，也不等真实时长。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.viewer.video import VideoView, _clock  # noqa: E402


@pytest.mark.parametrize(
    ("milliseconds", "text"),
    [
        (0, "0:00"),
        (999, "0:00"),
        (1000, "0:01"),
        (65_000, "1:05"),
        (3_661_000, "1:01:01"),
    ],
)
def test_clock_formats_minutes_and_hours(milliseconds: int, text: str) -> None:
    assert _clock(milliseconds) == text


def _view(qt_app: QApplication) -> VideoView:
    view = VideoView()
    view.resize(640, 480)
    view.show()
    return view


def _poster(size: tuple[int, int] = (64, 48)) -> QImage:
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkRed)
    return image


def test_playing_replaces_the_poster_with_the_first_frame(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    path = make_video(frames=3, directory=tmp_path)
    ref = PhotoRef.from_path(path)
    view = _view(qt_app)
    try:
        view.play(ref, _poster())

        assert view.current_ref == ref
        assert view._poster.isVisible(), "首帧到达前要先盖着缩略图"
        assert wait_until(qt_app, lambda: not view._poster.isVisible(), timeout=5)
        assert wait_until(qt_app, lambda: view._player.duration() == 120, timeout=5)
    finally:
        view.stop()
        view.close()

    assert view.current_ref is None


def test_the_first_frame_comes_back_when_playback_ends(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """播完不能留下空画面：Qt 在 ``EndOfMedia`` 之后不再保留视频帧。"""
    path = make_video(frames=5, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path), _poster())
        assert wait_until(
            qt_app,
            lambda: view._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            timeout=5,
        )

        assert view._poster.isVisible()
        assert not view._poster.pixmap().isNull()
        assert view._slider.value() == view._slider.maximum()
    finally:
        view.stop()
        view.close()


def test_the_first_frame_is_kept_even_without_a_poster(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """网格里没有缩略图时，播放器自己解出来的第一帧也要留住。"""
    path = make_video(frames=5, colors=[(200, 40, 40), *[(40, 200, 40)] * 4], directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))
        assert wait_until(
            qt_app,
            lambda: view._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            timeout=5,
        )

        pixmap = view._poster.pixmap()
        assert view._poster.isVisible()
        assert not pixmap.isNull()
        color = pixmap.toImage().pixelColor(pixmap.width() // 2, pixmap.height() // 2)
        assert color.red() > color.green() and color.red() > color.blue(), (
            f"播完显示的不是第一帧：{color.name()}"
        )
    finally:
        view.stop()
        view.close()


def test_replaying_after_the_end_hides_the_first_frame(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    path = make_video(frames=5, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path), _poster())
        assert wait_until(
            qt_app,
            lambda: view._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            timeout=5,
        )
        assert view._poster.isVisible()

        view.toggle_playback()

        assert wait_until(qt_app, lambda: not view._poster.isVisible(), timeout=5)
    finally:
        view.stop()
        view.close()


def test_seeking_after_the_end_hides_the_first_frame(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    path = make_video(frames=5, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path), _poster())
        assert wait_until(
            qt_app,
            lambda: view._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            timeout=5,
        )
        assert view._poster.isVisible()

        view._slider.setValue(max(0, view._slider.maximum() // 2))
        view._on_slider_released()

        assert not view._poster.isVisible()
    finally:
        view.stop()
        view.close()


def test_space_pauses_and_resumes(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    path = make_video(frames=100, directory=tmp_path)  # 4 秒，够按两下
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))
        assert wait_until(
            qt_app,
            lambda: view._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            timeout=5,
        )

        view.keyPressEvent(_key(Qt.Key.Key_Space))
        assert view._player.playbackState() == QMediaPlayer.PlaybackState.PausedState

        view.keyPressEvent(_key(Qt.Key.Key_Space))
        assert wait_until(
            qt_app,
            lambda: view._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            timeout=5,
        )
    finally:
        view.stop()
        view.close()


def test_escape_and_arrows_are_reported(qt_app: QApplication) -> None:
    view = _view(qt_app)
    closed: list[bool] = []
    steps: list[int] = []
    view.request_close.connect(lambda: closed.append(True))
    view.request_step.connect(steps.append)
    try:
        view.keyPressEvent(_key(Qt.Key.Key_Escape))
        view.keyPressEvent(_key(Qt.Key.Key_Left))
        view.keyPressEvent(_key(Qt.Key.Key_Right))
    finally:
        view.stop()
        view.close()

    assert closed == [True]
    assert steps == [-1, 1]


def test_an_unplayable_file_says_how_to_open_it(qt_app: QApplication, tmp_path: Path) -> None:
    path = tmp_path / "坏掉的.mp4"
    path.write_bytes(b"not a video")
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))

        assert wait_until(qt_app, view._error.isVisible, timeout=5)
        assert "⇧⏎" in view._error.text()
    finally:
        view.stop()
        view.close()


def test_stop_releases_the_source_and_the_controls(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    path = make_video(frames=100, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))
        assert view._controls.isVisible()

        view.stop()

        assert view.current_ref is None
        assert view._controls.isHidden()
        assert not view._poster.isVisible()
    finally:
        view.close()


def _key(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def test_the_progress_bar_does_not_move_between_play_and_pause(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """▶ 与 ❚❚ 的字形宽度不同：按钮不定宽时，进度条左端会随播放状态左右跳。"""
    path = make_video(frames=100, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))
        assert wait_until(
            qt_app,
            lambda: view._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            timeout=5,
        )
        playing = view._slider.geometry()

        view.toggle_playback()
        paused = view._slider.geometry()

        assert paused == playing
        assert view._play_button.width() == 26
    finally:
        view.stop()
        view.close()


def test_reopening_the_same_video_fits_again(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """同一个尺寸的视频再打开时 ``nativeSizeChanged`` 不会再发；场景矩形与适配要自己补。"""
    path = make_video(frames=100, size=(160, 120), directory=tmp_path)
    ref = PhotoRef.from_path(path)
    view = _view(qt_app)
    try:
        view.play(ref)
        assert wait_until(qt_app, lambda: view.transform().m11() > 1.5, timeout=5)
        first = view.transform().m11()

        view.stop()
        assert view.transform().m11() == pytest.approx(1.0)

        view.play(ref)
        assert wait_until(qt_app, lambda: view.transform().m11() > 1.5, timeout=5)
        assert view.transform().m11() == pytest.approx(first)
    finally:
        view.stop()
        view.close()


def test_the_controls_sit_inside_the_viewport_after_showing(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """第一次打开时布局还没稳定下来：控件要补算一次，不能停在视口外面。"""
    path = make_video(frames=100, directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path))
        assert wait_until(
            qt_app,
            lambda: view._controls.geometry().bottom() <= view.viewport().rect().bottom(),
            timeout=5,
        )
        assert view._controls.geometry().left() >= 0
    finally:
        view.stop()
        view.close()


def test_the_poster_stays_out_of_the_scene(
    qt_app: QApplication, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    """海报挂在视口上：场景里只有视频那一格，缩略图的宽高比不该把场景撑大。

    海报留在场景里时，它的缩放矩形（缩略图取整后与视频差千分之几）会被算进
    ``itemsBoundingRect``，重开时画面就偏了。
    """
    path = make_video(frames=100, size=(160, 256), directory=tmp_path)
    view = _view(qt_app)
    try:
        view.play(PhotoRef.from_path(path), _poster(size=(30, 48)))

        assert view._poster.isVisible()
        assert len(view._scene.items()) == 1

        assert wait_until(qt_app, lambda: not view._poster.isVisible(), timeout=5)
        rect = view._scene.itemsBoundingRect()
        assert (round(rect.width()), round(rect.height())) == (160, 256)
        assert view._video_item.boundingRect().height() == pytest.approx(256)
    finally:
        view.stop()
        view.close()
