"""放映背景音乐：把一次放映配的那首歌交给 Qt 播放（ADR 0109）。

``QMediaPlayer`` 只在这里出现，放映流程只问「放 / 停 / 暂停 / 继续」。播放器**懒建**：
没配音乐的人不该在开窗时多一个解码器。失败只发一条信号，具体文案由窗口拼。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

logger = logging.getLogger(__name__)


class SlideshowMusic(QObject):
    """一首歌的播放器：循环播放，**重新放映从开头开始**。"""

    failed = Signal()

    def __init__(self, parent: QObject | None = None, *, volume: int = 60) -> None:
        super().__init__(parent)
        self._player: QMediaPlayer | None = None
        self._output: QAudioOutput | None = None
        self._volume = _clamp(volume)
        self._path: Path | None = None
        self._error = ""

    @property
    def path(self) -> Path | None:
        """正在放（或刚放过）的那首歌；窗口据此知道该报哪一个文件名。"""
        return self._path

    @property
    def error(self) -> str:
        """Qt 给出的最后一条错误原文；只进日志，不显示给用户。"""
        return self._error

    def play(self, path: Path) -> None:
        """从开头播放 ``path``；同一首歌再放一次也从开头来。"""
        player = self._ensure()
        self._path = Path(path)
        self._error = ""
        player.setSource(QUrl.fromLocalFile(str(self._path)))
        player.setPosition(0)
        player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._path = None

    def pause(self) -> None:
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

    def resume(self) -> None:
        """暂停中才继续；失败停下来的那一次不重试（否则错误横幅会一直刷）。"""
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()

    def set_volume(self, percent: int) -> None:
        """音量按百分比算；0 是静音，但歌照常往前走。"""
        self._volume = _clamp(percent)
        if self._output is not None:
            self._output.setVolume(self._volume / 100)

    def dispose(self) -> None:
        """关窗时调用：先停声音，再让 Qt 回收两个对象。"""
        self.stop()
        if self._player is not None:
            self._player.deleteLater()
            self._player = None
        if self._output is not None:
            self._output.deleteLater()
            self._output = None

    def _ensure(self) -> QMediaPlayer:
        if self._player is None:
            self._output = QAudioOutput(self)
            self._output.setVolume(self._volume / 100)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._output)
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
            self._player.errorOccurred.connect(self._on_error)
        return self._player

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self._error = message
        logger.warning("放映背景音乐未能播放：%s（%s）", self._path, message)
        self.failed.emit()


def _clamp(percent: int) -> int:
    return max(0, min(100, int(percent)))


__all__ = ["SlideshowMusic"]
