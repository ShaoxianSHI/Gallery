"""缩略图：要哪几张、怎么换档、以及解码失败时该怎么说一句话。

网格与日历**共用同一条流水线**（缩放在工作线程里做，界面线程只留一次 ``QPixmap.fromImage``，
ADR 0015）；两页的取法不同：网格按**行**取，日历用 ``visible_refs()`` 自己取。换档那一族是同一个
交互的两种入口：捏合 / ⌘+滚轮 / ⇧⌘↑⇧⌘↓ 一档一档挪（要排队），或从菜单直接挑一档。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from gallery.core.i18n import t, t_plural
from gallery.core.library.photos import GridEntry, GroupRef, SectionRef
from gallery.ui.browser.grid import PREFETCH_MARGIN
from gallery.ui.tasks import TaskContext
from gallery.ui.window.state.thumbnail_size import ThumbnailSize

logger = logging.getLogger(__name__)


class ThumbnailFlowMixin:
    """网格与日历向缩略图流水线要图的入口，以及缩略图档位的切换。

    档位住在 :class:`~gallery.ui.window.thumbnail_size.ThumbnailSize` 里
    （ADR 0058 阶段 4）—— 窗口上留着一组同名 property 转发。
    """

    _thumb_size: ThumbnailSize

    def _init_thumb_size(self, index: int | None = None) -> None:
        self._thumb_size = ThumbnailSize(index)

    @property
    def _size_index(self) -> int:
        return self._thumb_size._size_index

    @_size_index.setter
    def _size_index(self, value: int) -> None:
        self._thumb_size._size_index = value

    @property
    def _pending_size_step(self) -> int:
        return self._thumb_size._pending_size_step

    @_pending_size_step.setter
    def _pending_size_step(self, value: int) -> None:
        self._thumb_size._pending_size_step = value

    # ---- 缩略图 ------------------------------------------------------------
    def _refresh_thumbnail_window(self) -> None:
        if self._pipeline is None:
            return
        if self._on_calendar_page():
            # 日历要的是「当前层、当前滚到的那几格」；「这批变没变」由
            # ``CalendarPage._refresh_window`` 自己判断。
            self._pipeline.set_window(
                self._calendar_page.current_view().visible_refs(),
                (),
                target_edge=self._calendar_page.thumbnail_edge(),
            )
            return
        if self._in_detail_page() and not self._detail.isHidden():
            if self._shows_filmstrip():
                self._refresh_filmstrip_thumbnails()
            return
        if not self._on_grid_page():
            return
        visible_rows = self._grid.visible_rows()
        # guard: allow-long-comment（网格没重排时「可见行」是空的，而 set_window 是整份替换）
        # **网格还没重排时，不要把已经排好的请求清掉**：那一刻「可见行」是空的，而
        # ``set_window`` 是**整份替换**，会把刚排好的几十个请求全丢掉（这一处空过一整屏）。
        # 模型**真的空了**时照旧报空窗口。
        if not visible_rows and self._model.rowCount() > 0:
            return
        prefetch_rows = self._grid.visible_rows(margin_ratio=PREFETCH_MARGIN)
        visible_set = set(visible_rows)
        visible = self._entries_missing_thumbnails(visible_rows)
        prefetch = self._entries_missing_thumbnails(
            [row for row in prefetch_rows if row not in visible_set]
        )
        self._pipeline.set_window(visible, prefetch, target_edge=self._model.icon_size.width())

    def _entries_missing_thumbnails(self, rows: Sequence[int]) -> list[GridEntry]:
        """只挑出需要取缩略图的条目（相册与分组卡片算它们的封面）。

        两种情况：手里根本没有，或者手里那张是按旧尺寸缩放的。滚动会让可见窗口反复变化，
        每次都把可见项重新要一遍，等于每滚一下都把磁盘缓存重读重缩放一次。
        """
        entries: list[GridEntry] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is None or isinstance(entry, SectionRef):
                continue  # 分区标题那一行只有字，没有图
            cover = entry.cover if isinstance(entry, GroupRef) else None
            path = cover.path if cover is not None else entry.path
            if self._model.needs_thumbnail(path):
                entries.append(entry)
        return entries

    def _shows_filmstrip(self) -> bool:
        """大图页上正看着一张照片。视频页没有那条面板（ADR 0091 决策 3）。"""
        ref = self._model.photo_at(self._current_row)
        return ref is not None and not ref.is_video

    def _refresh_filmstrip_thumbnails(self) -> None:
        """画廊条要的那一批：视口里的先出图，两侧备用的排后面。

        **档位跟网格那一份走**：模型只按路径存一张，换一个档位就等于把网格那份也作废，
        切回网格要整屏重解码一遍。缩到 64 点由画廊条在绘制时做。
        """
        pipeline = self._pipeline
        if pipeline is None:
            return
        visible_rows = set(self._detail.filmstrip_visible_rows())
        visible: list[GridEntry] = []
        prefetch: list[GridEntry] = []
        for row in self._detail.filmstrip_rows():
            entry = self._model.entry_at(row)
            if entry is None or not self._model.needs_thumbnail(entry.path):
                continue
            (visible if row in visible_rows else prefetch).append(entry)
        pipeline.set_window(visible, prefetch, target_edge=self._model.icon_size.width())

    def _on_thumbnail_ready(self, path: object, image: object, edge: int = 0) -> None:
        if isinstance(path, Path) and isinstance(image, QImage):
            self._model.set_thumbnail(path, image, edge)
            # 日历那一侧存的是另一份（小尺寸、按边长记账），两边各收各的。
            self._calendar_page.set_thumbnail(path, image, edge)
            if self._in_detail_page() and not self._detail.isHidden():
                self._detail.refresh_filmstrip()

    # ---- 缩略图尺寸 --------------------------------------------------------

    def set_thumbnail_size(self, index: int) -> None:
        """直接切到某一档（菜单用，也是排队那一步执行时用的）。

        **明确选了一档，排着的那些就都作废**：否则「动画期间按了 ⇧⌘↑（正好在顶上，本来
        什么也不该发生），接着又从菜单点了某一档」会让那一下在动画结束后才生效。
        """
        if self._thumb_size.select(index):
            self._apply_thumbnail_size()
            self._remember_thumbnail_size()

    def step_thumbnail_size(self, step: int) -> None:
        """按档位加减一档（捏合、⌘+滚轮、⇧⌘↑ / ⇧⌘↓ 用）。

        **上一段切档动画还在跑就排着，不丢**：第二下若立刻执行，网格那边判「上一段没跑完」
        就不接第二段、直接一步落位，画面上是一次跳变。**捏合不走这条路**（它已经限速过，
        意图本来就是连续的），而快捷键与 ⌘+滚轮是**每一下都独立的意图**。**排不排队的判据
        是「算上已排着的之后，这一下还会不会换档」**，而不是「当前在不在头尾」。"""
        if self._grid.is_transitioning:
            self._thumb_size.queue_step(step)
            return
        target = self._thumb_size.step(step)
        if target == self._size_index:
            return  # 已经在头尾：以前会提示「已经是最大尺寸」，随状态条一起去掉
        self.set_thumbnail_size(target)

    def _run_pending_size_step(self) -> None:
        """上一段切档动画结束了：把排着的那一下执行掉（网格发来 :attr:`transition_ended`）。

        排队的档数是**累加**出来的，所以这里一次 ``shifted_index`` 就跨到头。**先清空再执行**：
        执行途中会再起一段新的过渡，不先清空就会一次调用套一次调用地往下钻。
        """
        step = self._thumb_size.take_pending()
        if step:
            self.step_thumbnail_size(step)

    def _apply_thumbnail_size(self) -> None:
        """把档位交给网格；实际格子宽度由视口宽度自适应。

        网格算完会通过 ``icon_edge_changed`` 回来，届时再作废并重取缩略图。
        """
        self._grid.set_target_edge(self._thumb_size.edge)
        self._sync_size_actions()

    def _on_grid_edge_changed(self, edge: int) -> None:
        """网格自适应出新的缩略图尺寸：作废旧图并重取可见的那一批。"""
        self._model.set_icon_edge(edge)
        self._refresh_thumbnail_window()

    def _sync_size_actions(self) -> None:
        for index, action in enumerate(self._size_actions):
            action.setChecked(index == self._size_index)

    def _remember_thumbnail_size(self) -> None:
        # 档位是**库级**偏好（ADR 0038 决策 4）：换库时各自回到自己那一份。
        self._save_ui_state()

    def _on_thumbnail_failed(self, path: object, message: str) -> None:
        # guard: allow-long-comment（丢错类别会把一次成功的改名说成「文件可能已损坏」）
        # 两类失败不是「照片坏了」，是用户自己（或自家代码）刚造成的，丢掉：**这一格已经不在
        # 当前这一层**（刚改名 / 移走 / 删掉 / 换库），或者**这个路径是自家刚写过的**（进行中那次
        # 解码读的就是它，文件被改名那一瞬间确实读不到）。不丢的话，刚改完名成功横幅立刻变成
        # 「文件可能已损坏」—— 把成功的操作说成数据损坏。
        if isinstance(path, Path) and (
            not self._model.shows_path(path) or self._was_self_written(path)
        ):
            logger.info("缩略图失败，但这一格已经不在（或刚被自家改过），丢弃：%s", message)
            if self._pipeline is not None:
                # 别把它记成「这文件读不出来」：照片是好的。
                self._pipeline.forget([path])
            return
        failed = self._banner_text.note_thumbnail_failure()
        logger.info("缩略图失败：%s", message)
        self._show_banner(
            t_plural("flow.thumbnail_flow.failed", failed, reason=message),
            background=True,
            auto_hide=True,
        )

    # ---- 清空缩略图缓存 ------------------------------------------------------

    def clear_thumbnail_cache(self) -> None:
        """清空当前库的缩略图缓存（只删缓存目录里的文件）。**删除本身跑在工作线程上。**

        删除是 O(n) 的（2 万个条目约 884 ms），而它紧跟在一个模态对话框之后；留在主线程上
        的话，对话框关掉的那一瞬间窗口整个冻住。它跟粘贴 / 改名走同一条路（``self._runner``
        + 横幅，ADR 0018），并带代次判据：清到一半换了库时回来的结果不会落到新库上。
        """
        library = self._library
        if library is None:
            return
        answer = QMessageBox.question(
            self,
            t("flow.thumbnail_flow.clear_title"),
            t("flow.thumbnail_flow.clear_body", name=library.display_name),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._cache_job is not None:
            # 两次清空是同一个意思，跑两遍只会让第二个的「已清除 N 个」覆盖第一个的。
            self._show_banner(t("flow.thumbnail_flow.clear_busy"))
            return

        def work(context: TaskContext) -> object:
            # ``clear_cache`` 不接取消令牌：半途停下来只会留下**一半**的缓存目录，那比慢更难解释。
            return library.clear_cache()

        self._show_banner(t("flow.thumbnail_flow.clearing"), background=True)
        self._start_job(
            "_cache_job",
            work,
            on_result=partial(self._on_thumbnail_cache_cleared, self._generation),
            on_error=self._on_thumbnail_cache_failed,
        )

    def _on_thumbnail_cache_cleared(self, generation: int, payload: object) -> None:
        self._finish_job("_cache_job")
        if generation != self._generation:
            return  # 已经换库了：删的是上一个库的缓存，别去动新库的模型
        removed = payload if isinstance(payload, int) else 0
        self._model.clear_thumbnails()
        self._refresh_thumbnail_window()
        self._show_banner(t_plural("flow.thumbnail_flow.cleared", removed), auto_hide=True)

    def _on_thumbnail_cache_failed(self, message: str) -> None:
        self._finish_job("_cache_job")
        logger.info("清空缩略图缓存失败：%s", message)
        # 这一句**不自动收起**：它是「要用户动手处理」的（换个地方、腾点空间再试）。
        self._show_banner(t("flow.thumbnail_flow.clear_failed", reason=message.rstrip("。")))


__all__ = ["ThumbnailFlowMixin"]
