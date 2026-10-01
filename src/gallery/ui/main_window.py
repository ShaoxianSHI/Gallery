"""主窗口：把库、后台任务与各个视图装配起来。

界面层的状态全部集中在这里，并且**显式传递**：没有全局单例，没有模块级可变状态，
所以打开两个库、或者在测试里构造两个窗口，都不会互相干扰。

三个「不许」在这里具体落地：

- 不自己读原图：一切像素都来自 ``gallery.core``（经 ``Library`` 门面）。
- 不阻塞主线程：扫描、预览、全分辨率解码都在 ``TaskRunner`` 的线程池里。
- 不偷偷写盘：唯一的写操作是用户在弹出的对话框里同意后的 ``prepare_library``。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QAction, QCloseEvent, QImage, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.config import prepare_library
from gallery.core.errors import GalleryError
from gallery.core.library import Library
from gallery.core.photos import FolderRef, GridEntry, PhotoRef
from gallery.core.scanner import ScanReport
from gallery.core.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    WindowGeometry,
    load_session,
    save_session,
)
from gallery.ui.breadcrumb import BreadcrumbBar
from gallery.ui.detail_view import DetailView
from gallery.ui.formatting import format_bytes, format_timestamp
from gallery.ui.grid_view import PREFETCH_MARGIN, PhotoGrid
from gallery.ui.imaging import to_qimage
from gallery.ui.models import PhotoListModel
from gallery.ui.navigation import breadcrumb_parts, parent_of
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumb_sizes import (
    THUMBNAIL_SIZES,
    describe,
    describe_actual,
    edge_for_index,
    label_for_index,
    nearest_index,
    shifted_index,
)
from gallery.ui.thumbnails import ThumbnailPipeline
from gallery.ui.welcome import WelcomeView

logger = logging.getLogger(__name__)

#: 扫描时每积累多少张就送回主线程一次（流式出图，不等整库扫完）。
SCAN_BATCH_SIZE = 64

#: 大图预览在内存里保留几张（当前张前后各一张，切图才不会白屏）。
PREVIEW_CACHE_SIZE = 3

#: 预览图相对视口长边的倍率：留出余量，缩放一点也不会糊。
PREVIEW_EDGE_FACTOR = 1.5

#: 预览图长边的上下限（像素）。
MIN_PREVIEW_EDGE = 512
MAX_PREVIEW_EDGE = 4096

_BANNER_STYLE = """
QLabel#banner {
    background: rgba(255, 176, 32, 0.16);
    border: 1px solid rgba(255, 176, 32, 0.45);
    border-radius: 6px;
    padding: 6px 10px;
    color: palette(text);
}
"""


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        *,
        session_base: Path | None = None,
        initial_library: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_base = session_base
        self._session: SessionState = load_session(base=session_base)
        self._runner = TaskRunner(self)

        self._library: Library | None = None
        self._pipeline: ThumbnailPipeline | None = None
        self._generation = 0
        self._scan_job: int | None = None
        self._detail_job: int | None = None
        self._current_row = -1
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._thumbnail_failures = 0

        self._size_index = nearest_index(self._session.thumbnail_edge)
        self._browse_mode: str = self._session.browse_mode or BROWSE_MODE_FLAT
        self._current_dir: Path | None = None
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        self._build_ui()
        self._build_actions()
        self._restore_session()
        self._sync_size_actions()
        self._update_action_states()

        if initial_library is not None:
            self.open_library(initial_library)
        elif self._session.recent_roots and self._session.recent_roots[0].is_dir():
            # README 承诺过：下次启动回到上次打开的库。静默打开，失败就留在欢迎页。
            self.open_library(self._session.recent_roots[0], silent=True)

    # ---- 构建界面 ----------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Gallery")
        self.resize(1280, 860)
        self.setMinimumSize(720, 480)

        self._welcome = WelcomeView()
        self._welcome.open_requested.connect(self.choose_folder)
        self._welcome.recent_requested.connect(self._on_recent_requested)

        self._grid = PhotoGrid(icon_edge=edge_for_index(self._size_index))
        self._grid.setModel(self._model)
        self._grid.activated_index.connect(self._on_entry_activated)
        self._grid.viewport_changed.connect(self._refresh_thumbnail_window)
        self._grid.size_step_requested.connect(self.step_thumbnail_size)
        self._grid.icon_edge_changed.connect(self._on_grid_edge_changed)

        self._banner = QLabel()
        self._banner.setObjectName("banner")
        self._banner.setWordWrap(True)
        self._banner.hide()

        self._breadcrumb = BreadcrumbBar()
        self._breadcrumb.path_requested.connect(self._on_breadcrumb_requested)

        self._grid_page = QWidget()
        self._grid_page.setStyleSheet(_BANNER_STYLE)
        grid_layout = QVBoxLayout(self._grid_page)
        grid_layout.setContentsMargins(10, 10, 10, 0)
        grid_layout.setSpacing(8)
        grid_layout.addWidget(self._breadcrumb)
        grid_layout.addWidget(self._banner)
        grid_layout.addWidget(self._grid, 1)

        self._detail = DetailView()
        self._detail.request_close.connect(self.show_grid)
        self._detail.request_step.connect(self.step_detail)
        self._detail.zoom_changed.connect(self._on_zoom_changed)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._grid_page)
        self._stack.addWidget(self._detail)
        self.setCentralWidget(self._stack)

        self._status_left = QLabel()
        self._status_right = QLabel()
        self.statusBar().addWidget(self._status_left)
        self.statusBar().addPermanentWidget(self._status_right)

    def _build_actions(self) -> None:
        self._open_action = QAction("打开文件夹…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self.choose_folder)

        self._back_action = QAction("返回网格", self)
        self._back_action.setShortcut(QKeySequence("Esc"))
        self._back_action.triggered.connect(self.show_grid)

        self._fit_action = QAction("适应窗口", self)
        self._fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self._fit_action.triggered.connect(self._detail.fit_to_window)

        self._actual_action = QAction("实际大小", self)
        self._actual_action.setShortcut(QKeySequence("Ctrl+1"))
        self._actual_action.triggered.connect(self._detail.actual_size)

        self._zoom_in_action = QAction("放大", self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(self._detail.zoom_in)

        self._zoom_out_action = QAction("缩小", self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(self._detail.zoom_out)

        self._up_action = QAction("上一级", self)
        self._up_action.setShortcut(QKeySequence("Ctrl+Up"))
        self._up_action.triggered.connect(self.go_up)

        self._albums_action = QAction("相册（按文件夹）", self)
        self._albums_action.setCheckable(True)
        self._albums_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_ALBUMS))

        self._flat_action = QAction("全部照片", self)
        self._flat_action.setCheckable(True)
        self._flat_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_FLAT))

        self._clear_cache_action = QAction("清空缩略图缓存", self)
        self._clear_cache_action.triggered.connect(self.clear_thumbnail_cache)

        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._quit_action.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        file_menu.addAction(self._clear_cache_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self._back_action)
        view_menu.addAction(self._up_action)
        view_menu.addSeparator()
        browse_menu = view_menu.addMenu("浏览方式")
        browse_menu.addAction(self._albums_action)
        browse_menu.addAction(self._flat_action)
        view_menu.addSeparator()
        view_menu.addAction(self._fit_action)
        view_menu.addAction(self._actual_action)
        view_menu.addSeparator()
        view_menu.addAction(self._zoom_in_action)
        view_menu.addAction(self._zoom_out_action)
        view_menu.addSeparator()
        self._size_actions: list[QAction] = []
        size_menu = view_menu.addMenu("缩略图大小")
        for index in range(len(THUMBNAIL_SIZES)):
            action = QAction(f"{label_for_index(index)}（约 {edge_for_index(index)} 像素）", self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, target=index: self.set_thumbnail_size(target)
            )
            size_menu.addAction(action)
            self._size_actions.append(action)

        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.addAction(self._open_action)
        toolbar.addAction(self._back_action)
        toolbar.addAction(self._up_action)
        toolbar.addSeparator()
        toolbar.addAction(self._fit_action)
        toolbar.addAction(self._actual_action)
        toolbar.addAction(self._zoom_in_action)
        toolbar.addAction(self._zoom_out_action)

        self._rebuild_recent_menu()

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
            "无论选哪个，Gallery 都不会移动、重命名或修改你的照片。"
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
        self._current_row = -1
        self._preview_cache.clear()
        self._model.clear()

        self._pipeline = ThumbnailPipeline(library, self._runner, self)
        self._pipeline.ready.connect(self._on_thumbnail_ready)
        self._pipeline.failed.connect(self._on_thumbnail_failed)

        self._banner.hide()
        # 切库时真正清空大图区，别让上一个库的全分辨率副本一直占着内存。
        self._detail.clear_image()
        self._current_dir = library.root
        self._update_breadcrumb()
        self._update_mode_actions()
        self.setWindowTitle(f"{library.display_name} — Gallery")
        self._stack.setCurrentWidget(self._grid_page)
        self._update_action_states()
        self._remember(library.root)
        self._start_scan()

    def _teardown_library(self) -> None:
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        if self._detail_job is not None:
            self._runner.cancel(self._detail_job)
            self._detail_job = None
        if self._pipeline is not None:
            self._pipeline.shutdown()
            self._pipeline.deleteLater()
            self._pipeline = None
        self._library = None

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        """按当前浏览方式列出内容：平铺模式递归整库，相册模式只列当前一层。"""
        library = self._library
        if library is None:
            return
        if self._browse_mode == BROWSE_MODE_ALBUMS:
            self._start_level_scan(library)
        else:
            self._start_flat_scan(library)

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

        self._set_status("正在读取这一层…")
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

        self._set_status("正在扫描…")
        self._scan_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        self._model.append(batch)
        albums, photos = self._model.counts()
        if self._browse_mode == BROWSE_MODE_ALBUMS:
            self._set_status(f"已发现 {albums} 个相册 · {photos} 张照片…")
        else:
            self._set_status(f"已发现 {photos} 张照片…")
        self._refresh_thumbnail_window()

    def _on_scan_done(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        report = payload if isinstance(payload, ScanReport) else ScanReport()
        albums, photos = self._model.counts()
        if albums or photos:
            if self._browse_mode == BROWSE_MODE_ALBUMS:
                self._set_status(f"{albums} 个相册 · {photos} 张照片")
            else:
                self._set_status(f"{photos} 张照片")
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._set_status("这个文件夹是空的")
        else:
            self._set_status("这个文件夹里没有找到支持的照片")

        if report.has_issues:
            self._show_banner(f"部分内容没能读取：{report.summary}")
        elif not albums and not photos:
            if self._browse_mode == BROWSE_MODE_ALBUMS:
                self._show_banner("这个文件夹里既没有子文件夹，也没有可显示的照片。")
            else:
                self._show_banner(
                    "没有找到可显示的照片。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP。"
                )
        self._refresh_thumbnail_window()

    def _on_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        self._set_status("扫描失败")
        self._show_banner(message)

    # ---- 缩略图 ------------------------------------------------------------

    def _refresh_thumbnail_window(self) -> None:
        if self._pipeline is None or self._stack.currentWidget() is not self._grid_page:
            return
        visible_rows = self._grid.visible_rows()
        prefetch_rows = self._grid.visible_rows(margin_ratio=PREFETCH_MARGIN)
        visible_set = set(visible_rows)
        visible = self._entries_missing_thumbnails(visible_rows)
        prefetch = self._entries_missing_thumbnails(
            [row for row in prefetch_rows if row not in visible_set]
        )
        self._pipeline.set_window(visible, prefetch)

    def _entries_missing_thumbnails(self, rows: Sequence[int]) -> list[GridEntry]:
        """只挑出需要取缩略图的条目（相册算它的封面）。

        两种情况：手里根本没有，或者手里那张是按旧尺寸缩放的。滚动会让可见窗口
        反复变化，如果每次都把可见项重新要一遍，就等于每滚一下都把磁盘缓存重读
        重缩放一次，内存里的 LRU 也形同虚设。
        """
        entries: list[GridEntry] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is not None and self._model.needs_thumbnail(entry.path):
                entries.append(entry)
        return entries

    def _on_thumbnail_ready(self, path: object, image: object) -> None:
        if isinstance(path, Path) and isinstance(image, QImage):
            self._model.set_thumbnail(path, image)

    # ---- 缩略图尺寸 --------------------------------------------------------

    def set_thumbnail_size(self, index: int) -> None:
        """直接切到某一档（菜单用）。"""
        if index == self._size_index:
            return
        self._size_index = index
        self._apply_thumbnail_size()
        self.statusBar().showMessage(describe(self._size_index), 1500)
        self._remember_thumbnail_size()

    def step_thumbnail_size(self, step: int) -> None:
        """按档位加减一档（捏合或 ⌘+滚轮用）。"""
        target = shifted_index(self._size_index, step)
        if target == self._size_index:
            limit = "已经是最小尺寸" if step < 0 else "已经是最大尺寸"
            self.statusBar().showMessage(limit, 1200)
            return
        self.set_thumbnail_size(target)

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
        if self._grid.isVisible():
            self.statusBar().showMessage(describe_actual(self._size_index, edge), 1500)

    def _sync_size_actions(self) -> None:
        for index, action in enumerate(self._size_actions):
            action.setChecked(index == self._size_index)

    def _remember_thumbnail_size(self) -> None:
        self._session = self._session.with_thumbnail_edge(edge_for_index(self._size_index))
        self._save_session()

    def _on_thumbnail_failed(self, path: object, message: str) -> None:
        self._thumbnail_failures += 1
        logger.info("缩略图失败：%s", message)
        self._show_banner(f"有 {self._thumbnail_failures} 张照片无法显示缩略图：{message}")

    # ---- 大图 --------------------------------------------------------------

    def _on_entry_activated(self, index: QModelIndex) -> None:
        """双击一个格子：相册就进去，照片就打开大图。"""
        entry = self._model.entry_at(index.row())
        if isinstance(entry, FolderRef):
            self.enter_folder(entry.path)
        else:
            self._open_detail(index)

    def _open_detail(self, index: QModelIndex) -> None:
        row = index.row()
        if self._model.photo_at(row) is None:
            return
        self._current_row = row
        self._stack.setCurrentWidget(self._detail)
        self._detail.setFocus()
        self._update_action_states()
        self._load_detail(row)

    def show_grid(self) -> None:
        """回到网格，并让当前照片保持选中。"""
        if self._stack.currentWidget() is self._grid_page:
            return
        self._stack.setCurrentWidget(self._grid_page)
        if self._current_row >= 0:
            self._grid.select_row(self._current_row)
        # 缩放百分比只在大图下有意义，回到网格就清掉。
        self._status_right.clear()
        self._update_action_states()
        self._refresh_thumbnail_window()

    def step_detail(self, delta: int) -> None:
        """上一张 / 下一张。相册模式下会跳过相册格子。"""
        row = self._neighbour_photo_row(self._current_row, delta)
        if row is None:
            return
        self._current_row = row
        self._load_detail(row)

    def _neighbour_photo_row(self, start: int, delta: int) -> int | None:
        row = start + delta
        while 0 <= row < self._model.rowCount():
            if self._model.photo_at(row) is not None:
                return row
            row += delta
        return None

    def _load_detail(self, row: int) -> None:
        library = self._library
        ref = self._model.photo_at(row)
        if library is None or ref is None:
            return
        generation = self._generation
        self._set_status(
            f"{ref.name} · {format_bytes(ref.size)} · {format_timestamp(ref.mtime_ns)}"
        )

        if self._detail_job is not None:
            # 上一张的解码已经没有意义了，早点让它停下来。
            self._runner.cancel(self._detail_job)
            self._detail_job = None

        cached = self._preview_cache.get(ref.path)
        if cached is not None:
            self._preview_cache.move_to_end(ref.path)
            self._detail.set_image(cached, refit=True)
            self._request_full(ref, row, generation)
            return

        self._detail.show_loading()
        max_edge = self._preview_edge()

        def work_preview(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return to_qimage(library.preview(ref, max_edge=max_edge))

        self._detail_job = self._runner.submit(
            work_preview,
            on_result=partial(self._on_preview_ready, ref, row, generation),
            on_error=partial(self._on_detail_failed, ref, row, generation),
        )
        self._prefetch_neighbours(row)

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
            return to_qimage(library.full(ref))

        self._detail_job = self._runner.submit(
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
        self._set_status(ref.name, right="无法显示")

    def _prefetch_neighbours(self, row: int) -> None:
        """预取前后各一张的预览，让连续翻图不白屏。"""
        library = self._library
        if library is None:
            return
        generation = self._generation
        max_edge = self._preview_edge()
        for neighbour in (row - 1, row + 1):
            ref = self._model.photo_at(neighbour)
            if ref is None or ref.path in self._preview_cache:
                continue

            def work(context: TaskContext, target: PhotoRef = ref) -> object:
                if context.is_cancelled():
                    return None
                return to_qimage(library.preview(target, max_edge=max_edge))

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

    # ---- 状态与提示 --------------------------------------------------------

    def _set_status(self, left: str, right: str | None = None) -> None:
        self._status_left.setText(left)
        if right is not None:
            self._status_right.setText(right)

    def _show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.show()

    def _report_error(self, message: str) -> None:
        QMessageBox.critical(self, "Gallery", message)

    def _on_zoom_changed(self, scale: float) -> None:
        if self._stack.currentWidget() is self._detail:
            width, height = self._detail.image_size
            if width and height:
                self._status_right.setText(f"{width} × {height} · {scale * 100:.0f}%")

    def clear_thumbnail_cache(self) -> None:
        """清空当前库的缩略图缓存（只删缓存目录里的文件）。"""
        if self._library is None:
            return
        answer = QMessageBox.question(
            self,
            "清空缩略图缓存",
            f"将删除「{self._library.display_name}」的缩略图缓存。\n"
            "照片本身不受影响，缓存会在下次浏览时自动重建。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self._library.clear_cache()
        self._model.clear_thumbnails()
        self._refresh_thumbnail_window()
        self._show_banner(f"已清除 {removed} 个缩略图缓存文件。")

    def _update_action_states(self) -> None:
        self._update_navigation_actions()
        in_detail = self._stack.currentWidget() is self._detail
        for action in (
            self._back_action,
            self._fit_action,
            self._actual_action,
            self._zoom_in_action,
            self._zoom_out_action,
        ):
            action.setEnabled(in_detail)
        self._clear_cache_action.setEnabled(self._library is not None)

    # ---- 浏览方式与相册导航 ------------------------------------------------

    def set_browse_mode(self, mode: str) -> None:
        """切换「相册（逐层进入）」与「全部照片（整库平铺）」。"""
        if mode == self._browse_mode:
            self._update_mode_actions()
            return
        self._browse_mode = mode
        self._session = self._session.with_browse_mode(mode)
        self._save_session()
        self._update_mode_actions()
        if self._library is not None:
            self._reload_level()

    def enter_folder(self, directory: Path) -> None:
        """进入一个相册（子文件夹）。库以外的路径一律拒绝。"""
        library = self._library
        if library is None:
            return
        target = Path(directory)
        if not paths.is_within(library.root, target):
            return
        self._current_dir = target
        self._reload_level()

    def go_up(self) -> None:
        """回到上一层；已经在库根就什么也不做。"""
        library = self._library
        if library is None or self._browse_mode != BROWSE_MODE_ALBUMS:
            return
        current = self._current_dir or library.root
        parent = parent_of(library.root, current)
        if parent is not None:
            self.enter_folder(parent)

    def _on_breadcrumb_requested(self, directory: object) -> None:
        if isinstance(directory, Path):
            self.enter_folder(directory)

    def _reload_level(self) -> None:
        """重新列出当前层（切模式、进入或返回相册时都走这里）。"""
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        # 换代：让已经在路上的旧结果作废。
        self._generation += 1
        self._model.clear()
        self._preview_cache.clear()
        self._current_row = -1
        self._detail.clear_image()
        self._banner.hide()
        self._stack.setCurrentWidget(self._grid_page)
        self._update_breadcrumb()
        self._update_action_states()
        self._start_scan()

    def _update_breadcrumb(self) -> None:
        library = self._library
        if library is None or self._browse_mode != BROWSE_MODE_ALBUMS:
            self._breadcrumb.hide()
            return
        self._breadcrumb.set_path(breadcrumb_parts(library.root, self._current_dir or library.root))
        self._breadcrumb.show()

    def _update_mode_actions(self) -> None:
        albums = self._browse_mode == BROWSE_MODE_ALBUMS
        self._albums_action.setChecked(albums)
        self._flat_action.setChecked(not albums)

    def _update_navigation_actions(self) -> None:
        library = self._library
        can_go_up = (
            library is not None
            and self._browse_mode == BROWSE_MODE_ALBUMS
            and parent_of(library.root, self._current_dir or library.root) is not None
        )
        self._up_action.setEnabled(can_go_up)

    # ---- 会话 --------------------------------------------------------------

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

    # ---- 关闭 --------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_session()
        self._teardown_library()
        self._runner.cancel_all()
        if not self._runner.wait_for_done():
            logger.warning("仍有后台任务未结束，已放弃等待")
        super().closeEvent(event)


__all__ = ["MainWindow"]
