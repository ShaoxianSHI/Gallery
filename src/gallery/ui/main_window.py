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

from PySide6.QtCore import QModelIndex, QPointF, Qt
from PySide6.QtGui import QAction, QCloseEvent, QImage, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.config import prepare_library
from gallery.core.errors import GalleryError, GeoDataError
from gallery.core.geonames import PlaceIndex, download_data, geo_data_dir
from gallery.core.library import Library
from gallery.core.metadata import MetadataReport, read_metadata
from gallery.core.photos import FolderRef, GridEntry, PhotoRef
from gallery.core.rename import RenameResult
from gallery.core.scanner import ScanReport
from gallery.core.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    WindowGeometry,
    load_session,
    save_session,
)
from gallery.ui import theme
from gallery.ui.breadcrumb import BreadcrumbBar
from gallery.ui.detail_view import DetailView
from gallery.ui.grid_view import PREFETCH_MARGIN, PhotoGrid
from gallery.ui.imaging import to_qimage
from gallery.ui.metadata_panel import MetadataPanel
from gallery.ui.models import PhotoListModel
from gallery.ui.navigation import breadcrumb_parts, parent_of
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumb_sizes import (
    THUMBNAIL_SIZES,
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

#: 元数据缓存条数。翻图时命中缓存就直接显示，没有中间态。
METADATA_CACHE_SIZE = 128

#: 预读前后各几张的元数据。一次读取约 50ms，预读了就等于切图时零等待。
METADATA_PREFETCH_RADIUS = 2

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


def _rename_banner(result: RenameResult) -> str:
    """改名之后的那行提示。

    记账失败必须说出来：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来，
    让他以为「随时能撤销」比不告诉他更糟。
    """
    note = "" if result.journaled else "，但没能记下操作日志，⌘Z 撤销不了"
    return f"已重命名为「{result.entry.name}」{note}。"


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
        self._metadata_job: int | None = None
        self._place_index: PlaceIndex | None = None
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]] = OrderedDict()
        self._thumbnail_failures = 0

        self._size_index = nearest_index(self._session.thumbnail_edge)
        self._browse_mode: str = self._session.browse_mode or BROWSE_MODE_FLAT
        self._current_dir: Path | None = None
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        self._build_ui()
        self._build_actions()
        self._restore_session()
        self._metadata_action.setChecked(bool(self._session.metadata_visible))
        self._panel.setVisible(bool(self._session.metadata_visible))
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

        # 先把底色统一好，下面每一个控件都从这一份取色：标题栏那一段、面包屑、网格视口、
        # 大图区与元数据面板从此是同一块底色（见 ui.theme）。
        theme.ensure_installed()
        # 标题栏那一段交给窗口自己画，否则 macOS 会铺一层半透明材质，和下面的底色差一档。
        theme.unify_title_bar(self)

        self._welcome = WelcomeView()
        self._welcome.open_requested.connect(self.choose_folder)
        self._welcome.recent_requested.connect(self._on_recent_requested)

        self._grid = PhotoGrid(icon_edge=edge_for_index(self._size_index))
        self._grid.setModel(self._model)
        self._grid.activated_index.connect(self._on_entry_activated)
        self._grid.viewport_changed.connect(self._refresh_thumbnail_window)
        self._grid.size_step_requested.connect(self.step_thumbnail_size)
        self._grid.icon_edge_changed.connect(self._on_grid_edge_changed)
        self._grid.rename_requested.connect(self._on_rename_requested)
        self._grid.rename_committed.connect(self._on_rename_committed)
        self._grid.rename_active_changed.connect(self._on_rename_active_changed)
        # 「重命名」能不能用，取决于当前有没有选中一个格子。
        selection_model = self._grid.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(
                lambda _current, _previous: self._update_action_states()
            )

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

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._grid_page)
        self._panel = MetadataPanel()
        self._panel.hide()
        self._detail_page = QWidget()
        detail_layout = QHBoxLayout(self._detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_layout.addWidget(self._detail, 1)
        detail_layout.addWidget(self._panel)
        self._stack.addWidget(self._detail_page)
        self.setCentralWidget(self._stack)
        # 刻意不建状态栏：底部那一条只是为了放扫描进度、照片尺寸、缩放百分比，
        # 换来的是照片区少 20 来像素。出错信息有横幅，提示性信息一律不留。

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

        # 回上一层用 Esc：⌘↑ 在 macOS 上已经被占着，我们抢不到。
        # Esc 在大图页是「返回网格」，在网格页才是「上一级」——同一时刻只有一个主人
        # （见 _update_navigation_actions），两处不会互相抢键。
        self._up_action = QAction("上一级", self)
        self._up_action.setShortcut(QKeySequence("Esc"))
        self._up_action.triggered.connect(self.go_up)

        self._albums_action = QAction("相册（按文件夹）", self)
        self._albums_action.setCheckable(True)
        self._albums_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_ALBUMS))

        self._flat_action = QAction("全部照片", self)
        self._flat_action.setCheckable(True)
        self._flat_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_FLAT))

        self._metadata_action = QAction("显示元数据信息", self)
        self._metadata_action.setCheckable(True)
        self._metadata_action.setShortcut(QKeySequence("Ctrl+I"))
        self._metadata_action.toggled.connect(self.toggle_metadata)

        self._clear_cache_action = QAction("清空缩略图缓存", self)
        self._clear_cache_action.triggered.connect(self.clear_thumbnail_cache)

        self._rename_action = QAction("重命名…", self)
        self._rename_action.setShortcut(QKeySequence("Ctrl+R"))
        self._rename_action.triggered.connect(self.rename_current_entry)

        self._undo_rename_action = QAction("撤销重命名", self)
        self._undo_rename_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_rename_action.triggered.connect(self.undo_rename)

        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._quit_action.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        file_menu.addAction(self._rename_action)
        file_menu.addAction(self._undo_rename_action)
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
        view_menu.addAction(self._metadata_action)
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

        # 不加工具栏：这些命令在 macOS 顶部菜单里都有，快捷键也照常生效，
        # 界面上再重复一遍只会挤掉照片。
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
            "无论选哪个，Gallery 都不会移动或修改你的照片；"
            "唯一会改到文件名的操作，是你自己触发的那次重命名。"
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
        self._metadata_cache.clear()
        self._model.clear()

        self._pipeline = ThumbnailPipeline(library, self._runner, self)
        self._pipeline.ready.connect(self._on_thumbnail_ready)
        self._pipeline.failed.connect(self._on_thumbnail_failed)

        self._banner.hide()
        # 切库时真正清空大图区，别让上一个库的全分辨率副本一直占着内存。
        self._detail.clear_image()
        self._panel.clear()
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

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        self._model.append(batch)
        self._refresh_thumbnail_window()

    def _on_scan_done(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        report = payload if isinstance(payload, ScanReport) else ScanReport()
        albums, photos = self._model.counts()
        # 「扫了多少张」原本在状态条上，条子没了就不报了；出问题才值得打断用户。
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
        self._show_banner(f"扫描失败：{message}")

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
        self._remember_thumbnail_size()

    def step_thumbnail_size(self, step: int) -> None:
        """按档位加减一档（捏合或 ⌘+滚轮用）。"""
        target = shifted_index(self._size_index, step)
        if target == self._size_index:
            return  # 已经在头尾：以前会提示「已经是最大尺寸」，随状态条一起去掉
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

    def toggle_metadata(self, visible: bool) -> None:
        """⌘I：展开 / 收起右侧的元数据面板，并把选择记进会话。"""
        self._panel.setVisible(visible)
        self._session = self._session.with_metadata_visible(visible)
        self._save_session()
        if visible:
            self._load_metadata()
        else:
            self._cancel_metadata_job()

    def _cancel_metadata_job(self) -> None:
        if self._metadata_job is not None:
            self._runner.cancel(self._metadata_job)
            self._metadata_job = None

    def _load_metadata(self) -> None:
        """读当前这张照片的元数据；面板没展开就什么都不做。

        命中缓存就同步显示（真正的「直接显示」）；没命中就老实说「正在读取」，
        **不要把上一张的元数据留在屏幕上** —— 那是错的照片的元数据，用户会当成
        当前这张的信息看，比多等 50ms 糟糕得多。
        """
        self._cancel_metadata_job()
        if not self._panel.isVisible():
            return
        row = self._current_row
        ref = self._model.photo_at(row)
        if ref is None:
            self._panel.clear()
            return
        generation = self._generation
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._panel.set_report(cached)
            self._prefetch_metadata(row)
            # 缓存里的报告可能还没推断过地点（预读只读元数据）：命中缓存不等于
            # 「地点已经查过」，漏了这一步，带坐标的照片会永远不显示地点。
            self._maybe_lookup_place(ref, cached, generation, announce=False)
            return
        # 未命中：先说在读，不要让上一张的元数据冒充当前这张。
        self._panel.show_loading(ref.name)

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._metadata_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_ready, row, generation),
            on_error=partial(self._on_metadata_failed, row, generation),
        )

    def _on_metadata_ready(self, row: int, generation: int, payload: object) -> None:
        if not isinstance(payload, MetadataReport):
            return
        ref = self._model.photo_at(row)
        if ref is not None:
            self._cache_metadata(ref, payload)
        if generation != self._generation or row != self._current_row:
            return  # 用户已经翻到别处了，这份结果只进缓存，不上屏
        self._panel.set_report(payload)
        self._prefetch_metadata(row)
        self._maybe_lookup_place(ref, payload, generation, announce=True)

    def _on_metadata_failed(self, row: int, generation: int, message: str) -> None:
        if generation != self._generation or row != self._current_row:
            return
        self._panel.show_error(message)

    def _maybe_lookup_place(
        self,
        ref: PhotoRef,
        report: MetadataReport,
        generation: int,
        *,
        announce: bool,
    ) -> None:
        """按坐标推断最近的 city，结果一律入缓存。

        ``announce`` 为真时才在面板上显示中间状态（「正在查询…」「正在下载…」）。
        预读与缓存命中都传 ``False``：那时候面板上显示的是当前这张照片，
        拿别的照片的中间状态去替换它，用户只会看到内容闪一下。
        """
        if report.latitude is None or report.longitude is None:
            return
        if report.place_from_file or report.place_resolved:
            return  # 文件里写了地点，或已经推断过一次（含「没有记录」这种结论）
        if not self._panel.isVisible():
            return
        if announce:
            self._panel.set_report(report.with_place("正在查询最近的城市…"))
        latitude, longitude = report.latitude, report.longitude
        index = self._place_index

        def work(context: TaskContext) -> object:
            directory = geo_data_dir()
            if not PlaceIndex.data_present(directory):
                context.progress("正在下载城市数据（约 3.3 MB）…")
                download_data(directory)
            loaded = index if index is not None else PlaceIndex.load(directory)
            if loaded is None:
                raise GeoDataError("城市数据不可用，无法按坐标推断地点。")
            return (loaded, loaded.nearest(latitude, longitude))

        # 不取消进行中的地点查询：它只往缓存里写，切走了也仍然有用，
        # 切回来时反而能一次显示到位。
        self._runner.submit(
            work,
            on_progress=partial(self._on_place_progress, ref, report, generation),
            on_result=partial(self._on_place_ready, ref, report, generation),
            on_error=partial(self._on_place_failed, ref, report, generation),
        )

    def _is_current_photo(self, ref: PhotoRef, generation: int) -> bool:
        """这张照片是不是当前显示的那张（换代过就不算）。"""
        if generation != self._generation:
            return False
        current = self._model.photo_at(self._current_row)
        return current is not None and current.path == ref.path

    def _on_place_progress(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        if isinstance(payload, str) and self._is_current_photo(ref, generation):
            self._panel.set_report(report.with_place(payload))

    def _on_place_ready(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        index, place = payload
        if isinstance(index, PlaceIndex):
            self._place_index = index
        if place is None:
            self._place_index_saved(ref, report, generation, "坐标附近没有城市记录")
            return
        # 标签本身已经写了「（推断）」，内容里不再重复；只有偏得远时才补一句距离，
        # 那是在提醒「这只是最近的城市」，不是在报误差。
        suffix = "" if place.distance_km < 50 else f"（最近城市约 {place.distance_km:.0f} 公里外）"
        self._place_index_saved(ref, report, generation, f"{place.label()}{suffix}")

    def _place_index_saved(
        self, ref: PhotoRef, report: MetadataReport, generation: int, text: str
    ) -> None:
        """推断结果入缓存，并且只在这张正是当前照片时才上屏。"""
        enriched = report.with_place(text)
        self._cache_metadata(ref, enriched)
        if self._is_current_photo(ref, generation):
            self._panel.set_report(enriched)

    def _on_place_failed(
        self, ref: PhotoRef, report: MetadataReport, generation: int, message: str
    ) -> None:
        self._place_index_saved(ref, report, generation, f"未能推断地点：{message}")

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
        self._stack.setCurrentWidget(self._detail_page)
        self._detail.setFocus()
        self._update_action_states()
        self._load_detail(row)

    # ---- 重命名（唯一会改动用户文件的入口）----------------------------------

    def rename_current_entry(self) -> None:
        """对当前选中的格子开始重命名（菜单 / ⌘R 走这里）。"""
        index = self._grid.currentIndex()
        if index.isValid():
            self._grid.begin_rename(index)

    def _on_rename_requested(self, index: QModelIndex) -> None:
        """再次点击已选中的格子：进入行内重命名。"""
        self._grid.begin_rename(index)

    def _on_rename_active_changed(self, _active: bool) -> None:
        """改名的这段时间里 Esc 是「取消改名」，菜单里的「上一级」也跟着让开。"""
        self._update_action_states()

    def _on_rename_committed(self, row: int, text: str) -> None:
        entry = self._model.entry_at(row)
        library = self._library
        if entry is None or library is None:
            return
        try:
            result = library.rename_entry(entry, text)
        except GalleryError as exc:
            logger.info("重命名失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._apply_rename(result, row)

    def undo_rename(self) -> None:
        """撤销最近一次改名（⌘Z）。"""
        library = self._library
        if library is None:
            return
        try:
            result = library.undo_last_rename()
        except GalleryError as exc:
            logger.info("撤销重命名失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        if result is None:
            self._show_banner("没有可撤销的改名。")
            return
        # 撤销之前，这一行挂在改完名的新路径上 —— 要按它找行号。
        row = self._model.row_of_path(result.previous.path)
        self._apply_rename(result, row, banner=f"已撤销，恢复为「{result.entry.name}」。")

    def _apply_rename(self, result: RenameResult, row: int, *, banner: str | None = None) -> None:
        """把一次改名（或撤销）同步进模型与各级缓存。"""
        stale = result.previous.path
        self._preview_cache.pop(stale, None)
        self._metadata_cache.pop(stale, None)
        if not result.renamed:
            self._grid.select_row(row, ensure_visible=False)
            return
        if row >= 0 and self._model.replace_entry(row, result.entry):
            self._grid.select_row(row, ensure_visible=False)
        else:
            self._reload_level()
        self._show_banner(banner or _rename_banner(result))

    def show_grid(self) -> None:
        """回到网格，并让当前照片保持选中。"""
        if self._stack.currentWidget() is self._grid_page:
            return
        # 回到网格，把悬停提示还回去
        self._model.set_tooltips_enabled(True)
        QToolTip.hideText()  # 同 _load_detail：大图上的提示不该挂到网格上
        self._stack.setCurrentWidget(self._grid_page)
        if self._current_row >= 0:
            self._grid.select_row(self._current_row)
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
        # 网格卡片的悬停提示是一个**顶层窗口**（macOS 上白底、正好压在照片的左上角，
        # 内容是文件名与尺寸等元数据）。切图时它会闪，甚至闪两次：
        #
        #   1. 已经弹出来的那一个 —— ``hideText()`` 收掉；
        #   2. 还没到显示时间的那一个 —— Qt 已经排好了定时器，而 ``hideText()``
        #      **只隐藏窗口、不取消定时器**，它照样会在切换过程中弹出来。
        #
        # 所以要连根拔：让网格不再提供提示内容（PySide6 没有暴露
        # QWidget.setToolTipsEnabled，只能从数据侧断掉），再收当前窗口。
        # 定时器醒来问网格要提示，拿到空的，就什么都不会显示。
        self._model.set_tooltips_enabled(False)
        QToolTip.hideText()
        generation = self._generation

        if self._detail_job is not None:
            # 上一张的解码已经没有意义了，早点让它停下来。
            self._runner.cancel(self._detail_job)
            self._detail_job = None
        if self._panel.isVisible():
            self._load_metadata()

        cached = self._preview_cache.get(ref.path)
        if cached is not None:
            self._preview_cache.move_to_end(ref.path)
            self._detail.set_image(cached, refit=True)
            self._request_full(ref, row, generation)
            # 命中也必须继续预读前后：只预读未命中的那一张的话，连续翻图会变成
            # 「命中 → 不预读 → 下次未命中 → 预读 → 命中」地交替，每隔一张就
            # 先闪一下缩略图。命中与未命中应当对缓存的处理完全一致。
            self._prefetch_neighbours(row)
            return

        self._detail.prepare_switch()
        # 画面上此刻还留着**上一张**照片。留着它，用户会以为点错了文件 ——
        # 看到的是错的照片，比短暂的黑屏更糟。先用这张照片自己的缩略图顶上：
        # 尺寸小一点，但至少是对的。
        thumbnail = self._model.thumbnail_pixmap(ref.path)
        if thumbnail is not None:
            self._detail.set_image(thumbnail.toImage(), refit=True)
        else:
            self._detail.clear_image()
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
        # 原因直接画在大图区里（show_error），不需要状态条再报一遍。
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

        一次读取约 50ms：预读了，切过去就是 0ms；不预读则每次切图都要等，
        只能靠「保留旧内容」顶着 —— 那会把上一张的地点显示给当前照片，是错数据。
        """
        if not self._panel.isVisible():
            return
        for offset in range(-METADATA_PREFETCH_RADIUS, METADATA_PREFETCH_RADIUS + 1):
            if offset == 0:
                continue
            ref = self._model.photo_at(row + offset)
            if ref is None or self._cached_metadata(ref) is not None:
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

        地点必须在这里查完：否则切到这张照片时缓存里只有坐标，面板要先显示一遍
        没有地点的版本、再被反查结果替换 —— 那正是「切换照片时地点闪一下」的来源。
        入缓存的应当是可以一次显示到位的完整报告。
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

    # ---- 提示 --------------------------------------------------------------

    def _show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.show()

    def _report_error(self, message: str) -> None:
        QMessageBox.critical(self, "Gallery", message)

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
        in_detail = self._stack.currentWidget() is self._detail_page
        for action in (
            self._back_action,
            self._fit_action,
            self._actual_action,
            self._zoom_in_action,
            self._zoom_out_action,
        ):
            action.setEnabled(in_detail)
        self._clear_cache_action.setEnabled(self._library is not None)
        # 重命名只对网格里的一个格子有意义：大图页、没选中东西时都不该可用。
        in_grid = self._stack.currentWidget() is self._grid_page
        self._rename_action.setEnabled(in_grid and self._grid.currentIndex().isValid())

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
        self._grid.cancel_rename()
        self._model.clear()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._current_row = -1
        self._detail.clear_image()
        self._panel.clear()
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
        # 「上一级」只在网格页启用：大图页的 Esc 是「返回网格」，两个动作共用一个 Esc，
        # 必须有一个是关着的，否则同一按键落到谁身上说不准。改名的过程中也不启用 ——
        # 那时 Esc 是输入框的「取消改名」（输入框还会先抢一次按键，见 RenameEdit.event）。
        in_grid = self._stack.currentWidget() is self._grid_page
        can_go_up = (
            in_grid
            and not self._grid.is_renaming()
            and library is not None
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

    # ---- 窗口 --------------------------------------------------------------

    def _is_title_bar(self, point: QPointF) -> bool:
        """这一点是不是落在标题栏那一段（窗口顶边与内容区之间）。"""
        central = self.centralWidget()
        return central is not None and point.y() < central.geometry().top()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """按住标题栏那一段拖动窗口。

        内容铺到标题栏底下之后（见 ``ui.theme.unify_title_bar``），那一条已经是我们的
        客户区，系统不再代劳拖动，得自己发起。落在标题栏里的按下就交给 ``startSystemMove``；
        它返回假（平台不支持、或系统其实自己会拖）就照常往下传，不影响别处的点击。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._is_title_bar(event.position()):
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    # ---- 关闭 --------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_session()
        self._teardown_library()
        self._runner.cancel_all()
        if not self._runner.wait_for_done():
            logger.warning("仍有后台任务未结束，已放弃等待")
        super().closeEvent(event)


__all__ = ["MainWindow"]
