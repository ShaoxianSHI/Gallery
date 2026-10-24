"""视频大图页：在应用里播放（ADR 0091）。

`QMediaPlayer` + `QAudioOutput` + `QGraphicsVideoItem`（后者是 QGraphicsItem，于是与照片页
共用同一种容器）。播放器是异步的：每条回来的信号都要先问「还是这个视频吗」。
这是应用里第二个解码器 —— core 的 AVFoundation 只取缩略图，播放只有 Qt 能做。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QHideEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QWidget,
)

from gallery.core.library.photos import PhotoRef

#: 进度条那一层的边距与底色。半透明黑让它在亮画面上也读得清。
CONTROLS_MARGIN = 16
CONTROLS_STYLE = """
QWidget#videoControls {
    background: rgba(0, 0, 0, 0.55);
    border-radius: 10px;
}
QLabel#videoTime {
    color: rgba(255, 255, 255, 0.92);
    font-size: 12px;
}
QToolButton#videoPlay {
    color: white;
    font-size: 15px;
    border: none;
    padding: 0 4px;
}
QSlider::groove:horizontal {
    height: 3px;
    background: rgba(255, 255, 255, 0.32);
    border-radius: 1px;
}
QSlider::handle:horizontal {
    width: 11px;
    margin: -4px 0;
    border-radius: 5px;
    background: white;
}
"""

#: 放不了时那句角标的样式。与照片页的失败角标同一套颜色。
ERROR_STYLE = """
QLabel#videoError {
    background: rgba(150, 40, 40, 0.82);
    color: rgba(255, 255, 255, 0.94);
    border-radius: 9px;
    padding: 6px 12px;
    font-size: 13px;
}
"""


class VideoView(QGraphicsView):
    """播放一个视频：点画面或空格暂停 / 继续，底部进度条定位。"""

    #: Esc / ⏎：回网格（与照片页同一个语义）。
    request_close = Signal()
    #: ← / →：上一个 / 下一个（视频与照片共用同一层里的顺序）。
    request_step = Signal(int)
    #: 右键：视图只报位置，菜单由窗口拼（与照片页同一个流程）。
    context_requested = Signal(QPoint)
    #: ⌥ 点一下：打开它所属影片事件的网站（ADR 0063）。
    movie_open_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ref: PhotoRef | None = None
        self._poster_pixmap = QPixmap()
        self._at_end = False

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        # 首帧到达前盖着网格里那张缩略图：直接黑屏会让人以为点错了文件。
        self._poster = QGraphicsPixmapItem()
        self._poster.setZValue(1)
        self._poster.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._poster)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(QColor(0, 0, 0))
        self._scene.setBackgroundBrush(QColor(0, 0, 0))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_item)
        self._video_item.videoSink().videoFrameChanged.connect(self._on_frame)
        self._video_item.nativeSizeChanged.connect(self._on_native_size)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)

        self._build_controls()
        self._error = QLabel(self.viewport())
        self._error.setObjectName("videoError")
        self._error.setStyleSheet(ERROR_STYLE)
        self._error.setWordWrap(True)
        self._error.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._error.hide()

    # ---- 对外 --------------------------------------------------------------

    @property
    def current_ref(self) -> PhotoRef | None:
        return self._ref

    def play(self, ref: PhotoRef, poster: QImage | None = None) -> None:
        """打开并播放 ``ref``。``poster`` 是它的第一帧缩略图，首帧到了就撤掉。"""
        self.stop()
        self._ref = ref
        self._error.hide()
        self._controls.show()
        if poster is not None and not poster.isNull():
            self._poster_pixmap = QPixmap.fromImage(poster)
            self._poster.setPixmap(self._poster_pixmap)
            self._poster.show()
        self._player.setSource(QUrl.fromLocalFile(str(ref.path)))
        self._player.play()
        self._reposition_controls()

    def stop(self) -> None:
        """停下来并放开文件（切页、换片、切库、关窗都走这里）。"""
        self._ref = None
        self._at_end = False
        self._player.stop()
        self._player.setSource(QUrl())
        self._poster_pixmap = QPixmap()
        self._poster.setPixmap(QPixmap())
        self._poster.hide()
        self._scene.setSceneRect(QRectF())
        self._controls.hide()
        self._error.hide()
        self._slider.setRange(0, 0)
        self._time.setText("")
        self._set_play_icon(playing=False)
        self.resetTransform()

    def toggle_playback(self) -> None:
        if self._ref is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return
        if self._at_end:
            self._at_end = False
            self._player.setPosition(0)
        self._player.play()

    def show_error(self, text: str) -> None:
        """放不了时把原因摆出来（这一句由窗口或播放器给）。"""
        self._error.setText(text)
        self._error.adjustSize()
        self._error.show()
        self._reposition_error()

    # ---- 控件 --------------------------------------------------------------

    def _build_controls(self) -> None:
        self._controls = QWidget(self.viewport())
        self._controls.setObjectName("videoControls")
        self._controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._controls.setStyleSheet(CONTROLS_STYLE)
        layout = QHBoxLayout(self._controls)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(10)
        self._play_button = QToolButton(self._controls)
        self._play_button.setObjectName("videoPlay")
        self._play_button.setText("▶")
        self._play_button.setToolTip("播放 / 暂停（空格）")
        self._play_button.setAutoRaise(True)
        self._play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_button.clicked.connect(self.toggle_playback)
        self._slider = QSlider(Qt.Orientation.Horizontal, self._controls)
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.setRange(0, 0)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._time = QLabel("", self._controls)
        self._time.setObjectName("videoTime")
        layout.addWidget(self._play_button)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._time)
        self._controls.hide()

    def _reposition_controls(self) -> None:
        area = self.viewport().rect()
        width = max(240, area.width() - 2 * CONTROLS_MARGIN)
        height = self._controls.sizeHint().height()
        self._controls.setGeometry(
            area.left() + CONTROLS_MARGIN,
            area.bottom() - CONTROLS_MARGIN - height + 1,
            width,
            height,
        )

    def _reposition_error(self) -> None:
        area = self.viewport().rect()
        self._error.setMaximumWidth(max(200, area.width() - 2 * CONTROLS_MARGIN))
        self._error.adjustSize()
        x = area.right() - CONTROLS_MARGIN - self._error.width() + 1
        self._error.move(max(area.left() + CONTROLS_MARGIN, x), area.top() + CONTROLS_MARGIN)

    def _set_play_icon(self, *, playing: bool) -> None:
        self._play_button.setText("❚❚" if playing else "▶")

    # ---- 播放器信号 --------------------------------------------------------

    def _on_frame(self, frame: QVideoFrame) -> None:
        if self._ref is not None and frame.isValid():
            self._poster.hide()

    def _on_native_size(self, size: QSizeF) -> None:
        if self._ref is None or size.isEmpty():
            return
        self._video_item.setSize(size)
        self._scene.setSceneRect(QRectF(0.0, 0.0, size.width(), size.height()))
        if not self._poster_pixmap.isNull():
            self._poster.setScale(size.width() / self._poster_pixmap.width())
        self._fit()

    def _on_position(self, position: int) -> None:
        if self._ref is None:
            return
        if not self._slider.isSliderDown():
            self._slider.setValue(position)
        self._update_time(position)

    def _on_duration(self, duration: int) -> None:
        if self._ref is None:
            return
        self._slider.setRange(0, max(0, duration))
        self._update_time(self._player.position())

    def _on_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if self._ref is None:
            return
        self._set_play_icon(playing=state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._ref is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._at_end = True
            self._set_play_icon(playing=False)
            self._slider.setValue(self._slider.maximum())
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.show_error(self._unplayable_text("这个视频放不了"))

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if self._ref is None or error == QMediaPlayer.Error.NoError:
            return
        self.show_error(self._unplayable_text(message or "这个视频放不了"))

    def _unplayable_text(self, reason: str) -> str:
        return f"{reason}。⇧⏎ 可以用系统默认应用打开它。"

    def _update_time(self, position: int) -> None:
        self._time.setText(f"{_clock(position)} / {_clock(self._player.duration())}")

    def _on_slider_moved(self, position: int) -> None:
        self._update_time(position)

    def _on_slider_released(self) -> None:
        self._at_end = False
        self._player.setPosition(self._slider.value())

    # ---- 视图 --------------------------------------------------------------

    def _fit(self) -> None:
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """页面被换走（回网格、回日历、切库、关窗）就停播：解码线程与音频不该跟着走。"""
        self.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._fit()
        self._reposition_controls()
        self._reposition_error()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if event.button() == Qt.MouseButton.LeftButton and self._ref is not None:
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.movie_open_requested.emit()
            else:
                self.toggle_playback()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._ref is None:
            event.accept()
            return
        self.context_requested.emit(event.globalPos())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.request_close.emit()
            return
        if key == Qt.Key.Key_Left:
            self.request_step.emit(-1)
            return
        if key == Qt.Key.Key_Right:
            self.request_step.emit(1)
            return
        if key == Qt.Key.Key_Space:
            self.toggle_playback()
            return
        super().keyPressEvent(event)


def _clock(milliseconds: int) -> str:
    """毫秒 → ``m:ss``（超过一小时给 ``h:mm:ss``）。"""
    total = max(0, milliseconds) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


__all__ = ["CONTROLS_MARGIN", "CONTROLS_STYLE", "ERROR_STYLE", "VideoView"]
