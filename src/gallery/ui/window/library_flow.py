"""打开一个库、把它的内容列出来、以及把这一次运行的状态记进会话。

这是整个窗口的**入口流程**：欢迎页「打开文件夹…」或菜单「最近打开」到这里为止，
之后的每一件事（排序、缩略图、大图、右键菜单）都以 ``self._library`` 与
``self._generation`` 为前提。它回答三个问题：

- **这个库能不能打开**：缓存放库内还是系统目录（``_resolve_cache_location``，
  整个应用唯一一处会往照片目录里写东西的地方，而且必须由用户刚刚的点击触发）；
- **这一层有什么**：三种浏览方式各走一遍列举（分组视图铺开组内、平铺递归整库、
  相册模式只列当前层），流式分批送回，扫完再交给排序那一条链；
- **这次运行记住了什么**：最近打开过哪些库、窗口在哪儿（会话段，见
  :meth:`LibraryFlowMixin._save_session`）。

**切换库的那一刀**（:meth:`LibraryFlowMixin._teardown_library`）在这一段里，因为它要作废
的每一样东西都是上面这些流程起头的：监听器、扫描、元数据读取、检索、索引、地点解析，
外加那个会往目标目录里**落文件**的 ⌘V / 「移动到相册」—— 后者不取消的话，收尾时会拿
旧库的记录去问新库「下一次 ⌘Z 撤什么」，于是横幅上写着「⌘Z 可以撤销」而按下去什么也不发生。
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QMessageBox

from gallery.core import paths
from gallery.core.errors import GalleryError
from gallery.core.library.config import prepare_library
from gallery.core.library.facade import Library, PlacePass
from gallery.core.library.groups import sort_photos
from gallery.core.library.photos import GridEntry, PhotoRef
from gallery.core.library.scanner import ScanReport
from gallery.core.library.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    WindowGeometry,
    save_session,
)
from gallery.ui.tasks import TaskContext
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.window.notice import NoticeSlot

logger = logging.getLogger(__name__)

#: 扫描时每积累多少张就送回主线程一次（流式出图，不等整库扫完）。
SCAN_BATCH_SIZE = 64


class LibraryFlowMixin:
    """打开库 / 关闭库 / 列内容 / 记会话。窗口的入口流程都在这里。"""

    # ---- 恢复上一次的运行状态 ------------------------------------------------

    def _restore_session(self) -> None:
        self._welcome.set_recent(self._session.recent_roots)
        window = self._session.window
        if window is None:
            return
        self.setGeometry(window.x, window.y, window.width, window.height)
        if window.maximized:
            self.showMaximized()

    # ---- 打开库 ------------------------------------------------------------
    def choose_folder(self) -> None:
        """弹出系统文件夹选择框。"""
        start = str(self._library.root) if self._library is not None else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择照片文件夹", start)
        if chosen:
            self.open_library(Path(chosen))

    def open_library(self, root: Path, *, silent: bool = False) -> bool:
        """打开一个库。返回是否成功。

        :param silent: 启动时自动恢复上次的库时为 ``True``：不弹任何对话框，
            缓存一律放到系统目录，避免用户刚打开软件就被问问题。
        """
        root = Path(root)
        try:
            cache_in_library = self._resolve_cache_location(root, silent=silent)
        except GalleryError as exc:
            self._report_error(exc.message)
            return False
        if cache_in_library is None:
            return False
        try:
            library = Library.open(root, cache_in_library=cache_in_library)
        except GalleryError as exc:
            if not silent:
                self._report_error(exc.message)
            else:
                logger.info("自动打开上次的库失败：%s", exc.message)
            return False

        self._activate_library(library)
        return True

    def _resolve_cache_location(self, root: Path, *, silent: bool) -> bool | None:
        """决定缩略图缓存放在库内还是系统目录。

        返回 ``True`` 表示库内、``False`` 表示系统目录、``None`` 表示用户取消。
        """
        if paths.has_config_dir(root):
            return True
        if silent:
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("缓存放在哪里？")
        box.setText(f"「{root.name}」还没有 Gallery 的缓存目录。")
        box.setInformativeText(
            "缩略图缓存能让浏览快很多。\n\n"
            f"· 在库中缓存：在照片文件夹里创建 {paths.config_dir(root).name} 目录"
            "（随时可删，删除后会自动重建）\n"
            "· 缓存到系统目录：照片文件夹保持一个字节都不写入\n\n"
            "无论选哪个，Gallery 都不会移动或修改你的照片；"
            "只有你自己触发的重命名（⌘R）和移到废纸篓（⌘⌫）会动文件，"
            "两者都能用 ⌘Z 撤销。"
        )
        in_library = box.addButton("在库中缓存（推荐）", QMessageBox.ButtonRole.AcceptRole)
        system_cache = box.addButton("缓存到系统目录", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(in_library)
        box.exec()

        clicked = box.clickedButton()
        if clicked is in_library:
            # 这是全应用唯一会写入照片目录的动作，必须由用户刚刚的点击触发。
            prepare_library(root)
            return True
        if clicked is system_cache:
            return False
        return None

    def _activate_library(self, library: Library) -> None:
        self._teardown_library()
        self._library = library
        self._generation += 1
        self._thumbnail_failures = 0
        # 换一个库就是换一摞撤销栈，菜单里的「撤销」要立刻跟着换。
        self._refresh_undo_label()
        self._current_row = -1
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._facet_values = {}
        self._model.clear()

        self._pipeline = ThumbnailPipeline(library, self._runner, self)
        self._pipeline.ready.connect(self._on_thumbnail_ready)
        self._pipeline.failed.connect(self._on_thumbnail_failed)

        self._banner.hide()
        # 切库时真正清空大图区，别让上一个库的全分辨率副本一直占着内存。
        self._detail.clear_image()
        self._panel.clear()
        # 面板上那个「编辑元数据」按钮的可用性跟着新库走（没装 exiftool 的库上它不出现）。
        self._update_metadata_editable()
        self._current_dir = library.root
        self._arm_focus()  # 打开库走的是自己那一套，不经过 _reload_level
        # 每一层都要重排（排序字段总是有的），所以扫出来的一律先攒着，等顺序定下来。
        self._staged = []
        self._level_notice = False
        self._progress_notice = False
        self._progress_notice_text = None
        self._place_notice_text = None
        self._scan_issue = False
        self._update_breadcrumb()
        self._update_mode_actions()
        self._library_title = library.display_name
        self._calendar_page.set_level(self._session.resolved_calendar_level())
        # **只换页**：这一段末尾本来就有一句 `_start_scan()`，再起一遍就是两遍扫描
        # 同时往模型里塞同一库照片，于是每一行都出现两次。
        if self._browse_mode == BROWSE_MODE_CALENDAR:
            self._show_calendar()
        else:
            self._stack.setCurrentWidget(self._grid_page)
        self._refresh_title()  # 换库之后标题栏回到相册名（ADR 0025 决策 4）
        self._update_action_states()
        self._remember(library.root)
        self._watch.start(library)
        self._start_scan()

    def _teardown_library(self) -> None:
        # 监听器第一刀：它还在跑的时候，下面就把库和索引都拆了。
        self._watch.stop()
        self._watch_echoes.clear()
        self._watch_pending = None
        self._pending_group = None
        self._pending_focus = None
        # 「到了这一层该选中哪一格」是**上一个库**留下的：换库就得跟着一起走，
        # 不然新库的那一层会按上一个库的路径去选中一格（那一格多半在新库里根本不存在，
        # 于是只是白找一趟；更糟的是它要是在新库里也碰巧存在，选中的就是另一张照片）。
        # 「现在摆的是哪一层」同样属于上一个库：不作废的话，新库的第一层会被当成
        # 「还是刚才那一层」，于是把上一库的选中原样搬过来。
        self._focus = None
        self._level_key = None
        self._cancel_facet_job()
        self._cancel_calendar_job()
        self._cancel_place_pass()
        # **写操作闸门也要收。** 搬几百 MB 的那一个 ⌘V / 「移动到相册」是在旧库的
        # ``Library`` 上跑的；不取消它就让它接着跑，而 ``self._library`` 此时已经指向
        # 新库了 —— 收尾时 ``_on_transfer_done`` 会去问**新库**「下一次 ⌘Z 撤什么」，
        # 于是那条记录明明记在旧库里、界面上却一个都撤不掉。用户在横幅里看到的
        # 恰恰是「⌘Z 可以撤销」，按下去什么也没发生。
        if self._write_job is not None:
            self._runner.cancel(self._write_job)
            self._write_job = None
        # 清空缩略图缓存同理：它删的是**旧库**缓存目录里的文件，跑完的回调按代次
        # 判据会自己丢掉结果（不碰新库的模型），而取消只是为了别让它继续白花几秒。
        if self._cache_job is not None:
            self._runner.cancel(self._cache_job)
            self._cache_job = None
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        if self._detail_job is not None:
            self._runner.cancel(self._detail_job)
            self._detail_job = None
        self._cancel_search()
        self._cancel_photo_index()
        if self._pipeline is not None:
            self._pipeline.shutdown()
            self._pipeline.deleteLater()
            self._pipeline = None
        if self._library is not None:
            self._library.close_index()
        self._library = None
        self._group_view = None
        self._facet_values = {}
        self._update_metadata_editable()
        # 条件是**临时的**（ADR 0026 决策 9）：换库之后还留着上一座库的那几枚胶囊，
        # 用户看到的检索结果属于一个已经关掉的文件夹。
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        """按当前浏览方式列出内容：平铺模式递归整库，相册模式只列当前一层。

        已经进入某个分组时不再扫盘 —— 这一组是上一次分组算出来的，直接铺开即可。

        **有筛选条件时走检索那条路**（ADR 0026 决策 7）：结果是横跨整个库的，
        与当前在哪一层无关。
        """
        library = self._library
        if library is None:
            return
        if self._conditions:
            # 搜索优先于分组：分组那一族是「这一层里怎么归拢」，而检索结果是**一层**。
            self._start_search(library)
        elif self._group_view is not None:
            self._start_group_listing(library)
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._start_level_scan(library)
        else:
            self._start_flat_scan(library)

    def _start_group_listing(self, library: Library) -> None:
        """分组视图：铺开这个组里的照片，并按当前排序排好。"""
        generation = self._generation
        group = self._group_view
        assert group is not None  # 只在 _group_view 非空时调用
        photos = list(group.photos)
        if not photos:
            return
        ordered = sort_photos(
            photos,
            order=self._current_order(),
            root=library.root,
            values=self._facet_values,
        )

        def work(context: TaskContext) -> object:
            return tuple(ordered)

        self._scan_job = self._runner.submit(
            work,
            on_result=partial(self._on_group_listed, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _on_group_listed(self, generation: int, payload: object) -> None:
        """分组视图的照片进网格，然后按「扫描完成」那一套收尾。"""
        if generation != self._generation:
            return
        entries = payload if isinstance(payload, tuple) else ()
        if self._staged is None:
            self._model.append(entries)
        else:
            self._staged.extend(entries)
        self._on_scan_done(generation, ScanReport())

    def _start_level_scan(self, library: Library) -> None:
        """相册模式：列出当前层（相册在前，照片在后）。"""
        generation = self._generation
        report = ScanReport()
        directory = self._current_dir or library.root

        def work(context: TaskContext) -> object:
            batch: list[GridEntry] = []
            for entry in library.iter_entries(
                directory, should_cancel=context.is_cancelled, report=report
            ):
                batch.append(entry)
                if len(batch) >= SCAN_BATCH_SIZE:
                    context.progress(tuple(batch))
                    batch = []
            if batch:
                context.progress(tuple(batch))
            return report

        self._scan_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _start_flat_scan(self, library: Library) -> None:
        generation = self._generation
        report = ScanReport()

        def work(context: TaskContext) -> object:
            batch: list[PhotoRef] = []
            for ref in library.iter_photos(should_cancel=context.is_cancelled, report=report):
                batch.append(ref)
                if len(batch) >= SCAN_BATCH_SIZE:
                    context.progress(tuple(batch))
                    batch = []
            if batch:
                context.progress(tuple(batch))
            return report

        self._scan_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _feed_calendar(self) -> None:
        """扫完之后把整库交给日历分桶。

        **只在日历那一页上做**：同样的整库平铺在「全部照片」下已经在跑第二遍读元数据了，
        日历再起一次就是白读一次 EXIF。
        """
        if self._browse_mode != BROWSE_MODE_CALENDAR or self._library is None:
            return
        # **日历那一页不在前面时不喂**：模型每次重排都会走到这里（`_apply_ordering`），
        # 而用户正在看的可能是日历点出来的那一层网格 —— 那时重新分桶、清空日历那份
        # 缩略图仓库都是白做（还会把日历那一页已经出好的图扔掉）。
        if self._stack.currentWidget() is not self._calendar_page:
            return
        if self._scan_job is not None:  # 还没扫完，等 _on_scan_done
            return
        self._start_calendar_load()

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        if self._staged is not None:
            # 扫描期间一律先攒着：**边扫边出图在这里等于闪一下** —— 用户先看到一屏按
            # 扫描顺序排的照片，下一秒它们全变成排好序的（甚至全变成组卡片），看起来
            # 像界面自己乱了。攒到顺序定下来再一次给出去，中间那段宁可留白
            # （横幅会说明在读什么）。
            self._staged.extend(batch)
            return
        self._model.append(batch)
        self._refresh_thumbnail_window()

    def _on_scan_done(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        report = payload if isinstance(payload, ScanReport) else ScanReport()
        # 「扫了多少张」原本在状态条上，条子没了就不报了；出问题才值得打断用户。
        if report.has_issues:
            self._scan_issue = True
            self._show_banner(f"部分内容没能读取：{report.summary}")
        # 「这一层空不空」**不在这里判断**：要重排时条目还攒着没上屏（见 _staged），
        # 那时模型里一条也没有，照着它判断就会把一屏相册 / 组卡片误报成「空」。
        # 真正该由最终内容决定，所以交给 _apply_ordering → _update_level_notice。
        if self._watch_pending:
            # 扫描在途时收到过外部变化：流式扫描没法在半路上改（会插到旧位置、会插重）。
            # 干脆重来这一层，几十毫秒的事。
            self._watch_pending = None
            self._reload_level()
            return
        self._refresh_thumbnail_window()
        self._start_facet_read()  # 里面会走到 `_apply_ordering` → 喂日历
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _on_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        self._scan_issue = True
        self._show_banner(f"扫描失败：{message}")
        # 已经攒到的条目照常摆出来（按当前能算出来的顺序）：扫到一半失败时给用户
        # 一屏能看的东西，比给一屏空白加一句「扫描失败」有用。
        self._apply_ordering()

    # ---- 打开库时把地点解析一遍（ADR 0010）------------------------------------

    def _maybe_start_place_pass(self) -> None:
        """第一次打开一个库时，在后台把整库的地点算出来存进索引。

        **为什么是打开时而不是等用户去分组**：分组与元数据面板要的是同一份反查结论，
        两处各算一次就必然漂（面板写着杭州、分组写着 Bordeaux）。打开时算一遍存进
        索引，之后两处都只读它 —— 详见 ADR 0010。

        一个库里只跑一次：``_place_pass_roots`` 记着起过哪些库，换库才重来，
        来回切回同一个库也不再起。
        扫描本身出问题时不抢横幅 —— 那句「哪些内容没读到」比进度重要。

        **本层正在读元数据时先让着**：那一遍读的就是同一批照片的 EXIF，
        两边同时跑等于把同一件事做两遍。等它结束再开始（见 ``_on_facets_ready``）。
        """
        library = self._library
        if (
            library is None
            or library.root in self._place_pass_roots
            or self._scan_issue
            or self._facet_job is not None
        ):
            return
        self._place_pass_roots.add(library.root)
        resolver = self._place_lookup()

        def work(context: TaskContext) -> object:
            return library.resolve_places(
                should_cancel=context.is_cancelled,
                on_work=lambda count: context.progress(("work", count)),
                on_progress=lambda done, total: context.progress(done),
                status=lambda text: context.progress(text),
                resolver=resolver,
            )

        # **横幅不在这儿亮**：这一遍多半是空跑（库里都算过了），先亮等于在说一件
        # 没有发生的事。等 worker 真的找到活干，由 ``_on_place_pass_work`` 再说。
        self._place_job = self._runner.submit(
            work,
            on_progress=self._on_place_pass_progress,
            on_result=self._on_place_pass_done,
            on_error=self._on_place_pass_failed,
        )

    def _on_place_pass_progress(self, payload: object) -> None:
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "work":
            self._on_place_pass_work(payload[1])
            return
        if self._place_notice_text is None or self._scan_issue:
            return
        if isinstance(payload, str):
            self._show_place_notice(payload)  # 「正在加载城市库…」这类说明
            return
        if isinstance(payload, int) and payload > 0:
            self._show_place_notice(f"正在解析照片的地点…已处理 {payload} 张")

    def _on_place_pass_work(self, count: object) -> None:
        """真的找到活干了才亮横幅：空跑的库不该让用户看见「正在解析…」。"""
        if self._scan_issue or not isinstance(count, int):
            return
        self._show_place_notice(f"正在解析 {count} 张照片的地点…")

    def _on_place_pass_done(self, payload: object) -> None:
        self._place_job = None
        self._clear_place_notice()
        result = payload if isinstance(payload, PlacePass) else PlacePass()
        logger.info("地点解析完成：扫过 %d 张，反查 %d 张", result.scanned, result.resolved)
        # 地点那一遍腾出了后台，检索索引那一遍接着起（见 _maybe_start_photo_index）。
        self._maybe_start_photo_index()

    def _on_place_pass_failed(self, message: str) -> None:
        self._place_job = None
        self._clear_place_notice()
        # 解析失败不影响浏览：分组与面板都会在自己要用的时候再试一次。
        logger.info("解析地点失败：%s", message)
        self._maybe_start_photo_index()

    def _show_place_notice(self, text: str) -> None:
        """进度消息**不许抢**横幅：上面正有别人的话就等它说完。

        抢了会怎样：用户刚丢掉三张照片，横幅上「已把 3 项移到废纸篓」被后台进度顶掉，
        那条确认就此消失。这条横幅是全局共享的，谁都能写 —— 所以后台的进度是
        **最没有资格**占着它的那一个。

        两道判据：上面已经有别人的话（比文字），或者刚刚有用户动作（比时间，
        见 :data:`BANNER_STICKY_SECONDS`）。后者能挡住「用户动作的那句话说完之后、
        进度才姗姗来迟」这种情形。前者由 :class:`~gallery.ui.window.notice.NoticeSlot`
        统一记账，与检索索引那一族、读元数据那一族共用同一份。
        """
        self._place_slot.show(text)

    def _clear_place_notice(self) -> None:
        """收掉自己那一条 —— **而且只在自己还占着的时候收**（理由见 ``NoticeSlot.clear``）。"""
        self._place_slot.clear()

    @property
    def _place_slot(self) -> NoticeSlot:
        return NoticeSlot(self, text_attr="_place_notice_text")

    def _cancel_place_pass(self) -> None:
        """作废在途的全库解析（切库、关窗时）。"""
        if self._place_job is not None:
            self._runner.cancel(self._place_job)
            self._place_job = None

    # ---- 会话（记在那个缓存目录里，下次启动沿用）----------------------------

    def _remember(self, root: Path) -> None:
        self._session = self._session.with_recent_root(root)
        self._welcome.set_recent(self._session.recent_roots)
        self._rebuild_recent_menu()
        self._save_session()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        roots = [root for root in self._session.recent_roots if root.is_dir()]
        if not roots:
            empty = QAction("（还没有）", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for root in roots:
            action = QAction(str(root), self)
            action.triggered.connect(lambda _checked=False, path=root: self.open_library(path))
            self._recent_menu.addAction(action)

    def _on_recent_requested(self, root: object) -> None:
        if isinstance(root, Path):
            self.open_library(root)

    def _save_session(self) -> None:
        geometry = self.geometry()
        self._session = self._session.with_window(
            WindowGeometry(
                x=geometry.x(),
                y=geometry.y(),
                width=geometry.width(),
                height=geometry.height(),
                maximized=self.isMaximized(),
            )
        )
        try:
            save_session(self._session, base=self._session_base)
        except OSError as exc:
            # 记不住窗口位置不值得打断用户。
            logger.debug("会话状态写入失败：%s", exc)


__all__ = ["LibraryFlowMixin"]
