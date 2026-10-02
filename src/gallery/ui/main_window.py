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
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, Qt
from PySide6.QtGui import QAction, QCloseEvent, QImage, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.errors import GalleryError, GeoDataError
from gallery.core.library.config import prepare_library
from gallery.core.library.facade import Library
from gallery.core.library.groups import SortOrder, build_groups, sort_photos
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import ScanReport
from gallery.core.library.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    WindowGeometry,
    load_session,
    save_session,
)
from gallery.core.metadata.exif import ExifToolReader, MetadataReport, read_metadata
from gallery.core.metadata.facets import (
    GROUP_FACETS,
    SORT_FACETS,
    Facet,
    PhotoFacets,
    group_facet,
    needs_metadata,
    sort_facet,
)
from gallery.core.metadata.journal import ACTION_RENAME
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.trash import Failure, TrashResult
from gallery.core.metadata.undo import UndoResult
from gallery.core.places.geonames import PlaceIndex, download_data, geo_data_dir
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui import theme
from gallery.ui.browser.grid import PREFETCH_MARGIN, PhotoGrid
from gallery.ui.browser.model import PhotoListModel
from gallery.ui.browser.thumb_sizes import (
    THUMBNAIL_SIZES,
    edge_for_index,
    label_for_index,
    nearest_index,
    shifted_index,
)
from gallery.ui.imaging import to_qimage
from gallery.ui.navigation import breadcrumb_parts, parent_of
from gallery.ui.panels.breadcrumb import BreadcrumbBar
from gallery.ui.panels.metadata import MetadataPanel
from gallery.ui.panels.welcome import WelcomeView
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.viewer.detail import DetailView

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


#: 选中分组卡片时对「改名 / 删除」的统一解释。两件事共用一句话，省得用户以为是两个毛病。
_GROUP_NOT_FILE_ACTION = (
    "分组不是磁盘上的文件，改不了名也删不掉。双击进入分组后选中里面的照片再操作。"
)

#: 双击了空白处却已经退不动时的那句话。什么都不做等于「按了没反应」。
_NO_PARENT_NOTICE = "已经是最外面一层了，没有上一级可退。"


def _rename_banner(result: RenameResult) -> str:
    """改名之后的那行提示。

    记账失败必须说出来：这时 ⌘Z 撤销不了，用户只能自己去访达里改回来，
    让他以为「随时能撤销」比不告诉他更糟。
    """
    note = "" if result.journaled else "，但没能记下操作日志，⌘Z 撤销不了"
    return f"已重命名为「{result.entry.name}」{note}。"


def _undo_note(journaled: bool) -> str:
    return "" if journaled else "，但没能记下操作日志"


def _trash_banner(result: TrashResult) -> str:
    """移到废纸篓之后的那行提示。

    三件事必须说清楚：动了多少、能不能撤销、没成的那些为什么没成。
    废纸篓本身是第二层保险，但用户不该靠猜。
    """
    moved = result.moved
    if not moved:
        return f"没有移走任何东西：{_first_reason(result.failures)}{_undo_note(result.journaled)}。"
    if any(isinstance(entry, FolderRef) for entry in moved):
        head = f"已把相册「{_album_name(moved)}」和里面的内容移到废纸篓"
    elif len(moved) == 1:
        head = f"已把「{moved[0].name}」移到废纸篓"
    else:
        head = f"已把 {len(moved)} 项移到废纸篓"
    if result.failures:
        head += f"，但 {len(result.failures)} 项没移走：{_first_reason(result.failures)}"
    # 不能一边说「⌘Z 可以撤销」一边说「撤销不了」，所以这两半是一起给的。
    hint = "⌘Z 可以撤销。" if result.journaled else "⌘Z 撤销不了：没能记下操作日志。"
    return f"{head}。{hint}"


def _undo_banner(result: UndoResult) -> str:
    """撤销之后的那行提示。"""
    note = _undo_note(result.journaled)
    if result.action == ACTION_RENAME:
        return f"已撤销，恢复为「{result.entries[0].name}」{note}。"
    restored = result.entries
    if not restored:
        return f"没能取回来：{_first_reason(result.failures)}{note}。"
    if len(restored) == 1:
        head = f"已从废纸篓取回「{restored[0].name}」"
    else:
        head = f"已从废纸篓取回 {len(restored)} 项"
    if result.failures:
        head += f"，但 {len(result.failures)} 项没取回来：{_first_reason(result.failures)}"
    return f"{head}{note}。"


def _album_name(entries: Sequence[GridEntry]) -> str:
    """一批条目里的相册名（可能混着照片，取第一个相册的）。"""
    for entry in entries:
        if isinstance(entry, FolderRef):
            return entry.display_name
    return ""


def _first_reason(failures: Sequence[Failure]) -> str:
    """失败原因的摘要。

    只有一项时要点名道姓 —— 用户得知道该去看哪张照片；多项时横幅只有一行容身之地，
    只给第一条，完整列表在日志里。
    """
    if not failures:
        return "原因不明"
    head = f"「{failures[0].name}」" if len(failures) == 1 else "其中一项"
    return f"{head}：{failures[0].message.rstrip('。')}"


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
        #: 分组 / 排序用的地点反查器（按坐标）。与元数据面板共用同一份城市数据。
        self._place_resolver: LazyPlaceResolver | None = None
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]] = OrderedDict()
        self._thumbnail_failures = 0
        # 下一次 ⌘Z 会撤掉什么。日志只有写操作才会变，所以只在这几处刷新它 ——
        # 每次选中变化都重放一遍日志的话，用方向键翻图时白白读文件。
        self._undo_label: str | None = None

        self._size_index = nearest_index(self._session.thumbnail_edge)
        self._browse_mode: str = self._session.browse_mode or BROWSE_MODE_FLAT
        self._current_dir: Path | None = None
        # 分组 / 排序（见 ADR 0009）：字段 id 存进会话，下次打开沿用。
        self._group_facet: str | None = self._session.group_facet
        self._sort_facet: str | None = self._session.sort_facet
        self._sort_descending: bool = bool(self._session.sort_descending)
        #: 已经进入的分组（它不是磁盘上的目录，只是这一层的一个筛选项）。
        self._group_view: GroupRef | None = None
        #: 本层照片的分组 / 排序字段快照（按路径索引）。
        self._facet_values: dict[Path, PhotoFacets] = {}
        self._facet_job: int | None = None
        #: 这一层还没摆上屏的条目。要重排时先攒在这里，顺序定下来再一次给出去；
        #: ``None`` 表示「模型就是当前这一层的全部」，可以边扫边出图。
        self._staged: list[GridEntry] | None = None
        #: 横幅上正占着「这一层什么都没有」这句话；有了内容要把它收回去。
        self._level_notice = False
        #: 横幅上正占着「正在读取元数据…」这条进度。
        self._progress_notice = False
        #: 扫描本身出过问题（跳过的目录 / 读不了的文件）。它比「这里是空的」更该说。
        self._scan_issue = False
        self._exiftool_available: bool | None = None  # 探测一次就够
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        self._build_ui()
        self._build_actions()
        self._restore_session()
        self._sync_order_actions()
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
        self._grid.background_activated.connect(self._on_background_activated)
        self._grid.viewport_changed.connect(self._refresh_thumbnail_window)
        self._grid.size_step_requested.connect(self.step_thumbnail_size)
        self._grid.icon_edge_changed.connect(self._on_grid_edge_changed)
        self._grid.rename_requested.connect(self._on_rename_requested)
        self._grid.rename_committed.connect(self._on_rename_committed)
        self._grid.rename_active_changed.connect(self._on_rename_active_changed)
        # 「重命名」「移到废纸篓」能不能用，取决于当前有没有选中东西。
        selection_model = self._grid.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(
                lambda _current, _previous: self._update_action_states()
            )
            selection_model.selectionChanged.connect(
                lambda _selected, _deselected: self._update_action_states()
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

        # ⌘⌫：与访达一致，直接把选中的若干项移进废纸篓，不弹确认框（见 ADR 0008）。
        # Qt 在 macOS 上把 Command 写作 Ctrl；StandardKey.Delete 指的是前向删除键
        # （⌘⌦），不是访达那个「移到废纸篓」的 ⌘⌫，所以这里显式写出来。
        self._trash_action = QAction("移到废纸篓", self)
        self._trash_action.setShortcut(QKeySequence("Ctrl+Backspace"))
        self._trash_action.triggered.connect(self.trash_selected)

        # ⌘Z 撤的是这一摞里的栈顶，改名与移到废纸篓共用（core.metadata.undo）。
        # 文案跟着栈顶变，用户按之前就知道会撤掉什么。
        self._undo_action = QAction("撤销", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.triggered.connect(self.undo_last_write)

        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._quit_action.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        file_menu.addAction(self._rename_action)
        file_menu.addAction(self._trash_action)
        file_menu.addAction(self._undo_action)
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
        self._build_order_menus(view_menu)
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

    # ---- 分组与排序（见 ADR 0009）--------------------------------------------

    def _build_order_menus(self, view_menu: QMenu) -> None:
        """「视图 → 分组 / 排序」两个子菜单。

        放菜单而不是窗口上的控件：这个项目一直刻意不加工具栏，照片区那点高度不该
        拿来放两个下拉。
        """
        group_menu = view_menu.addMenu("分组")
        group_menu.setToolTipsVisible(True)
        self._group_actions: dict[str | None, QAction] = {
            None: self._order_choice(group_menu, "不分组", None, self.set_group_facet)
        }
        for facet in GROUP_FACETS:
            self._group_actions[facet.id] = self._order_choice(
                group_menu,
                facet.label,
                facet.id,
                self.set_group_facet,
                unavailable=self._unavailable_hint(facet),
            )

        sort_menu = view_menu.addMenu("排序")
        sort_menu.setToolTipsVisible(True)
        self._sort_actions: dict[str | None, QAction] = {
            None: self._order_choice(
                sort_menu, "默认（相册在前，按名称）", None, self.set_sort_facet
            )
        }
        for facet in SORT_FACETS:
            self._sort_actions[facet.id] = self._order_choice(
                sort_menu,
                facet.label,
                facet.id,
                self.set_sort_facet,
                unavailable=self._unavailable_hint(facet),
            )
        sort_menu.addSeparator()
        self._descending_action = QAction("逆序（新在前 / 大在前）", self)
        self._descending_action.setCheckable(True)
        self._descending_action.toggled.connect(self.set_sort_descending)
        sort_menu.addAction(self._descending_action)

    def _order_choice(
        self,
        menu: QMenu,
        label: str,
        facet_id: str | None,
        handler: Callable[[str | None], None],
        *,
        unavailable: str | None = None,
    ) -> QAction:
        """菜单里的一个单选项。``unavailable`` 非空时置灰并说明原因。"""
        action = QAction(label, self)
        action.setCheckable(True)
        if unavailable is not None:
            action.setEnabled(False)
            action.setToolTip(unavailable)
        else:
            action.triggered.connect(lambda _checked=False, target=facet_id: handler(target))
        menu.addAction(action)
        return action

    def _unavailable_hint(self, facet: Facet) -> str | None:
        """没装 exiftool 时，只有 exiftool 读得到的那些字段要**明说**为什么用不了。

        降级可以，假装没降级不行（见 ``core.metadata.exif``）：让用户分出一个
        「全都没有」的组，比一开始就告诉他「这个字段要装 exiftool」糟糕得多。
        """
        if not facet.needs_exiftool or self._has_exiftool:
            return None
        return "需要 exiftool 才能读取这个字段。安装后即可使用：brew install exiftool"

    @property
    def _has_exiftool(self) -> bool:
        """exiftool 现在能不能用。启动时探一次，之后不再反复起进程去问。"""
        if self._exiftool_available is None:
            self._exiftool_available = ExifToolReader().available()
        return self._exiftool_available

    def set_group_facet(self, facet_id: str | None) -> None:
        """切换分组字段（菜单用）。"""
        if facet_id is not None and group_facet(facet_id) is None:
            return  # 非法 id：会话文件里的旧值也可能走到这里，静默忽略
        if facet_id == self._group_facet:
            self._sync_order_actions()
            return
        self._group_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        # 换分组字段等于换了一组组卡片，原来进入的那个组已经不存在了。
        self._group_view = None
        self._reload_level()

    def set_sort_facet(self, facet_id: str | None) -> None:
        """切换排序字段（菜单用）。组内排序也跟着换 —— 这正是它的用处。"""
        if facet_id is not None and sort_facet(facet_id) is None:
            return  # 非法 id
        if facet_id == self._sort_facet:
            self._sync_order_actions()
            return
        self._sort_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def set_sort_descending(self, descending: bool) -> None:
        """切换正序 / 逆序。只重排当前这一层，不必重新读元数据。"""
        if descending == self._sort_descending:
            return
        self._sort_descending = descending
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def _remember_ordering(self) -> None:
        self._session = self._session.with_ordering(
            self._group_facet, self._sort_facet, self._sort_descending
        )
        self._save_session()

    def _sync_order_actions(self) -> None:
        """让菜单的勾选状态跟着实际选择走（含从会话恢复出来的选择）。"""
        for facet_id, action in self._group_actions.items():
            action.setChecked(facet_id == self._group_facet)
        for facet_id, action in self._sort_actions.items():
            action.setChecked(facet_id == self._sort_facet)
        self._descending_action.setChecked(self._sort_descending)
        # 没有排序字段时「逆序」没有意义（默认顺序是相册在前、名称升序）。
        self._descending_action.setEnabled(self._sort_facet is not None)

    def _current_order(self) -> SortOrder:
        return SortOrder(facet=sort_facet(self._sort_facet), descending=self._sort_descending)

    def _current_group_facet(self) -> Facet | None:
        return group_facet(self._group_facet)

    def _ordering_active(self) -> bool:
        """这一层要不要在最后统一重排一次。

        默认顺序（相册在前、按名称）**不需要**：扫描本来就是这个顺序，边扫边出图
        才是大库该有的手感。选了分组或某个排序字段才需要重排。
        """
        if self._group_view is not None:
            # 组里已经是照片了，再分组进不去也出不来，只剩组内排序。
            return sort_facet(self._sort_facet) is not None
        return self._group_facet is not None or self._sort_facet is not None

    def _needs_facet_read(self) -> bool:
        """当前的选择是否必须真去读 EXIF 才能算出顺序。"""
        group = None if self._group_view is not None else self._current_group_facet()
        return needs_metadata((group, sort_facet(self._sort_facet)))

    # ---- 进入与退出分组 ------------------------------------------------------

    def enter_group(self, group: GroupRef) -> None:
        """进入一个分组（双击组卡片）。

        分组不是磁盘上的目录，因此不走 ``enter_folder``：它记不住路径，退出时
        直接回到进来之前的那一层。
        """
        self._group_view = group
        self._reload_level()

    def _leave_group(self) -> None:
        if self._group_view is None:
            return
        self._group_view = None
        self._reload_level()

    # ---- 读取本层元数据并重排 ------------------------------------------------

    def _start_facet_read(self) -> None:
        """按当前选择给这一层补齐元数据，然后重排。

        只有真的要读 EXIF 的字段才会走到这里（按文件名排序之类是纯算的）。
        读过一次的结果落进索引（``core.library.index``），所以第二次换分组字段
        几乎立刻完成。
        """
        library = self._library
        if library is None or not self._needs_facet_read():
            self._apply_ordering()
            return
        # 暂存优先：要重排时这一层的照片还没上屏，模型里当然一条也没有。
        entries = self._staged if self._staged is not None else self._model.entries()
        refs = [entry for entry in entries if isinstance(entry, PhotoRef)]
        if not refs:
            self._apply_ordering()
            return
        generation = self._generation
        self._progress_notice = True
        self._show_banner(
            f"正在读取 {len(refs)} 张照片的元数据，用于{'分组' if self._group_facet else '排序'}…"
        )

        resolver = self._facet_place_resolver()

        def work(context: TaskContext) -> object:
            return library.photo_facets(
                refs,
                should_cancel=context.is_cancelled,
                on_progress=lambda done, total: context.progress((done, total)),
                status=lambda text: context.progress(text),
                resolver=resolver,
            )

        self._facet_job = self._runner.submit(
            work,
            on_progress=partial(self._on_facet_progress, generation),
            on_result=partial(self._on_facets_ready, generation),
            on_error=partial(self._on_facets_failed, generation),
        )

    def _clear_progress_notice(self) -> None:
        """收掉「正在读取元数据…」那条 —— **只收自己那一条**：扫描出问题时那条横幅
        比进度重要得多，不能顺手一起藏了。"""
        if not self._progress_notice:
            return
        self._progress_notice = False
        self._banner.hide()

    def _update_level_notice(self) -> None:
        """这一层什么都没有时说一声；有了内容就把这句收回去。

        **判据是「网格上有没有条目」，不是「有没有相册 / 照片」**：分组会把照片换成
        一张张组卡片，只数相册和照片会把一屏组卡片（甚至一屏相册）误判成空 ——
        那正是这条提示曾经出现的地方。
        """
        if self._library is None:
            return
        if self._scan_issue:
            # 扫描出过问题时横幅归它：那句（哪些内容没读到）比「这里是空的」更该说，
            # 而这里也不许把它收掉 —— 收了等于把用户真正需要知道的事藏起来。
            return
        if self._model.rowCount():
            if self._level_notice:
                self._level_notice = False
                self._banner.hide()
            return
        self._level_notice = True
        if self._group_view is not None:
            self._show_banner("这个分组里没有照片了。")
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._show_banner("这个文件夹里既没有子文件夹，也没有可显示的照片。")
        else:
            self._show_banner("没有找到可显示的照片。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP。")

    def _cancel_facet_job(self) -> None:
        """作废在途的元数据读取（换库、换层、换分组字段时）。"""
        if self._facet_job is not None:
            self._runner.cancel(self._facet_job)
            self._facet_job = None

    def _facet_place_resolver(self) -> LazyPlaceResolver:
        """给分组 / 排序用的地点反查器；面板那边加载过索引就直接复用。"""
        # 面板那边已经加载过索引就直接复用，省一次读 8.5 MB 城市表。
        needs = self._place_resolver is None or (
            self._place_index is not None and not self._place_resolver.ready
        )
        if needs:
            self._place_resolver = LazyPlaceResolver(self._place_index)
        return self._place_resolver

    def _on_facet_progress(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        if isinstance(payload, str):
            self._show_banner(payload)  # 「正在下载城市数据…」这类说明
            return
        if not isinstance(payload, tuple):
            return
        done, total = payload if len(payload) == 2 else (0, 0)
        if isinstance(done, int) and isinstance(total, int) and total:
            self._show_banner(f"正在读取元数据…{done} / {total}")

    def _on_facets_ready(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._facet_values = payload if isinstance(payload, dict) else {}
        if self._place_resolver is not None and self._place_resolver.ready:
            # 城市数据这回加载过了，面板那边就不用再加载一遍。
            self._place_index = self._place_resolver.index
        self._clear_progress_notice()
        self._apply_ordering()

    def _on_facets_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._show_banner(f"读取元数据失败，按默认顺序显示：{message}")
        self._apply_ordering()

    def _apply_ordering(self) -> None:
        """把当前这一层按分组 / 排序重排一次，然后一次性摆到网格上。"""
        if self._stack.currentWidget() is not self._grid_page:
            return
        library = self._library
        if library is None:
            return
        try:
            self._reorder_level(library)
        finally:
            # 顺序定下来了，这才有资格回答「这一层是不是空的」。
            self._update_level_notice()

    def _reorder_level(self, library: Library) -> None:
        # 暂存的优先：那一层还没上屏，顺序就是这里算出来的。
        staged = self._staged
        entries = staged if staged is not None else list(self._model.entries())
        self._staged = None
        albums = [entry for entry in entries if isinstance(entry, FolderRef)]
        photos = [entry for entry in entries if isinstance(entry, PhotoRef)]
        # 相册卡片永远排在前面：先看文件夹、再看按元数据归拢出来的组，是既有心智。
        order = self._current_order()
        # 已经在分组里面了：那一层全是照片，再分一次组毫无意义（进去就出不来）。
        group = None if self._group_view is not None else self._current_group_facet()
        values = self._facet_values
        if group is not None:
            ordered: list[GridEntry] = [
                *albums,
                *build_groups(photos, facet=group, order=order, root=library.root, values=values),
            ]
        else:
            ordered = [*albums, *sort_photos(photos, order=order, root=library.root, values=values)]
        if staged is None and ordered == list(self._model.entries()):
            return  # 什么都没攒、顺序也没变：让整屏重画一次毫无意义
        selected = self._grid.selected_rows()
        anchor = self._model.photo_at(self._grid.current_row())
        self._model.reorder(ordered)
        if anchor is not None:
            row = self._model.row_of_path(anchor.path)
            if row >= 0:
                self._grid.select_row(row, ensure_visible=False)
        elif selected:
            self._grid.select_row(min(selected), ensure_visible=False)
        self._refresh_thumbnail_window()

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
        self._current_dir = library.root
        self._staged = [] if self._ordering_active() else None
        self._level_notice = False
        self._progress_notice = False
        self._scan_issue = False
        self._update_breadcrumb()
        self._update_mode_actions()
        self.setWindowTitle(f"{library.display_name} — Gallery")
        self._stack.setCurrentWidget(self._grid_page)
        self._update_action_states()
        self._remember(library.root)
        self._start_scan()

    def _teardown_library(self) -> None:
        self._cancel_facet_job()
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
        if self._library is not None:
            self._library.close_index()
        self._library = None
        self._group_view = None
        self._facet_values = {}

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        """按当前浏览方式列出内容：平铺模式递归整库，相册模式只列当前一层。

        已经进入某个分组时不再扫盘 —— 这一组是上一次分组算出来的，直接铺开即可。
        """
        library = self._library
        if library is None:
            return
        if self._group_view is not None:
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

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        if self._staged is not None:
            # 要重排就先攒着：**边扫边出图在这里等于闪一下** —— 用户先看到一屏
            # 未分组的照片，下一秒它们全变成组卡片，看起来像界面自己乱了。
            # 攒到顺序定下来再一次给出去，中间那段宁可留白（横幅会说明在读什么）。
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
        self._refresh_thumbnail_window()
        self._start_facet_read()

    def _on_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        self._scan_issue = True
        self._show_banner(f"扫描失败：{message}")
        # 已经攒到的条目照常摆出来（按当前能算出来的顺序）：扫到一半失败时给用户
        # 一屏能看的东西，比给一屏空白加一句「扫描失败」有用。
        self._apply_ordering()

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
        """双击一个格子：相册或分组就进去，照片就打开大图。"""
        entry = self._model.entry_at(index.row())
        if isinstance(entry, FolderRef):
            self.enter_folder(entry.path)
        elif isinstance(entry, GroupRef):
            self.enter_group(entry)
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

    # ---- 重命名与移到废纸篓（仅有的两个会动用户文件的入口）--------------------

    def rename_current_entry(self) -> None:
        """对当前选中的格子开始重命名（菜单 / ⌘R 走这里）。"""
        index = self._grid.currentIndex()
        if not index.isValid():
            return
        if self._model.group_at(index.row()) is not None:
            self._show_banner(_GROUP_NOT_FILE_ACTION)
            return
        self._grid.begin_rename(index)

    def _on_rename_requested(self, index: QModelIndex) -> None:
        """再次点击已选中的格子：进入行内重命名。"""
        if self._model.group_at(index.row()) is not None:
            self._show_banner(_GROUP_NOT_FILE_ACTION)
            return
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
        self._refresh_undo_label()

    # ---- 移到废纸篓（界面上就是「删除」，见 ADR 0008）------------------------

    def trash_selected(self) -> None:
        """⌘⌫：把选中的照片 / 相册移进系统废纸篓。

        不弹确认框，理由和访达一样：废纸篓本身就是那层保险，⌘Z 再是一层；
        弹窗只会让「整理一批照片」变成一连串的确认。做完立刻用横幅说清楚。
        """
        library = self._library
        if library is None or self._stack.currentWidget() is not self._grid_page:
            return
        if self._grid.is_renaming():
            return  # 键盘正在行内输入框里，删掉正在编辑的那一张只会让人以为见鬼了
        rows = self._grid.selected_rows()
        entries = [entry for entry in (self._model.entry_at(row) for row in rows) if entry]
        if not entries:
            return
        groups = [entry for entry in entries if isinstance(entry, GroupRef)]
        if groups:
            # 静默跳过等于「按了没反应」。分组不是磁盘上的东西，删不掉。
            self._show_banner(_GROUP_NOT_FILE_ACTION)
            return
        try:
            result = library.move_to_trash(entries)
        except GalleryError as exc:
            logger.info("移到废纸篓失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._forget_trashed(result.moved)
        # 只摘真正移走的那些行：批量里失败的那几张还得留在网格上。
        moved_paths = {entry.path for entry in result.moved}
        removed = [row for row in rows if self._is_row_moved(row, moved_paths)]
        self._model.remove_rows(removed)
        self._select_after_removal(removed)
        self._show_banner(_trash_banner(result))
        self._refresh_undo_label()
        self._refresh_thumbnail_window()

    def _is_row_moved(self, row: int, moved_paths: set[Path]) -> bool:
        entry = self._model.entry_at(row)
        return entry is not None and entry.path in moved_paths

    def _forget_trashed(self, entries: Sequence[GridEntry]) -> None:
        """丢掉内存里按路径索引的缓存。

        磁盘上的缩略图缓存**留着**：照片还在废纸篓里，撤销回来时直接命中，
        不必重新解码（这与改名的处理相反，见 ``Library.move_to_trash``）。
        """
        for entry in entries:
            self._preview_cache.pop(entry.path, None)
            self._metadata_cache.pop(entry.path, None)

    def _select_after_removal(self, removed: Sequence[int]) -> None:
        """摘掉几行之后，把选中落到原来那一带（访达是选中后一个）。"""
        if not removed:
            return
        target = min(removed)
        if target >= self._model.rowCount():
            target = self._model.rowCount() - 1
        if target >= 0:
            self._grid.select_row(target, ensure_visible=False)

    # ---- 撤销 --------------------------------------------------------------

    def undo_last_write(self) -> None:
        """⌘Z：撤销最近一次写操作 —— 改回去，或者从废纸篓取回来。"""
        library = self._library
        if library is None:
            return
        try:
            result = library.undo_last_write()
        except GalleryError as exc:
            logger.info("撤销失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            self._refresh_undo_label()
            return
        if result is None:
            self._show_banner("没有可撤销的操作。")
            return
        if result.action == ACTION_RENAME and result.replaced is not None:
            # 撤销之前，这一行挂在改完名的新路径上 —— 要按它找行号。
            row = self._model.row_of_path(result.replaced.path)
            self._apply_rename(
                RenameResult(
                    entry=result.entries[0],
                    previous=result.replaced,
                    renamed=True,
                    journaled=result.journaled,
                ),
                row,
                banner=_undo_banner(result),
            )
        else:
            self._apply_restore(result)
            self._show_banner(_undo_banner(result))
        self._refresh_undo_label()

    def _apply_restore(self, result: UndoResult) -> None:
        """把撤销删除取回来的条目放回网格，然后按当前分组 / 排序重排一次。

        重排而不是就地插行：一张照片取回来之后该落在哪，取决于当前的排序字段与方向
        （逆序时光标插到「最后一个更大值之后」恰恰是错的位置）。整层重排一次是
        几十微秒的事，而它给出的位置一定是对的。
        """
        if not result.entries:
            return
        for entry in result.entries:
            self._model.insert_entry(entry)
        self._apply_ordering()
        restored = [self._model.row_of_path(entry.path) for entry in result.entries]
        first = min((row for row in restored if row >= 0), default=-1)
        if first >= 0:
            self._grid.select_row(first, ensure_visible=False)
        self._refresh_thumbnail_window()

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
        # 重命名与移到废纸篓只对网格里的选中项有意义：大图页、没选中东西时都不该可用。
        # 改名的过程中也不启用 —— 那时键盘归输入框，⌘⌫ 不该把正在编辑的那张删掉。
        in_grid = self._stack.currentWidget() is self._grid_page
        idle = not self._grid.is_renaming()
        current_row = self._grid.current_row()
        # 分组卡片不是磁盘上的东西：选中它时改名与删除都不可用，按下去也没有
        # 「只处理照片」这种含糊的半截行为（见 ADR 0009）。
        file_only = not any(
            isinstance(self._model.entry_at(row), GroupRef)
            for row in (*self._grid.selected_rows(), current_row)
            if row >= 0
        )
        self._rename_action.setEnabled(in_grid and current_row >= 0 and idle and file_only)
        self._trash_action.setEnabled(
            in_grid and bool(self._grid.selected_rows()) and idle and file_only
        )
        self._update_undo_action()

    def _update_undo_action(self) -> None:
        """「撤销」的文案跟着这摞的栈顶变，栈空时禁用。"""
        self._undo_action.setText(
            "撤销" if self._undo_label is None else f"撤销 {self._undo_label}"
        )
        self._undo_action.setEnabled(self._undo_label is not None)

    def _refresh_undo_label(self) -> None:
        """重新问一次 core：下一次 ⌘Z 会撤掉什么。"""
        library = self._library
        self._undo_label = library.next_undo_label() if library is not None else None
        self._update_undo_action()

    # ---- 浏览方式与相册导航 ------------------------------------------------

    def set_browse_mode(self, mode: str) -> None:
        """切换「相册（逐层进入）」与「全部照片（整库平铺）」。"""
        if mode == self._browse_mode:
            self._update_mode_actions()
            return
        self._browse_mode = mode
        self._group_view = None
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
        self._group_view = None
        self._reload_level()

    def go_up(self) -> None:
        """回到上一层：正在分组里就退回那一层分组，否则按相册逐层往上。

        分组比文件夹深一级（它是文件夹之上的一个视图），所以先退它。
        """
        library = self._library
        if library is None:
            return
        if self._group_view is not None:
            self._leave_group()
            return
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return
        current = self._current_dir or library.root
        parent = parent_of(library.root, current)
        if parent is not None:
            self.enter_folder(parent)

    def _can_go_up(self) -> bool:
        """现在退不退得回去。菜单里的「上一级」与双击空白处都问它。

        判据与 :meth:`go_up` 一致，否则菜单灰着、双击却退了回去（或者反过来）。
        """
        library = self._library
        if library is None or self._grid.is_renaming():
            return False
        if self._group_view is not None:
            return True
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return False
        return parent_of(library.root, self._current_dir or library.root) is not None

    def _on_background_activated(self) -> None:
        """双击了网格里没有格子的地方：退回上一层，与 `Esc` 是同一件事。

        这一层空着（空相册、空分组）时尤其需要它 —— 屏幕上根本没有可双击的东西，
        而这正是最想立刻退出去的时候。退不回去时说一句为什么，不做「按了没反应」。
        """
        if self._grid.is_renaming():
            # 改到一半：这一下先归输入框。既不把人带走，也不能说「退不动了」——
            # 明明退得回去，只是这一刻不归这一下管。
            return
        if self._can_go_up():
            self.go_up()
            return
        self._show_banner(_NO_PARENT_NOTICE)

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
        self._cancel_facet_job()
        self._facet_values = {}
        self._model.clear()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = [] if self._ordering_active() else None
        self._level_notice = False
        self._progress_notice = False
        self._scan_issue = False
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
        # 平铺模式下平时不显示面包屑，但**分组里必须显示**：那里没有文件夹可指，
        # 只有一条「库名 ▸ 2024 年 5 月」才说得出自己在哪。
        in_albums = self._browse_mode == BROWSE_MODE_ALBUMS
        if library is None or (not in_albums and self._group_view is None):
            self._breadcrumb.hide()
            return
        parts = (
            breadcrumb_parts(library.root, self._current_dir or library.root)
            if in_albums
            else [(library.display_name, library.root)]
        )
        group = self._group_view.label if self._group_view is not None else None
        self._breadcrumb.set_path(parts, group=group)
        self._breadcrumb.show()

    def _update_mode_actions(self) -> None:
        albums = self._browse_mode == BROWSE_MODE_ALBUMS
        self._albums_action.setChecked(albums)
        self._flat_action.setChecked(not albums)

    def _update_navigation_actions(self) -> None:
        # 「上一级」只在网格页启用：大图页的 Esc 是「返回网格」，两个动作共用一个 Esc，
        # 必须有一个是关着的，否则同一按键落到谁身上说不准。改名的过程中也不启用 ——
        # 那时 Esc 是输入框的「取消改名」（输入框还会先抢一次按键，见 RenameEdit.event）。
        in_grid = self._stack.currentWidget() is self._grid_page
        self._up_action.setEnabled(in_grid and self._can_go_up())

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
