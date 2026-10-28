"""视频大图页：在应用里播放（ADR 0091）。

`QMediaPlayer` + `QAudioOutput` + `QGraphicsVideoItem`（后者是 QGraphicsItem，于是与照片页
共用同一种容器）。播放器是异步的：每条回来的信号都要先问「还是这个视频吗」。
这是应用里第二个解码器 —— core 的 AVFoundation 只取缩略图，播放只有 Qt 能做。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QContextMenuEvent,
    QHideEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QWidget,
)

from gallery.core.i18n import t
from gallery.core.library.photos import PhotoRef
from gallery.ui import theme

#: ``QGraphicsView.fitInView`` 会在视口四边各留 2 像素。海报要按同一个矩形缩放，
#: 首帧换上时才不会缩一下。
_FIT_MARGIN = 2

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
        #: 这个源的原生尺寸已经落到 item 上了吗。**不能用 sceneRect 判**：清空之后它会
        #: 退成 itemsBoundingRect（被海报撑大过），重开时就会以为尺寸已经设好。
        self._size_applied = False

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        # 首帧到达前盖着网格缩略图。**挂在视口上而不是场景里**：场景里的 item（哪怕隐藏）
        # 会被算进 itemsBoundingRect，缩略图的宽高比会把场景撑大、把画面挤偏。
        self._poster = QLabel(self.viewport())
        self._poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._poster.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._poster.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._poster.hide()

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 视口不铺底色，视频四周露出的就是窗口画的那一块（与照片页一致，ADR 0096）。
        theme.mark_see_through(self)
        theme.mark_see_through(self.viewport())
        self._apply_theme()
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
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
        # 布局要等这一轮事件走完才算好：首帧与 show() 都可能跑在它前面，那时算出来的
        # 适配比例与控件位置都是按旧几何算的。排一个 0ms 的补算，见 :meth:`_relayout`。
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(0)
        self._layout_timer.timeout.connect(self._relayout)
        self._error = QLabel(self.viewport())
        self._error.setObjectName("videoError")
        self._error.setStyleSheet(ERROR_STYLE)
        self._error.setWordWrap(True)
        self._error.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._error.hide()

    def _apply_theme(self) -> None:
        """视频四周不铺底色（ADR 0096）：硬编码深色会让浅色主题下点开视频突然变一块黑。

        场景那一层也得清掉 —— 它自己不铺，露出的才是窗口那一块。
        """
        self.setBackgroundBrush(QBrush())
        self._scene.setBackgroundBrush(QBrush())

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
        # 新源的第一帧到之前先不画旧的最后一帧：先上海报（没有海报就露出窗口底色）。
        self._video_item.setVisible(False)
        if poster is not None and not poster.isNull():
            self._poster_pixmap = QPixmap.fromImage(poster)
            self._update_poster()
            self._poster.show()
        self._player.setSource(QUrl.fromLocalFile(str(ref.path)))
        self._player.play()
        self._schedule_layout()

    def stop(self) -> None:
        """停下来并放开文件（切页、换片、切库、关窗都走这里）。"""
        self._ref = None
        self._at_end = False
        self._size_applied = False
        self._player.stop()
        self._player.setSource(QUrl())
        self._poster_pixmap = QPixmap()
        self._poster.clear()
        self._poster.hide()
        self._video_item.setVisible(False)
        self._scene.setSceneRect(QRectF())
        self._controls.hide()
        self._error.hide()
        self._slider.setRange(0, 0)
        self._time.setText("")
        self._set_play_icon(playing=False)
        self.resetTransform()

    def pause(self) -> None:
        """暂停（交给外部播放器时用）；**不放开文件**，回来还能接着放。"""
        if self._ref is not None:
            self._player.pause()

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
        self._play_button.setToolTip(t("viewer.video.play_tooltip"))
        self._play_button.setAutoRaise(True)
        self._play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # ▶ 与 ❚❚ 的字形宽度不同：按钮不定宽的话，进度条左端会随播放状态左右跳。
        self._play_button.setFixedSize(26, 24)
        self._play_button.clicked.connect(self.toggle_playback)
        self._slider = QSlider(Qt.Orientation.Horizontal, self._controls)
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.setRange(0, 0)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._time = QLabel("", self._controls)
        self._time.setObjectName("videoTime")
        # 同理：时长从「0:00」变成「1:01:01」时，不定宽会把进度条右端推来推去。
        self._time.setFixedWidth(120)
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._play_button)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._time)
        # 高度也定住：sizeHint 会随文案变，进度条那一层的上下位置就不该跟着动。
        self._controls.setFixedHeight(36)
        self._controls.hide()

    def _reposition_controls(self) -> None:
        area = self.viewport().rect()
        width = max(240, area.width() - 2 * CONTROLS_MARGIN)
        height = self._controls.height()
        self._controls.setGeometry(
            area.left() + CONTROLS_MARGIN,
            area.bottom() - CONTROLS_MARGIN - height + 1,
            width,
            height,
        )

    def _update_poster(self) -> None:
        """把海报按视口大小等比缩放并居中（只在首帧到达前可见）。"""
        if self._poster_pixmap.isNull() or self.viewport().width() <= 0:
            return
        area = self.viewport().rect().adjusted(_FIT_MARGIN, _FIT_MARGIN, -_FIT_MARGIN, -_FIT_MARGIN)
        self._poster.setGeometry(self.viewport().rect())
        self._poster.setPixmap(
            self._poster_pixmap.scaled(
                area.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
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
        if self._ref is None or not frame.isValid():
            return
        self._video_item.setVisible(True)
        if self._poster.isVisible():
            self._poster.hide()
            self._poster_pixmap = QPixmap()
            self._poster.clear()
        self._ensure_size()

    def _on_native_size(self, size: QSizeF) -> None:
        self._apply_native_size(size)

    def _apply_native_size(self, size: QSizeF) -> None:
        if self._ref is None or size.isEmpty():
            return
        self._size_applied = True
        self._video_item.setSize(size)
        self._scene.setSceneRect(QRectF(0.0, 0.0, size.width(), size.height()))
        self._schedule_layout()

    def _ensure_size(self) -> None:
        """换回同一个尺寸的视频时 ``nativeSizeChanged`` 不会再发：从当前源自己取一次。

        不补这一下，``stop()`` 清掉场景矩形之后重新打开一个同尺寸的视频，画面就停在
        1:1 的左上角（实测重开第二次必定这样）。
        """
        if self._ref is None or self._size_applied:
            return
        size = self._video_item.nativeSize()
        if not size.isEmpty():
            self._apply_native_size(size)

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
        elif status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self._ensure_size()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.show_error(self._unplayable_text(t("viewer.video.unplayable")))

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if self._ref is None or error == QMediaPlayer.Error.NoError:
            return
        self.show_error(self._unplayable_text(message or t("viewer.video.unplayable")))

    def _unplayable_text(self, reason: str) -> str:
        return t("viewer.video.unplayable.hint", reason=reason)

    def _update_time(self, position: int) -> None:
        self._time.setText(f"{_clock(position)} / {_clock(self._player.duration())}")

    def _on_slider_moved(self, position: int) -> None:
        self._update_time(position)

    def _on_slider_released(self) -> None:
        self._at_end = False
        self._player.setPosition(self._slider.value())

    # ---- 视图 --------------------------------------------------------------

    def _fit(self) -> None:
        """按**视频自己那一格**适配，不看场景矩形 —— 海报等其它 item 不该影响它。"""
        if self._ref is None:
            return
        rect = self._video_item.boundingRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _relayout(self) -> None:
        """按此刻的视口重算适配比例与控件位置。**迟到的布局也算一次**，见 ``_schedule_layout``。"""
        self._fit()
        self._reposition_controls()
        self._reposition_error()

    def _schedule_layout(self) -> None:
        """现在算一次，这一轮事件循环结束再算一次（布局请求通常排在我们后面）。"""
        self._relayout()
        self._layout_timer.start()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().showEvent(event)
        self._schedule_layout()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """页面被换走（回网格、回日历、切库、关窗）就停播：解码线程与音频不该跟着走。"""
        self.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._relayout()
        self._update_poster()

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
