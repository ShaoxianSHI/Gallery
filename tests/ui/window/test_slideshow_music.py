"""``gallery.ui.window.slideshow_music`` 的测试：真的能放、放完能重来、失败不抛。

素材是现场生成的 WAV，所以不依赖用户磁盘上的音乐，也不等一首歌的真实长度。
**没有音频输出设备时跳过** —— 解码路径与音量无关，但 `QAudioOutput` 需要一台设备。
"""

from __future__ import annotations

import os
import struct
import wave
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.ui.window.slideshow_music import SlideshowMusic, _clamp  # noqa: E402


def _require_audio() -> None:
    if not QMediaDevices.audioOutputs():
        pytest.skip("这台机器没有音频输出设备")


def _wav(path: Path, seconds: float = 1.0) -> Path:
    rate = 44_100
    frames = bytearray()
    for index in range(int(rate * seconds)):
        value = 6000 if (index // 50) % 2 == 0 else -6000
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(bytes(frames))
    return path


def _position(music: SlideshowMusic) -> int:
    assert music._player is not None
    return music._player.position()


def test_it_plays_and_starts_over_after_stop(qt_app: QApplication, tmp_path: Path) -> None:
    """**重新放映从头开始**：停过之后位置归零，而不是接着上一轮继续。"""
    _require_audio()
    song = _wav(tmp_path / "tone.wav")
    music = SlideshowMusic(volume=0)
    try:
        music.play(song)
        assert wait_until(qt_app, lambda: _position(music) > 0, timeout=10)

        music.stop()
        assert _position(music) == 0

        music.play(song)
        assert wait_until(qt_app, lambda: _position(music) > 0, timeout=10)
        assert _position(music) < 900, "这一次应当从 0 秒附近重新开始"
    finally:
        music.dispose()


def test_a_missing_file_reports_failure_without_raising(
    qt_app: QApplication, tmp_path: Path
) -> None:
    _require_audio()
    music = SlideshowMusic(volume=0)
    failed: list[bool] = []
    music.failed.connect(lambda: failed.append(True))
    missing = tmp_path / "没有这首歌.mp3"
    try:
        music.play(missing)

        assert wait_until(qt_app, lambda: failed, timeout=10)
        assert music.path == missing
        assert music.error, "Qt 的原始错误要留给日志"
    finally:
        music.dispose()


def test_the_song_loops(qt_app: QApplication) -> None:
    _require_audio()
    music = SlideshowMusic(volume=0)
    try:
        player = music._ensure()

        assert player.loops() == QMediaPlayer.Loops.Infinite
    finally:
        music.dispose()


def test_the_volume_is_clamped_to_the_valid_range() -> None:
    assert _clamp(140) == 100
    assert _clamp(-5) == 0
    assert _clamp(60) == 60
