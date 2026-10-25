"""放映当前层级：复用大图页，按序轮播并淡入淡出。# guard: allow-long-comment（顺序、面板、退出）

**顺序就是网格里那一刻的顺序**：从模型里按行取出所有照片，放映途中只认路径与那一份
快照，不自己重排。**元信息面板在放映期间收起**（用户要的是照片本身），结束之后按库级
偏好放回来。**Esc 结束放映并回到当前层级的网格** —— 那是用户在大图页上一贯的退出方向
（ADR 0041）。

自动播放用 ``QTimer``，手动按 ← / → 会重新计时；空格暂停 / 继续。放映途中换层、切库、
离开大图页都会收掉这一次放映。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer

from gallery.core.library.photos import PhotoRef
from gallery.ui.window.state.slideshow import SlideshowState

#: 自动播放的间隔（毫秒）。手动翻页后会重新计这一段时间。
AUTO_ADVANCE_MS = 3000


class SlideshowFlowMixin:
    """开始 / 暂停 / 结束放映，以及把「现在放到第几张」换成大图页上的那一张。"""

    _slideshow: SlideshowState
    _slideshow_timer: QTimer

    def _init_slideshow(self) -> None:
        """建放映的状态与计时器；``MainWindow.__init__`` 里只留这一句。"""
        self._slideshow = SlideshowState()
        self._slideshow_timer = QTimer(self)
        # 间隔是应用级偏好（ADR 0073）；常数只是它从没被改过时的默认值。
        seconds = self._session.preferences.resolved_slideshow_seconds()
        self._slideshow_timer.setInterval(seconds * 1000)
        self._slideshow_timer.timeout.connect(self._advance_slideshow)

    # ---- 入口 --------------------------------------------------------------

    def _on_play_clicked(self) -> None:
        """标题条上那颗按钮：没在放映就开始，在放映就暂停 / 继续。"""
        if self._slideshow.active:
            self._toggle_slideshow_pause()
        else:
            self.start_slideshow()

    def start_slideshow(self) -> None:
        """从当前层级的第一张开始放映；**大图页上从正在看的那一张开始**。

        这一层没有照片（例如全是相册或全是分组）时用横幅说明，不进入大图页。
        """
        if self._library is None or self._on_calendar_page():
            return
        # 视频不进放映：它自己有多长，与固定秒数是两套节奏。
        photos = [
            entry
            for entry in self._model.entries()
            if isinstance(entry, PhotoRef) and not entry.is_video
        ]
        if not photos:
            self._show_banner("当前层级中没有照片。")
            return
        paths = tuple(photo.path for photo in photos)
        current = self._model.photo_at(self._current_row) if self._in_detail_page() else None
        # 视频不进放映：从视频页按播放键要从这一层第一张照片重新开始。
        in_detail_photo = current is not None and not current.is_video
        index = _index_of(paths, current.path) if in_detail_photo else 0
        # **先收起元信息页再打开大图**：`_load_detail` 看面板可见性决定读不读元数据。
        self._panel.hide()
        # 从网格起步时**先把大图打开、再进放映**：那一刻 `_load_detail` 还看不到放映状态，
        # 首张就不会从大图页上一次留下的旧画面淡入（淡入淡出只发生在放映中的两张之间）。
        if not in_detail_photo:
            row = self._model.row_of_path(paths[index])
            if row < 0:
                # 这一瞬间模型变了：把面板按偏好放回去，别留下一个被收起来的空布局。
                self._panel.setVisible(self._metadata_visible)
                return
            self._open_detail(self._model.index(row, 0))
        self._slideshow.start(paths, index)
        self._restart_slideshow_timer()
        self._update_play_button()

    def stop_slideshow(self) -> None:
        """结束放映。**元信息面板按库级偏好放回来**，不改变用户原来的设置。"""
        if not self._slideshow.active:
            return
        self._slideshow.stop()
        self._slideshow_timer.stop()
        self._panel.setVisible(self._metadata_visible)
        self._update_play_button()

    # ---- 暂停 / 继续 -------------------------------------------------------

    def _toggle_slideshow_pause(self) -> None:
        if not self._slideshow.active:
            return
        self._slideshow.paused = not self._slideshow.paused
        self._restart_slideshow_timer()
        self._update_play_button()

    def _on_space_pressed(self) -> None:
        """空格：放映中暂停 / 继续，其余时候与右方向键一样翻下一张。"""
        if self._slideshow.active:
            self._toggle_slideshow_pause()
        else:
            self.step_detail(1)

    # ---- 逐张前进 ----------------------------------------------------------

    def _advance_slideshow(self) -> None:
        if not self._slideshow.active or self._slideshow.paused:
            return
        self._show_slideshow_delta(1)

    def _show_slideshow_delta(self, delta: int) -> None:
        """切到快照里的上一张 / 下一张，**这一次切换带淡入淡出**。"""
        if len(self._slideshow.paths) <= 1:
            return
        path = self._slideshow.step(delta)
        if path is None:
            return
        row = self._model.row_of_path(path)
        if row < 0:
            # 那张照片在放映途中被移走了：这一批快照已经作废，别再拿行号去指别的东西。
            self.stop_slideshow()
            return
        self._clear_banner()
        self._focus_state.set_current(row)
        self._load_detail(row, fade=True)
        self._restart_slideshow_timer()

    def _restart_slideshow_timer(self) -> None:
        """重新开始计这三秒；暂停中或只有一张时保持停着。"""
        if self._slideshow.active and not self._slideshow.paused and len(self._slideshow.paths) > 1:
            self._slideshow_timer.start()
        else:
            self._slideshow_timer.stop()

    # ---- 标题条上那颗按钮 --------------------------------------------------

    def _sync_play_button(self) -> None:
        """摆不摆、以及此刻是三角还是暂停 —— 两样都只在这一句里决定。"""
        visible = self._owns_title_bar and self._library is not None
        self._play_button.setVisible(visible and not self._on_calendar_page())
        self._update_play_button()

    def _update_play_button(self) -> None:
        self._play_button.set_running(self._slideshow.active, paused=self._slideshow.paused)


def _index_of(paths: tuple[Path, ...], target: Path) -> int:
    """这张照片在快照里排第几；找不到就从头放。"""
    try:
        return paths.index(target)
    except ValueError:
        return 0


__all__ = ["AUTO_ADVANCE_MS", "SlideshowFlowMixin"]
