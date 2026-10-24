"""大图页（ADR 0021）：一张一张地看，以及为了「翻过去不白屏」而做的那点预读。

取舍几乎全在**延迟**上：先命中缓存给一张缩略图顶着，再起后台解码预览图，再换全分辨率的那一张 ——
每一步都让用户马上看到点什么，而**没有一步是在主线程上解码的**。``_preview_cache`` 与
``_metadata_cache`` 各记一份账，文件一改键就自然对不上；预读元数据**顺带把地点也推断好**，
否则切到那张时面板要先显示一遍没有地点的版本再被替换。
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtGui import QImage

from gallery.core.library.config import BROWSE_MODE_FLAT
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.exif import read_metadata
from gallery.core.metadata.report import MetadataReport
from gallery.ui.imaging import to_qimage
from gallery.ui.tasks import TaskContext

logger = logging.getLogger(__name__)

#: 大图预览在内存里保留几张（当前张前后各一张，切图才不会白屏）。
PREVIEW_CACHE_SIZE = 3

#: 元数据缓存条数。翻图时命中缓存就直接显示，没有中间态。
METADATA_CACHE_SIZE = 128

#: 预读前后各几张的元数据。一次读取约 50ms，预读了就等于切图时零等待。
METADATA_PREFETCH_RADIUS = 2

#: 预览图相对视口长边的倍率：留出余量，缩放一点也不会糊。
PREVIEW_EDGE_FACTOR = 1.5

#: 预览图长边的上下限（像素）。
MIN_PREVIEW_EDGE = 512
MAX_PREVIEW_EDGE = 4096


class ViewerFlowMixin:
    """大图页的显示、翻图，以及预览图与元数据的两份缓存。"""

    # ---- 大图（见 ADR 0021）
    def show_grid(self) -> None:
        """回到网格，并让当前照片保持选中。

        关大图、删掉最后一张之后退回这两条路走这里；``Esc``（菜单里那一条「上一级」）走
        :meth:`MainWindow.go_up`。两条路在栈顶投影出同一件事（弹掉照片帧），所以退到网格
        还是日历由照片帧底下压着的那一层决定。
        """
        self.stop_slideshow()
        if self._on_calendar_page():
            # 日历与网格**同一时刻只有一个**：从日历按 Esc 出来就是把浏览方式换回「全部照片」，
            # 否则菜单里那个勾就成了假话，``Y`` / ``M`` / ``W`` 也没处可去。
            self.set_browse_mode(BROWSE_MODE_FLAT)
            return
        if self._photo_frame is not None:
            self.go_up()
            return
        self.leave_detail_page()

    def leave_detail_page(self) -> None:
        """**照片帧已经不在栈上了**：把页面收成网格，并把大图页暂存的东西一次性收拾掉。

        与 :meth:`show_grid` 分开是因为「弹栈」是统一的，而收尾是「刚离开大图页」才做的
        事 —— 合成一处就得判「这一次是不是从大图页来的」。**横幅那一句在
        :meth:`~gallery.ui.window.navigation_flow.NavigationFlowMixin._leave_frame` 里收**
        （那是「刚弹掉一张照片」的现场）：走到这里栈上已经不是照片帧，按栈判永远为假。"""
        self._title_bar.stop_edit()  # 切页时把改到一半的名字收起来（ADR 0025）
        self.stop_slideshow()
        self._leave_video()
        self._sync_page_to_stack()  # 照片帧弹掉之后，这一条栈表示的就是网格页
        if self._current_row >= 0:
            self._grid.select_row(self._current_row)
        # 在大图页时暂存的外部变化：这里才是网格能改的地方（ADR 0013）。
        self._flush_pending_watch()
        self._sync_ui()
        self._refresh_thumbnail_window()

    def step_detail(self, delta: int) -> None:
        """上一张 / 下一张。相册模式下会跳过相册格子。"""
        if self._slideshow.active:
            self._show_slideshow_delta(delta)
            return
        row = self._neighbour_photo_row(self._current_row, delta)
        if row is None:
            return
        # **换一张照片就把横幅收掉**（见 :meth:`_clear_banner`）：那句「已把它移到废纸篓」
        # 说的是刚才那一张。
        self._clear_banner()
        self._focus_state.set_current(row)
        self._load_detail(row)

    def step_photo_from_panel(self, delta: int) -> None:
        """元数据面板里按了 ← / → —— 换上一张 / 下一张。

        **走大图页自己那条路**（``step_detail``，相册模式下会跳过相册格子）。面板只摆在大图页上
        （``_build_ui`` 里它加的是 ``detail_layout``），所以不必再判断当前在哪一页。
        **焦点不转移**：用户还停在面板里，接着按 ← / → 能继续翻。
        """
        self.step_detail(delta)

    def _neighbour_photo_row(self, start: int, delta: int) -> int | None:
        row = start + delta
        while 0 <= row < self._model.rowCount():
            if self._model.photo_at(row) is not None:
                return row
            row += delta
        return None

    def _load_detail(self, row: int, *, fade: bool = False) -> None:
        library = self._library
        ref = self._model.photo_at(row)
        if library is None or ref is None:
            return
        # 放映途中任何一次换照片都该淡入淡出，不管是谁发起的（自动、← / →、监听器换页）。
        fade = fade or self._slideshow.active
        # guard: allow-long-comment（五个调用点都靠这一句换页，少一句就是「画面换了而页面没换」）
        # 标题栏那一行从此是这张照片的文件名（ADR 0025 决策 4）：翻图、打开、
        # 删掉一张之后接着看，走的都是这一段。**换页也在 `_sync_ui` 里**，所以调用点
        # （每一个都先改了 ``_current_row``）不必自己换页。
        self._sync_ui()
        generation = self._generation

        self._cancel_job("_detail_job")
        if ref.is_video:
            self._show_video(ref)
            if self._panel.isVisible():
                self._load_metadata()
            return
        self._leave_video()
        if self._panel.isVisible():
            self._load_metadata()

        cached = self._preview_cache.get(ref.path)
        if cached is not None:
            self._preview_cache.move_to_end(ref.path)
            self._detail.set_image(cached, refit=True, fade=fade)
            self._request_full(ref, row, generation)
            # 命中也必须继续预读前后：只预读未命中的那张，连续翻图会每隔一张先闪一下缩略图。
            self._prefetch_neighbours(row)
            return

        self._detail.prepare_switch()
        # 画面上此刻还留着**上一张**照片，那比短暂的黑屏更糟（用户会以为点错了文件）：
        # 先用这张照片自己的缩略图顶上，小一点但至少是对的。
        thumbnail = self._model.thumbnail_pixmap(ref.path)
        if thumbnail is not None:
            self._detail.set_image(thumbnail.toImage(), refit=True, fade=fade)
        else:
            self._detail.clear_image()
        max_edge = self._preview_edge()

        def work_preview(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            # 取消令牌要传进解码：云盘上没下完的照片会让 ``open()`` 永远不返回，把退出拖住。
            return to_qimage(
                library.preview(ref, max_edge=max_edge, should_cancel=context.is_cancelled)
            )

        self._start_job(
            "_detail_job",
            work_preview,
            on_result=partial(self._on_preview_ready, ref, row, generation),
            on_error=partial(self._on_detail_failed, ref, row, generation),
        )
        self._prefetch_neighbours(row)

    def _show_video(self, ref: PhotoRef) -> None:
        """视频页：播放器摆到前面，首帧到达前用网格里那张缩略图垫着。"""
        self._detail.clear_image()
        self._detail.hide()
        self._video.show()
        thumbnail = self._model.thumbnail_pixmap(ref.path)
        self._video.play(ref, None if thumbnail is None else thumbnail.toImage())
        self._video.setFocus()

    def _leave_video(self) -> None:
        """从视频页翻回照片：收掉播放器，把照片视图摆回来。"""
        self._video.stop()
        self._video.hide()
        self._detail.show()

    def _on_preview_ready(self, ref: PhotoRef, row: int, generation: int, payload: object) -> None:
        if generation != self._generation or row != self._current_row:
            return  # 用户已经翻到别处了，丢弃这张迟到的图
        if not isinstance(payload, QImage):
            return
        self._cache_preview(ref.path, payload)
        self._detail.set_image(payload, refit=True)
        self._request_full(ref, row, generation)

    def _request_full(self, ref: PhotoRef, row: int, generation: int) -> None:
        """后台解码全分辨率图像，用于 100% 查看。"""
        library = self._library
        if library is None:
            return

        def work_full(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return to_qimage(library.full(ref, should_cancel=context.is_cancelled))

        self._start_job(
            "_detail_job",
            work_full,
            on_result=partial(self._on_full_ready, ref, row, generation),
            on_error=partial(self._on_detail_failed, ref, row, generation),
        )

    def _on_full_ready(self, ref: PhotoRef, row: int, generation: int, payload: object) -> None:
        if generation != self._generation or row != self._current_row:
            return
        if not isinstance(payload, QImage):
            return
        # refit=False：替换成更清晰的图，但保住用户当前的缩放与位置，画面不跳。
        self._detail.set_image(payload, refit=False)

    def _on_detail_failed(self, ref: PhotoRef, row: int, generation: int, message: str) -> None:
        if generation != self._generation or row != self._current_row:
            return
        self._detail.show_error(message)

    def _cached_metadata(self, ref: PhotoRef) -> MetadataReport | None:
        """缓存里有没有这张照片的元数据；文件改过（大小 / 修改时间）就当没有。"""
        entry = self._metadata_cache.get(ref.path)
        if entry is None or entry[0] != ref.size or entry[1] != ref.mtime_ns:
            return None
        self._metadata_cache.move_to_end(ref.path)
        return entry[2]

    def _cache_metadata(self, ref: PhotoRef, report: MetadataReport) -> None:
        self._metadata_cache[ref.path] = (ref.size, ref.mtime_ns, report)
        self._metadata_cache.move_to_end(ref.path)
        while len(self._metadata_cache) > METADATA_CACHE_SIZE:
            self._metadata_cache.popitem(last=False)

    def _prefetch_metadata(self, row: int) -> None:
        """预读附近几张的元数据，让翻照片时直接显示、没有中间态。

        一次读取约 50ms：预读了，切过去就是 0ms；不预读则每次切图都要等，只能靠「保留旧
        内容」顶着 —— 那会把上一张的地点显示给当前照片，是错数据。
        """
        if not self._panel.isVisible():
            return
        for offset in range(-METADATA_PREFETCH_RADIUS, METADATA_PREFETCH_RADIUS + 1):
            if offset == 0:
                continue
            ref = self._model.photo_at(row + offset)
            if ref is None or ref.is_video or self._cached_metadata(ref) is not None:
                continue

            def work(context: TaskContext, target: PhotoRef = ref) -> object:
                if context.is_cancelled():
                    return None
                return read_metadata(target.path)

            self._runner.submit(
                work,
                on_result=partial(self._on_metadata_prefetched, ref),
                on_error=partial(self._on_metadata_prefetch_failed, ref),
            )

    def _on_metadata_prefetched(self, ref: PhotoRef, payload: object) -> None:
        """预读结果入缓存，顺带把地点也推断好。

        地点必须在这里查完：否则切到这张照片时缓存里只有坐标，面板要先显示一遍没有地点的
        版本、再被反查结果替换 —— 那正是「切换照片时地点闪一下」的来源。
        """
        if isinstance(payload, MetadataReport):
            self._cache_metadata(ref, payload)
            self._maybe_lookup_place(ref, payload, self._generation, announce=False)

    def _on_metadata_prefetch_failed(self, ref: PhotoRef, message: str) -> None:
        logger.info("预读元数据失败：%s（%s）", ref.name, message)

    def _prefetch_neighbours(self, row: int) -> None:
        """预取前后各一张的预览，让连续翻图不白屏。"""
        library = self._library
        if library is None:
            return
        generation = self._generation
        max_edge = self._preview_edge()
        for neighbour in (row - 1, row + 1):
            ref = self._model.photo_at(neighbour)
            if ref is None or ref.is_video or ref.path in self._preview_cache:
                continue

            def work(context: TaskContext, target: PhotoRef = ref) -> object:
                if context.is_cancelled():
                    return None
                return to_qimage(
                    library.preview(target, max_edge=max_edge, should_cancel=context.is_cancelled)
                )

            self._runner.submit(
                work,
                on_result=partial(self._on_neighbour_ready, ref, generation),
            )

    def _on_neighbour_ready(self, ref: PhotoRef, generation: int, payload: object) -> None:
        if generation == self._generation and isinstance(payload, QImage):
            self._cache_preview(ref.path, payload)

    def _cache_preview(self, path: Path, image: QImage) -> None:
        self._preview_cache[path] = image
        self._preview_cache.move_to_end(path)
        while len(self._preview_cache) > PREVIEW_CACHE_SIZE:
            self._preview_cache.popitem(last=False)

    def _preview_edge(self) -> int:
        """预览图长边：略大于视口的物理像素，保证 1:1 时也不糊。"""
        viewport = self._detail.viewport().size()
        ratio = self._detail.devicePixelRatioF()
        edge = int(max(viewport.width(), viewport.height()) * ratio * PREVIEW_EDGE_FACTOR)
        return max(MIN_PREVIEW_EDGE, min(MAX_PREVIEW_EDGE, edge))


__all__ = ["ViewerFlowMixin"]
