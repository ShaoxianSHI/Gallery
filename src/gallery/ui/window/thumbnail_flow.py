"""缩略图：要哪几张、怎么换档、以及解码失败时该怎么说一句话。

网格与日历**共用同一条流水线**（:mod:`gallery.ui.thumbs.pipeline`）——缩放在工作线程里做，
界面线程只留一次 ``QPixmap.fromImage``（ADR 0015）。于是这一段要回答的是「**现在要哪一批**」，
而两页的取法不同：网格按**行**取（日历那一页用 ``visible_refs()`` 自己取），
所以 :meth:`ThumbnailFlowMixin._refresh_thumbnail_window` 开头就分岔。

那一段里有一句必须留住的经验（那一处空过一整屏）：**网格还没重排时不要把已经排好的请求
清掉**。刚换上一批照片的那一刻视图内部布局还没跟上，「可见行」是空的，而 ``set_window``
是整份替换 —— 那一次空窗口会把刚排好的几十个请求全丢掉。

换档那一族（``step_thumbnail_size`` 与它排着的那一步）是同一个交互的两种入口：
捏合 / ⌘+滚轮 / ⇧⌘↑ ⇧⌘↓ 一档一档挪，或从菜单直接挑一档。差别在于前者要排队、后者直接落位。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from gallery.core.library.photos import GridEntry, GroupRef
from gallery.ui.browser.grid import PREFETCH_MARGIN
from gallery.ui.browser.thumb_sizes import edge_for_index, shifted_index
from gallery.ui.tasks import TaskContext

logger = logging.getLogger(__name__)


class ThumbnailFlowMixin:
    """网格与日历向缩略图流水线要图的入口，以及缩略图档位的切换。"""

    # ---- 缩略图 ------------------------------------------------------------
    def _refresh_thumbnail_window(self) -> None:
        if self._pipeline is None:
            return
        if self._stack.currentWidget() is self._calendar_page:
            # 日历要的那一批是「当前层、当前滚到的那几格」，与网格的按行取法不同源，
            # 但**同样交给这一条流水线**（缩放在工作线程里做，ADR 0015）。
            #
            # 「这一批变没变」由**页面**自己判断（``CalendarPage._refresh_window``）——
            # 它认得手上的图有没有被换库 / 重扫 / 文件变化清掉；这里只管照着报。
            self._pipeline.set_window(
                self._calendar_page.current_view().visible_refs(),
                (),
                target_edge=self._calendar_page.thumbnail_edge(),
            )
            return
        if self._stack.currentWidget() is not self._grid_page:
            return
        visible_rows = self._grid.visible_rows()
        # **网格还没重排时，不要把已经排好的请求清掉**（这一处空过一整屏）。
        #
        # 刚换成一批照片的那一刻，视图内部的布局还没跟上（`indexAt` 全落空），
        # 于是「可见行」是空的；而 ``set_window`` 是**整份替换** —— 那一次空窗口
        # 会把刚排好的几十个请求全丢掉。在途的还会跑完（它不在队列里），
        # 没跑到的就彻底没了，要等下一次 ``viewport_changed`` 才重新排上。
        # 从日历点一格进网格正好连着发生两次（换页一次、模型换掉一次），
        # 于是有几张到几秒的空白，很看运气。
        #
        # 模型**真的空了**（空相册、换了空层）时照旧要报空窗口：那一批确实不需要了。
        if not visible_rows and self._model.rowCount() > 0:
            return
        prefetch_rows = self._grid.visible_rows(margin_ratio=PREFETCH_MARGIN)
        visible_set = set(visible_rows)
        visible = self._entries_missing_thumbnails(visible_rows)
        prefetch = self._entries_missing_thumbnails(
            [row for row in prefetch_rows if row not in visible_set]
        )
        # 把当前档位一起交给流水线：缩放在工作线程里做，界面线程只留一次
        # QPixmap.fromImage（ADR 0015）。
        self._pipeline.set_window(visible, prefetch, target_edge=self._model.icon_size.width())

    def _entries_missing_thumbnails(self, rows: Sequence[int]) -> list[GridEntry]:
        """只挑出需要取缩略图的条目（相册与分组卡片算它们的封面）。

        两种情况：手里根本没有，或者手里那张是按旧尺寸缩放的。滚动会让可见窗口
        反复变化，如果每次都把可见项重新要一遍，就等于每滚一下都把磁盘缓存重读
        重缩放一次，内存里的 LRU 也形同虚设。
        """
        entries: list[GridEntry] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is None:
                continue
            cover = entry.cover if isinstance(entry, GroupRef) else None
            path = cover.path if cover is not None else entry.path
            if self._model.needs_thumbnail(path):
                entries.append(entry)
        return entries

    def _on_thumbnail_ready(self, path: object, image: object, edge: int = 0) -> None:
        if isinstance(path, Path) and isinstance(image, QImage):
            self._model.set_thumbnail(path, image, edge)
            # 日历那一侧存的是另一份（小尺寸、按边长记账），两边各收各的。
            self._calendar_page.set_thumbnail(path, image, edge)

    # ---- 缩略图尺寸 --------------------------------------------------------

    def set_thumbnail_size(self, index: int) -> None:
        """直接切到某一档（菜单用，也是排队那一步执行时用的）。

        **明确选了一档，排着的那些就都作废。** 否则「动画期间按了 ⇧⌘↑（正好在顶上，
        本来什么也不该发生），接着又从菜单点了某一档」会让那一下在动画结束后才生效 ——
        用户看到的是自己刚点完，尺寸又自己动了一下。
        """
        self._pending_size_step = 0
        if index == self._size_index:
            return
        self._size_index = index
        self._apply_thumbnail_size()
        self._remember_thumbnail_size()

    def step_thumbnail_size(self, step: int) -> None:
        """按档位加减一档（捏合、⌘+滚轮、⇧⌘↑ / ⇧⌘↓ 用）。

        **上一段切档动画还在跑就排着，不丢。** 连按两下时第二下若立刻执行，网格那边
        判「上一段没跑完」就不接第二段、直接一步落位（见
        ``grid.PhotoGrid._start_transition``），画面上是一次跳变 —— 快起来一帧动画都
        看不见。排着等它跑完，两下就都是看得见的动画。

        **捏合为什么不走这条路**：捏合已经由 ``thumb_sizes.RateLimitedAccumulator``
        限速过了（间隔不足直接丢掉）。捏合的意图本来就是连续的，多出来的那几下是同
        一个意图的重复表达；而快捷键与 ⌘+滚轮是**每一下都独立的意图**，丢掉一下就是
        丢了一次明确的操作 —— 所以这两条排队，那一条丢弃。

        菜单里直接点某一档（:meth:`set_thumbnail_size`）不排队：那是明确选了终值，
        等一等反而不对劲，它维持原来「直接落到用户选的那一档」的行为，并且会把
        排着的那些作废。

        **排不排队的判据是「算上已经排着的那些之后，这一下还会不会换档」**，而不是
        「当前在不在头尾」：顶部那一档上按 ⇧⌘↑ 要是正好抵销掉排着的那一档缩小，
        少了这一句，「先 ⇧⌘↓ 再 ⇧⌘↑」会变成真的一次缩小；而抵销掉之后它也不该
        凭空排一档上去。
        """
        if self._grid.is_transitioning:
            effective = shifted_index(self._size_index, self._pending_size_step)
            if shifted_index(effective, step) != effective:
                self._pending_size_step += step
            return
        target = shifted_index(self._size_index, step)
        if target == self._size_index:
            return  # 已经在头尾：以前会提示「已经是最大尺寸」，随状态条一起去掉
        self.set_thumbnail_size(target)

    def _run_pending_size_step(self) -> None:
        """上一段切档动画结束了：把排着的那一下执行掉（网格发来 :attr:`transition_ended`）。

        排队的档数在 :meth:`step_thumbnail_size` 里是**累加**出来的，所以这里一次
        ``shifted_index`` 就跨到头：连按三下 ⇧⌘↓ 要能一下到最小，不是慢慢挪。

        **先清空再执行**：执行途中会再起一段新的过渡，它结束时会再叫回来一次这个槽。
        不先清空就会一次调用套一次调用地往下钻。队列被自己抵成 0（先放大又缩小）
        或者越界夹住时，这里什么也不做 —— 网格也就不会为此起一段动画。
        """
        step, self._pending_size_step = self._pending_size_step, 0
        if step:
            self.step_thumbnail_size(step)

    def _apply_thumbnail_size(self) -> None:
        """把档位交给网格；实际格子宽度由视口宽度自适应。

        网格算完会通过 ``icon_edge_changed`` 回来，届时再作废并重取缩略图。
        """
        self._grid.set_target_edge(edge_for_index(self._size_index))
        self._sync_size_actions()

    def _on_grid_edge_changed(self, edge: int) -> None:
        """网格自适应出新的缩略图尺寸：作废旧图并重取可见的那一批。"""
        self._model.set_icon_edge(edge)
        self._refresh_thumbnail_window()

    def _sync_size_actions(self) -> None:
        for index, action in enumerate(self._size_actions):
            action.setChecked(index == self._size_index)

    def _remember_thumbnail_size(self) -> None:
        self._session = self._session.with_thumbnail_edge(edge_for_index(self._size_index))
        self._save_session()

    def _on_thumbnail_failed(self, path: object, message: str) -> None:
        # 两类失败不是「照片坏了」，是用户自己（或自家代码）刚刚造成的，丢掉：
        #
        # 1. **这一格已经不在当前这一层** —— 用户刚改名 / 移走 / 删掉 / 换了库。
        #    成功的回填早就在模型里按这个判据丢掉了（``PhotoListModel.set_thumbnail``），
        #    失败这一侧原先却没有对称的判据。
        # 2. **这个路径是自家刚写过的** —— 在途的那次解码读的就是它，而文件被改名 /
        #    移走的那一瞬间它确实读不到，失败是必然的。这类失败是**并发**的产物：
        #    解码开始时文件还在、失败报上来时它又回来了（撤销改名就是这样），
        #    照片其实好得很。
        #
        # 不丢的话，用户看到的是：刚改完名（或刚撤销）成功，横幅立刻变成
        # 「文件可能已损坏」—— 把一次成功的操作说成了数据损坏，最坏的一种假警报。
        if isinstance(path, Path) and (
            not self._model.shows_path(path) or self._was_self_written(path)
        ):
            logger.info("缩略图失败，但这一格已经不在（或刚被自家改过），丢弃：%s", message)
            if self._pipeline is not None:
                # 别把它记成「这文件读不出来」：照片是好的，下次窗口刷新该重取就重取。
                self._pipeline.forget([path])
            return
        self._thumbnail_failures += 1
        logger.info("缩略图失败：%s", message)
        self._show_banner(
            f"有 {self._thumbnail_failures} 张照片无法显示缩略图：{message}",
            background=True,
            auto_hide=True,
        )

    # ---- 清空缩略图缓存 ------------------------------------------------------

    def clear_thumbnail_cache(self) -> None:
        """清空当前库的缩略图缓存（只删缓存目录里的文件）。**删除本身跑在工作线程上。**

        这一条原先是整个窗口里**唯一一个留在主线程上的 O(n) 动作**，而它一次要删的
        是整份缓存：实测 2 万个条目约 884 ms，按默认那 2 GiB 的上限外推是 2～3 秒。
        它又正好紧跟在模态对话框之后 —— 对话框关掉的那一瞬间窗口整个冻住，而用户
        按的明明是「清空缓存」，看到的是软件没反应。

        所以它跟粘贴 / 改名走同一条路（``self._runner`` + 横幅，见 ADR 0018）：对话框
        仍在主线程（那是 Qt 的规定），**要花钱的那一段**进 ``TaskRunner``，结果与失败
        都在 :meth:`_show_banner` 那一条槽上报。

        顺带按了代次判据：清到一半用户换了库的话，回来的是**上一个库**的删除结果，
        而那一步会把新库的模型里所有缩略图清掉 —— 界面于是对着一个没被清过的库
        重新解码全部照片。
        """
        library = self._library
        if library is None:
            return
        answer = QMessageBox.question(
            self,
            "清空缩略图缓存",
            f"将删除「{library.display_name}」的缩略图缓存。\n"
            "照片本身不受影响，缓存会在下次浏览时自动重建。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._cache_job is not None:
            # 两次清空是同一个意思，跑两遍只会让第二个的「已清除 N 个」覆盖第一个的。
            self._show_banner("上一次清空还没结束。")
            return

        def work(context: TaskContext) -> object:
            # ``clear_cache`` 不接取消令牌：它要么删完，要么中途出错，
            # 半途停下来只会留下**一半**的缓存目录，那比慢更难解释。
            return library.clear_cache()

        self._show_banner("正在清空缩略图缓存…", background=True)
        self._cache_job = self._runner.submit(
            work,
            on_result=partial(self._on_thumbnail_cache_cleared, self._generation),
            on_error=self._on_thumbnail_cache_failed,
        )

    def _on_thumbnail_cache_cleared(self, generation: int, payload: object) -> None:
        self._cache_job = None
        if generation != self._generation:
            return  # 已经换库了：删的是上一个库的缓存，别去动新库的模型
        removed = payload if isinstance(payload, int) else 0
        self._model.clear_thumbnails()
        self._refresh_thumbnail_window()
        self._show_banner(f"已清除 {removed} 个缩略图缓存文件。", auto_hide=True)

    def _on_thumbnail_cache_failed(self, message: str) -> None:
        self._cache_job = None
        logger.info("清空缩略图缓存失败：%s", message)
        # 这一句**不自动收起**：它是「要用户动手处理」的（换个地方、腾点空间再试），
        # 收走了就等于用户还没看见。
        self._show_banner(f"没能清空缩略图缓存：{message.rstrip('。')}")


__all__ = ["ThumbnailFlowMixin"]
