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
