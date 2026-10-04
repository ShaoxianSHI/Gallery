"""主窗口：**装配**在这里，各条流程在 :mod:`gallery.ui.window` 下的几个 mixin 里。

界面层的状态全部集中在这个窗口对象上，并且**显式传递**：没有全局单例，没有模块级可变状态，
所以打开两个库、或者在测试里构造两个窗口，都不会互相干扰。

三个「不许」在这里具体落地：

- 不自己读原图：一切像素都来自 ``gallery.core``（经 ``Library`` 门面）。
- 不偷偷写盘：**每一次写操作都来自用户在菜单或快捷键里亲手触发的那一下** ——
  改名（⌘R）、移到废纸篓（⌘⌫）、粘贴（⌘V）、复制 / 移动到相册、新建相册
  （⇧⌘N）、设为相册封面、撤销（⌘Z），另加开场对话框里同意后的 ``prepare_library``。
  每一条都有对应的 ADR，没有一个是后台整理、脚本改名或「顺便改改」（红线 1）。
- 不阻塞主线程：**大件**都在 ``TaskRunner`` 的线程池里 —— 扫描、分组用的元数据批量
  读取、缩略图与全分辨率解码、粘贴与复制 / 移动（一次几百 MB）。
  留在主线程上的是**小件**，刻意如此：``mkdir`` 建一个空文件夹（ADR 0022 决策 6）、
  会话记忆与窗口几何的落盘。

**这一份文件现在只留三样东西**：

1. ``__init__``（状态一律在这里声明并初始化，**显式传下去**，不藏在别处）与
   ``_build_ui`` / ``_build_actions`` —— 控件与动作的**装配**；
2. **Qt 的覆写**（``mousePressEvent`` / ``showEvent`` / ``resizeEvent`` / ``closeEvent`` …）
   与标题栏那一行的几何 —— 它们**必须**留在 ``MainWindow`` 自己身上：Qt 按 MRO 找第一个
   实现，拆进 mixin 就会有一处永远不生效，而哪一处不生效取决于书写顺序；
3. **跨流程的那几条线** —— 换层与换浏览方式时怎么重置状态（:meth:`MainWindow._reload_level`）、
   到了新的一层该选中哪一格（``_arm_focus`` 一族）、面包屑、每条命令此刻能不能按
   （``_update_action_states``）、以及关窗时的收尾。

其余每一条流程各住一个 mixin（``gallery.ui.window``），那些模块的文档里各自说清
「这一块归它管、为什么单独拆出来」：

=========================  ========================================================
``ordering``               分组与排序，以及「把这一层重排一遍」那条链（ADR 0009）
``library_flow``           打开库 / 关库 / 扫描 / 会话
``search_flow``            检索与索引维护（ADR 0026）
``watch_flow``             实时监听（ADR 0013）
``thumbnail_flow``         缩略图要哪一批、怎么换档（ADR 0015）
``metadata_flow``          元数据面板与按坐标反查的地点（ADR 0010）
``context_menu_flow``      右键菜单与那几条只读命令（ADR 0014）
``viewer_flow``            大图页与两份缓存（ADR 0021）
``banner_flow``            横幅与它的摆位（ADR 0029）
``calendar_flow``          浏览方式与日历页（ADR 0033）
=========================  ========================================================

**会动用户文件的那些入口不在上面这张表里**：它们在 :mod:`gallery.ui.write_ops`，
那一份**留在原地不搬** —— ``tests/test_guards.py`` 按**文件名**匹配它来守红线 1，
搬进子包会让那条守护失守。

方法名与属性名一个字都没改（测试直接摸 ``window._model``、
``window._refresh_thumbnail_window``、``window._search_field`` 这些私有成员）；
搬进 mixin 仍然是继承来的，所以那 1809 个用例一个都不用动。
"""

from __future__ import annotations

import datetime
import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.external import launch
from gallery.core.library.facade import Library
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.core.library.search import SearchCondition
from gallery.core.library.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    SessionState,
    load_session,
)
from gallery.core.library.watcher import LibraryChange
from gallery.core.metadata.exif import MetadataReport
from gallery.core.metadata.facets import PhotoFacets
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui import banners, desktop, theme
from gallery.ui.banner_overlay import BannerOverlay
from gallery.ui.browser.grid import PhotoGrid
from gallery.ui.browser.model import PhotoListModel
from gallery.ui.browser.thumb_sizes import (
    THUMBNAIL_SIZES,
    edge_for_index,
    label_for_index,
    nearest_index,
)
from gallery.ui.calendar import CalendarPage
from gallery.ui.external_button import (
    BUTTON_GAP,
    BUTTON_WIDTH,
    ExternalAppsButton,
    ExternalEntry,
    button_rect,
    calendar_icon,
    folder_icon,
)
from gallery.ui.navigation import (
    ENTRY_KIND_PATH,
    EntryKey,
    LevelKey,
    breadcrumb_parts,
    entry_key,
    level_key,
    parent_of,
    path_key,
    row_of_entry_key,
)
from gallery.ui.panels.breadcrumb import BreadcrumbBar
from gallery.ui.panels.metadata import MetadataPanel
from gallery.ui.panels.welcome import WelcomeView
from gallery.ui.search_field import HEIGHT as SEARCH_FIELD_HEIGHT
from gallery.ui.search_field import MARGIN_RIGHT as SEARCH_FIELD_MARGIN_RIGHT
from gallery.ui.search_field import TEXT_ALIGN_DROP as SEARCH_FIELD_TEXT_ALIGN_DROP
from gallery.ui.search_field import WIDTH as SEARCH_FIELD_WIDTH
from gallery.ui.search_field import SearchField
from gallery.ui.tasks import TaskRunner
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.title_bar import EXTRA_PADDING as TITLE_BAR_EXTRA
from gallery.ui.title_bar import HEIGHT as TITLE_BAR_HEIGHT
from gallery.ui.title_bar import TitleBar
from gallery.ui.viewer.detail import DetailView
from gallery.ui.watch import WatchBridge, WatchStarter, start_watch
from gallery.ui.window.banner_flow import BannerFlowMixin
from gallery.ui.window.calendar_flow import CalendarFlowMixin
from gallery.ui.window.context_menu_flow import ContextMenuFlowMixin
from gallery.ui.window.library_flow import LibraryFlowMixin
from gallery.ui.window.metadata_flow import MetadataFlowMixin
from gallery.ui.window.ordering import OrderingMixin
from gallery.ui.window.search_flow import SearchFlowMixin
from gallery.ui.window.thumbnail_flow import ThumbnailFlowMixin
from gallery.ui.window.viewer_flow import ViewerFlowMixin
from gallery.ui.window.watch_flow import WatchFlowMixin
from gallery.ui.write_ops import WriteOpsMixin, flattened_entries

logger = logging.getLogger(__name__)


class MainWindow(
    OrderingMixin,
    LibraryFlowMixin,
    SearchFlowMixin,
    WatchFlowMixin,
    ThumbnailFlowMixin,
    MetadataFlowMixin,
    ContextMenuFlowMixin,
    ViewerFlowMixin,
    BannerFlowMixin,
    CalendarFlowMixin,
    WriteOpsMixin,
    QMainWindow,
):
    # 上面那十个 mixin 就是这个窗口的**目录**：每一条流程各住一间
    # （``gallery.ui.window`` 下每个模块的文档说清自己归哪一块管），这一份文件留装配、
    # Qt 覆写与跨流程的那几条线。它们互相不重名，真撞了也是这一行 ``class`` 写得清楚
    # 的时候才看得见。
    #
    # ``WriteOpsMixin`` 带进来的是**会动用户文件的那些入口**（粘贴 / 新建相册 /
    # 复制与移动到相册 / 改名 / 移到废纸篓 / 撤销），连同它们共用的那个前置问题
    # 「现在动的是哪些东西」。红线 1 说「绝不删除、移动用户照片」—— 把它们收在一个
    # 文件里，那句话就从「靠人记」变成「可以 grep」（详见 ui/write_ops.py 的文档）。
    # 它**留在原地不搬进 ui/window/**：那条守护是按文件名匹配那个文件的。

    """应用主窗口。"""

    def __init__(
        self,
        *,
        session_base: Path | None = None,
        initial_library: Path | None = None,
        watch_starter: WatchStarter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_base = session_base
        self._session: SessionState = load_session(base=session_base)
        self._runner = TaskRunner(self)
        #: 外部文件变化 → 网格。回调在 watchdog 的线程上到达，必须用排队连接接。
        self._watch = WatchBridge(self, starter=watch_starter or start_watch)
        self._watch.changed.connect(self._on_watch_changed, Qt.ConnectionType.QueuedConnection)
        self._watch.failed.connect(self._on_watch_failed, Qt.ConnectionType.QueuedConnection)
        #: 自家刚写过的路径与它们的过期时刻（见 ``WATCH_ECHO_TTL``）。
        self._watch_echoes: dict[Path, float] = {}
        #: 用户触发的那条横幅要让后台消息让位到什么时候（见 BANNER_STICKY_SECONDS）。
        self._banner_sticky_until = 0.0
        #: 攒着没落地的变化（当时扫描在途、条目还在暂存区，或人正在大图页）。
        #: 条件合适时补做，见 :meth:`_flush_pending_watch`。
        self._watch_pending: tuple[LibraryChange, ...] | None = None
        #: 分组视图里来了新照片：重新分组之后要回到哪个组（ADR 0013）。
        self._pending_group: str | None = None
        #: 回到那个组时该选中哪一格（那一趟是软件自己绕出去的，见 :meth:`_reenter_group_later`）。
        self._pending_focus: EntryKey | None = None

        self._library: Library | None = None
        self._pipeline: ThumbnailPipeline | None = None
        self._generation = 0
        self._scan_job: int | None = None
        #: 选区变化后**攒一轮**再全量重算动作可用性（0ms 单次；理由见连接处）。
        #: 同步清掉是为了关窗时不留一个还排着队的定时器。
        self._action_states_timer = QTimer(self)
        self._detail_job: int | None = None
        self._current_row = -1
        self._metadata_job: int | None = None
        #: 「复制元数据」现读元数据用的那次任务（面板的读取消了就没人要那份结果）。
        self._metadata_copy_job: int | None = None
        #: 会往某个目录里**落文件**的后台任务（⌘V 粘贴、复制到相册、移动到相册，
        #: 见 ADR 0018 / 0019）。**三者同时只允许一个**：它们会争同一个目标名字，
        #: 后到的会把先到的盖掉 —— 那是这个项目最不能出现的一种事。
        #: 切库时必须取消（见 ``_teardown_library``），否则它会拿旧库的记录去问新库
        #: 「下一次 ⌘Z 撤什么」，于是记录明明记了、界面上却撤不掉。
        self._write_job: int | None = None
        #: **在大图页上删掉的那一张**（路径；``None`` = 没删过，或已经取回来了）。
        #: ⌘Z 之后要跳回它（见 ``write_ops._jump_back_to_restored``）：删掉一张之后界面
        #: 接着看下一张，撤回来时人正站在另一张上，而整层重排还会把刚取回来的那张挪出
        #: 视野。**只对紧接着的那一次撤销有效**，用过即清（由 ``undo_last_write`` 清）。
        self._detail_trashed: Path | None = None
        #: 分组 / 排序与元数据面板共用的地点反查器（按坐标），首次用时才加载城市数据。
        self._place_resolver: LazyPlaceResolver | None = None
        #: 打开库时那一遍「全库解析地点」的任务（ADR 0010）。
        self._place_job: int | None = None
        #: 本次运行已经起过那一遍的库。**是集合不是单个**：来回切库不该把同一个库
        #: 再起一遍空跑（后来才加进来的照片由面板那条兜底路补，见 ``_maybe_lookup_place``）。
        self._place_pass_roots: set[Path] = set()
        #: 横幅上属于本任务的那句话（``None`` = 不占着横幅）。
        self._place_notice_text: str | None = None
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]] = OrderedDict()
        self._thumbnail_failures = 0
        #: 「清空缩略图缓存」在途的那一次（删除跑在**工作线程**上，见
        #: :meth:`~gallery.ui.window.thumbnail_flow.ThumbnailFlowMixin.clear_thumbnail_cache`
        #: —— 它是一次 O(n) 的 unlink 循环，留在主线程上会把窗口冻住几秒）。
        self._cache_job: int | None = None
        # 下一次 ⌘Z 会撤掉什么。日志只有写操作才会变，所以只在这几处刷新它 ——
        # 每次选中变化都重放一遍日志的话，用方向键翻图时白白读文件。
        self._undo_label: str | None = None

        self._size_index = nearest_index(self._session.thumbnail_edge)
        self._browse_mode: str = self._session.browse_mode or BROWSE_MODE_FLAT
        self._current_dir: Path | None = None
        # 分组 / 排序（见 ADR 0009）：字段 id 存进会话，下次打开沿用。
        # 从没选过排序字段的人（会话里那一项是空的）按默认排序字段走，见 session 模块。
        self._group_facet: str | None = self._session.group_facet
        self._sort_facet: str | None = self._session.resolved_sort_facet()
        self._sort_descending: bool = bool(self._session.sort_descending)
        #: 已经进入的分组（它不是磁盘上的目录，只是这一层的一个筛选项）。
        self._group_view: GroupRef | None = None
        #: 模型里此刻摆着的是哪一层。换层时用它分清「换了一层」（重新进入，选区清空）与
        #: 「还是这一层，重排一遍」（跟着停着的那一格走）—— ``enter_folder`` /
        #: ``enter_group`` 都是先改状态再重载，所以只能事后比一比，见 :meth:`_arm_focus`。
        self._level_key: LevelKey | None = None
        #: 这一层第一次排好时该选中的一格（``None`` = 谁也不选，见 :meth:`_arm_focus`）。
        self._focus: EntryKey | None = None
        #: 上面那一格摆出来了吗。一层只摆一次，免得跟用户后来的点选抢当前项。
        self._focus_restored = False
        #: 本层照片的分组 / 排序字段快照（按路径索引）。
        self._facet_values: dict[Path, PhotoFacets] = {}
        self._facet_job: int | None = None
        #: 日历那一页读整库拍摄时间的那次任务（与 :attr:`_facet_job` 分开：两个各自
        #: 有各自的取消时机，混成一个会让取消掉其中一个时另一个的结果也一起作废）。
        self._calendar_job: int | None = None
        # **从日历点开的那一层**：日历 → 某一格（若干张）→ 大图。
        # 两者都非空时，退回去要回**日历原来那一段**，而不是「全部照片」。
        self._from_calendar = False
        self._drill_photos: tuple[PhotoRef, ...] | None = None
        self._drill_label = ""
        #: 这一层还没摆上屏的条目。扫出来的一律先攒在这里，顺序定下来再一次给出去
        #: （见 ADR 0009 决策 6）；``None`` 表示「模型就是当前这一层的全部」，
        #: 也就是这一层已经排好、正在被人看。
        self._staged: list[GridEntry] | None = None
        #: 横幅上正占着「这一层什么都没有」这句话；有了内容要把它收回去。
        self._level_notice = False
        #: 横幅上正占着「正在读取元数据…」这条进度。
        self._progress_notice = False
        #: 进度那条自己写上去的话（收尾时**只收自己那一句**，见 ``_clear_progress_notice``）。
        self._progress_notice_text: str | None = None
        #: 扫描本身出过问题（跳过的目录 / 读不了的文件）。它比「这里是空的」更该说。
        self._scan_issue = False
        self._exiftool_available: bool | None = None  # 探测一次就够
        # ---- 搜索（ADR 0026）-------------------------------------------------
        #: 已生效的筛选条件，按录入顺序。**空列表 = 不在搜索**（普通的一层）。
        self._conditions: list[SearchCondition] = []
        #: 正在搜索框里改的是第几条条件（``None`` = 没在改）。改完回车**就地替换**它。
        self._editing_condition: int | None = None
        #: 上一次检索命中了多少张（面包屑上那个「搜索结果（128）」；``None`` = 还没查过）。
        self._search_total: int | None = None
        self._search_job: int | None = None
        #: 「把整库的照片写进索引」那一遍的任务（见 ``_maybe_start_photo_index``）。
        self._index_job: int | None = None
        #: 已经起过那一遍的库。**是集合不是单个**：来回切库不该把同一个库再起一遍空跑。
        self._index_roots: set[Path] = set()
        #: 横幅上正占着「正在建立检索索引…」这条进度。
        self._index_notice_text: str | None = None
        #: 窗口顶边那一条是不是已经归我们了（见 ``theme.unify_title_bar``）。
        #: 没归我们（例如 Linux）就照旧用 ``setWindowTitle``，不摆 ``TitleBar``。
        self._owns_title_bar = False
        #: 不在大图页时窗口顶边那一行显示的名字（ADR 0025 决策 4）。
        self._library_title = "Gallery"
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        #: 「等上一段切档动画跑完再执行」的档数，见 :meth:`step_thumbnail_size`。
        #: 0 = 没有排着的。方向相反的连按会互相抵掉（连按 ⇧⌘↑ 再 ⇧⌘↓ 等于没按）。
        self._pending_size_step = 0
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
        self.resize(1280, 860)
        self.setMinimumSize(720, 480)

        # 先把底色统一好，下面每一个控件都从这一份取色：标题栏那一段、面包屑、网格视口、
        # 大图区与元数据面板从此是同一块底色（见 ui.theme）。
        theme.ensure_installed()
        # 标题栏那一段交给窗口自己画，否则 macOS 会铺一层半透明材质，和下面的底色差一档。
        # 它同时回答「那一段归不归我们」：归了就得连**标题文字**也自己画，否则系统那份
        # 会与 TitleBar 叠在同一处（ADR 0025 决策 1）。
        self._owns_title_bar = theme.unify_title_bar(self)

        self._title_bar = TitleBar(self)
        self._title_bar.rename_committed.connect(self._on_title_rename_committed)
        self._title_bar.rename_cancelled.connect(self._title_bar.stop_edit)
        # 改到一半 Esc 归输入框、会动文件的命令一起让开 —— 与网格那个输入框同一件事
        # （网格那边是 ``rename_active_changed``，这里是对应的另一半，ADR 0025 决策 6）。
        self._title_bar.edit_active_changed.connect(self._on_rename_active_changed)
        if self._owns_title_bar:
            # 标题文字改由 ``TitleBar`` 画，系统那份必须空着 ——
            # 两份标题叠在同一处是最难看的失败方式（ADR 0025 决策 1）。
            self.setWindowTitle("")
        # 位置与「现在在哪一页」都还没定下来（``_layout_title_bar`` / ``showEvent``），
        # 这里只是别让它在窗口还没建好时就抢着显示：``_refresh_title`` 问的是页面栈。
        self._title_bar.hide()

        self._welcome = WelcomeView()
        self._welcome.open_requested.connect(self.choose_folder)
        self._welcome.recent_requested.connect(self._on_recent_requested)

        self._grid = PhotoGrid(icon_edge=edge_for_index(self._size_index))
        self._grid.setModel(self._model)
        self._grid.activated_index.connect(self._on_entry_activated)
        self._grid.background_activated.connect(self._on_background_activated)
        self._grid.viewport_changed.connect(self._refresh_thumbnail_window)
        self._grid.size_step_requested.connect(self.step_thumbnail_size)
        self._grid.transition_ended.connect(self._run_pending_size_step)
        self._grid.icon_edge_changed.connect(self._on_grid_edge_changed)
        self._grid.rename_requested.connect(self._on_rename_requested)
        self._grid.rename_committed.connect(self._on_rename_committed)
        self._grid.rename_active_changed.connect(self._on_rename_active_changed)
        self._grid.context_requested.connect(self._show_context_menu)
        # 「重命名」「移到废纸篓」能不能用，取决于当前有没有选中东西。
        #
        # **只接 `selectionChanged`，并且攒一轮再算。** Qt 一次用户动作会先发
        # `currentChanged` 再发 `selectionChanged`（点一格、Shift 连选一段都是），
        # 两条都接就是同一个动作算两遍。而一次算就是十八枚动作全量重算，
        # 其中 ⌘V 那一条还要**同步读一次系统剪贴板** —— 框里拖选三百行，
        # 就是几百次全量重算加几百次剪贴板往返，全在主线程上。
        #
        # 那个 0 毫秒的定时器把一串连发攒成一次：它不是「等一会儿再算」，而是
        # 「这一轮事件循环转完就算」，所以用户感觉不到延迟。
        selection_model = self._grid.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._schedule_action_states)
        self._action_states_timer.setSingleShot(True)
        self._action_states_timer.setInterval(0)
        self._action_states_timer.timeout.connect(self._update_action_states)

        self._breadcrumb = BreadcrumbBar()
        self._breadcrumb.path_requested.connect(self._on_breadcrumb_requested)
        self._breadcrumb.condition_removed.connect(self._on_condition_removed)
        self._breadcrumb.condition_edited.connect(self._on_condition_edited)
        # 右上角那一块（搜索胶囊，页面上还有左边的「用外部应用打开」按钮）要让出多宽，
        # **不在这里给死** —— 那一块的内容随页面变（按钮只在日历页出现），由
        # ``_layout_title_bar`` 每次摆的时候按此刻的样子算（ADR 0034）。

        # **横幅不进这个布局**（ADR 0029 决策 1）：它在竖排里占一行，于是每一条消息
        # 都把网格往下挤一段。控件本体与摆位在 ``_build_ui`` 的最后一步建。
        self._grid_page = QWidget()
        grid_layout = QVBoxLayout(self._grid_page)
        grid_layout.setContentsMargins(10, 10, 10, 0)
        grid_layout.setSpacing(8)
        grid_layout.addWidget(self._breadcrumb)
        grid_layout.addWidget(self._grid, 1)

        self._detail = DetailView()
        self._detail.request_close.connect(self.show_grid)
        self._detail.request_step.connect(self.step_detail)
        # 大图页上的右键：视图只报位置，菜单在这一侧拼（与网格同一个流程，见 ADR 0021）。
        self._detail.context_requested.connect(self._show_detail_context_menu)

        # 日历那一页**自己带着工具条**（它占掉的就是网格那页的面包屑那一行，
        # ADR 0033 决策 5），所以这里加进去的是一个完整的一页，不是网格页里的一块。
        self._calendar_page = CalendarPage()
        self._calendar_page.cell_requested.connect(self._on_calendar_cell_requested)
        self._calendar_page.level_changed.connect(self._on_calendar_level_changed)
        # 换层 / 翻页 / 滚动都会改变「现在看得见哪些」，据此重新要缩略图。
        self._calendar_page.window_changed.connect(self._on_calendar_window_changed)
        self._calendar_page.set_today(datetime.date.today())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._grid_page)
        self._stack.addWidget(self._calendar_page)
        self._panel = MetadataPanel()
        self._panel.hide()
        # 「编辑元数据」那个按钮只发信号（面板不碰文件，红线 1）；写盘在 ``write_ops``。
        self._panel.edit_metadata_requested.connect(self.edit_metadata_of_current)
        # 焦点停在面板上时，← / → 仍然换照片（大图页走它自己那条路，网格上挪当前项）。
        self._panel.photo_step_requested.connect(self.step_photo_from_panel)
        self._detail_page = QWidget()
        detail_layout = QHBoxLayout(self._detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_layout.addWidget(self._detail, 1)
        detail_layout.addWidget(self._panel)
        self._stack.addWidget(self._detail_page)
        self.setCentralWidget(self._stack)
        # 内容区往下让出 :data:`TITLE_BAR_EXTRA`（标题栏那一条加高的那一段）。
        # **不是把那一段画在内容上，而是真的给它腾出地方**：加边距之后三页的内容
        # 都从 ``安全区 + EXTRA`` 开始，胶囊与标题文字因此可以摆在多出来的那一段里，
        # 不会像之前那样压住照片区的顶边（ADR 0026 决策 8）。
        if self._owns_title_bar:
            self._stack.setContentsMargins(0, TITLE_BAR_EXTRA, 0, 0)

        # 「用外部应用打开」按钮摆在搜索胶囊**左边**（ADR 0034）。它也是浮在顶边
        # 那一行上的一层，与胶囊同一手法：不进布局、摆位由 ``_layout_title_bar`` 算，
        # 抬一次的理由也一样（Qt 按创建顺序叠兄弟控件）。
        # **建在搜索框之前**：它俩不重叠，但都压在中央控件之上，顺序与「谁先建谁在
        # 下层」保持一致，将来要挪位置时不用重排。
        self._external_button = ExternalAppsButton(self)
        self._external_button.set_entries(self._external_entries())
        self._external_button.entry_activated.connect(self._on_external_entry)
        self._external_button.hide()

        # 搜索框**最后建**：它要压在中央控件之上（候选列表画在窗口内容那一段，
        # 而 Qt 按创建顺序叠兄弟控件 —— 先建的那个被后建的盖住）。
        # 摆位同样由 ``_layout_title_bar`` 负责，那里先算高度再决定要不要摆它。
        self._search_field = SearchField(self)
        self._search_field.submitted.connect(self._on_search_submitted)
        self._search_field.dismissed.connect(self._on_search_dismissed)
        self._search_field.editing_changed.connect(self._on_search_active_changed)
        self._search_field.hide()

        # 横幅**也建在最后**：它是压在中央控件之上的一层，与搜索胶囊同一手法（Qt 按创建
        # 顺序叠兄弟控件，先建的被后建的盖住）。差别是它**不进任何布局** —— 出现与消失
        # 都不动底下任何一样东西（ADR 0029 决策 1），摆位由 ``_layout_banner`` 负责。
        self._banner = BannerOverlay(self)
        # 换页时重新摆：大图页上照片区右边让给元数据面板，居中的位置也就跟着变了。
        self._stack.currentChanged.connect(self._on_page_changed)

        # 标题栏那一行第一次有话可说：还没打开库，所以是「Gallery」（ADR 0025 决策 4）。
        self._refresh_title()
        # 刻意不建状态栏：底部那一条只是为了放扫描进度、照片尺寸、缩放百分比，
        # 换来的是照片区少 20 来像素。出错信息有横幅，提示性信息一律不留 —— 横幅浮在
        # 内容上面，压根不占照片区的那一行（ADR 0029 决策 1）。

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

        # ⇧⌘↑ / ⇧⌘↓：缩略图放大 / 缩小**一档**。捏合与 ⌘+滚轮已经能做同一件事，
        # 但那两条要手正好搭在触控板上；这两条给的是键盘那一路，握着鼠标也能一档一档挪。
        #
        # 为什么不挂 ⌘↑ / ⌘↓：上面那条「上一级」的注释已经说明 ⌘↑ 在 macOS 上被占着。
        # 而 ⌘+ / ⌘- 归大图的缩放（StandardKey.ZoomIn/ZoomOut），网格里换档不能去抢它 ——
        # 两者动的是**不同的东西**（照片的缩放倍率 vs 缩略图的档位）。
        self._thumb_zoom_in_action = QAction("放大缩略图", self)
        self._thumb_zoom_in_action.setShortcut(QKeySequence("Ctrl+Shift+Up"))
        # ``triggered`` 递过来的是 checked（一个 bool），不是档数，所以要包一层。
        self._thumb_zoom_in_action.triggered.connect(
            lambda _checked=False: self.step_thumbnail_size(1)
        )

        self._thumb_zoom_out_action = QAction("缩小缩略图", self)
        self._thumb_zoom_out_action.setShortcut(QKeySequence("Ctrl+Shift+Down"))
        self._thumb_zoom_out_action.triggered.connect(
            lambda _checked=False: self.step_thumbnail_size(-1)
        )
        # **显式挂到窗口上**（与 ``_new_album_action`` / ``_reveal_action`` 同一个做法）：
        # QAction 只有被加进某个控件才会注册进 Qt 的快捷键表。它们同时进了「视图」菜单
        # （见下面那两行 ``view_menu.addAction``），本来也能注册 —— 但快捷键的归属不该
        # 取决于「那一份菜单还在不在」，挂在窗口上才是它自己的保证。
        self.addAction(self._thumb_zoom_in_action)
        self.addAction(self._thumb_zoom_out_action)

        # 回上一层用 Esc：⌘↑ 在 macOS 上已经被占着，我们抢不到。
        # Esc 在大图页是「返回网格」，在网格页才是「上一级」——同一时刻只有一个主人
        # （见 _update_navigation_actions），两处不会互相抢键。
        self._up_action = QAction("上一级", self)
        self._up_action.setShortcut(QKeySequence("Esc"))
        self._up_action.triggered.connect(self.go_up)

        self._albums_action = QAction("相册", self)
        self._albums_action.setCheckable(True)
        self._albums_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_ALBUMS))

        self._flat_action = QAction("全部照片", self)
        self._flat_action.setCheckable(True)
        self._flat_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_FLAT))

        self._calendar_action = QAction("日历", self)
        self._calendar_action.setCheckable(True)
        self._calendar_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_CALENDAR))

        self._metadata_action = QAction("显示元数据信息", self)
        self._metadata_action.setCheckable(True)
        self._metadata_action.setShortcut(QKeySequence("Ctrl+I"))
        self._metadata_action.toggled.connect(self.toggle_metadata)

        self._clear_cache_action = QAction("清空缩略图缓存", self)
        self._clear_cache_action.triggered.connect(self.clear_thumbnail_cache)

        # ⇧⌘N：与访达一致，在当前这一层里建一个相册（ADR 0022）。建完立刻进行内重命名，
        # 因此**不进后台线程** —— 那是一次 mkdir，不是搬几百兆的复制。
        self._new_album_action = QAction("新建相册", self)
        self._new_album_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_album_action.triggered.connect(self.create_album)
        # **必须挂到窗口上**（理由见 ``_reveal_action`` 那段）：QAction 的快捷键只有在
        # 它被加进某个控件之后才注册进 Qt 的快捷键表，而右键菜单是用完即弃的。
        # 挂窗口不会让它冒到菜单栏里 —— 菜单栏只画被塞进某个 ``QMenu`` 的动作。
        self.addAction(self._new_album_action)

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
        # 文案跟着栈顶变，用户按之前就知道会撤掉什么。它住在「编辑」菜单里 ——
        # 那是 macOS 放撤销的标准位置，见 ADR 0014 决策 5。
        self._undo_action = QAction("撤销", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.triggered.connect(self.undo_last_write)

        # ---- 菜单栏与快捷键（见 ADR 0014）--------------------------------
        # 「打开」刻意不给**真**快捷键：⌘O 已经是「打开文件夹…」，而回车网格自己已经处理
        # 了（见 PhotoGrid.keyPressEvent），抢过来只会和它打架。
        #
        # 菜单里那枚「回车」是 ui.menus 挂在**这一次弹出的菜单**上的提示，只画不生效
        # （ShortcutColumnMenu.show_key），这个共用的 QAction 一个字节都没动。
        self._open_entry_action = QAction("打开", self)
        self._open_entry_action.triggered.connect(self.open_current_entry)

        # 「退出」只活在**大图页的右键菜单**里（ADR 0021），是那张菜单的第一行。
        # 与「打开」一样**不给真快捷键**：大图页上的 Esc 归 DetailView.keyPressEvent，
        # 菜单里那枚 ⎋ 是 ui.menus 挂在这一次菜单上的提示，只画不生效。
        self._close_entry_action = QAction("退出", self)
        self._close_entry_action.triggered.connect(self.show_grid)

        # ⇧⏎：与 ⌘⏎ 同一个思路再加一层 —— ⏎ 归「打开」、⌘⏎ 归「在访达中显示」，
        # ⇧⏎ 就归「在默认应用中打开」。三个「打开」因此挨着摆，菜单里画的也是同一列。
        #
        # 与「打开」一样**只活在右键菜单里**：它离不开「当前这一张」，而菜单栏上放一个
        # 没有选区可依的命令没有意义（见下面 ``_copy_metadata_action`` 那段）。
        self._open_default_action = QAction("在默认应用中打开", self)
        self._open_default_action.setShortcut(QKeySequence("Shift+Return"))
        self._open_default_action.triggered.connect(self.open_current_in_default_app)
        # 同样**必须挂到窗口上**才按得出来（理由见 ``_reveal_action`` 那段）。
        self.addAction(self._open_default_action)

        # ⌘⏎：⏎ 归「打开」自己（网格里已经处理了），加上 ⌘ 正好空出来一条，与访达把
        # ⌘C / ⇧⌘C 让给拷贝是一个思路。这条**只作用在网格里**（置灰判据见
        # ``_update_action_states``），所以 ⌘⏎ 在大图页按下去仍然只是回车。
        self._reveal_action = QAction("在访达中显示", self)
        self._reveal_action.setShortcut(QKeySequence("Ctrl+Return"))
        self._reveal_action.triggered.connect(self.reveal_selection)
        # **必须挂到窗口上**（``QWidget.addAction``）这个快捷键才真的按得出来：QAction 的
        # 快捷键只有在它被加进某个控件之后才注册进 Qt 的快捷键表，光给它一个 QAction 父对象
        # 不算数（实测：那样按 ⌘⏎ 什么都不发生）。而右键菜单是用完即弃的，靠它注册等于没注册。
        #
        # 挂窗口**不会**让它冒到菜单栏里：菜单栏只画被塞进某个 ``QMenu`` 的动作。
        self.addAction(self._reveal_action)

        # ⌘C 与访达一致：往剪贴板里放的是**文件本身**，访达里 ⌘V 得到一份拷贝
        # （ADR 0017）。注意放的是路径引用，不读像素、不写文件。
        self._copy_action = QAction("拷贝", self)
        self._copy_action.setShortcut(QKeySequence("Ctrl+C"))
        self._copy_action.triggered.connect(self.copy_selected_files)

        # ⇧⌘C 与访达一致。注意 Qt 在 macOS 上把 Command 写作 Ctrl，不要手动换键。
        self._copy_path_action = QAction("拷贝路径", self)
        self._copy_path_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_path_action.triggered.connect(self.copy_selected_paths)

        # ⌘V 与访达一致：把剪贴板里的文件**复制**进当前这一层的目录（ADR 0018）。
        # 它是这三个写操作里唯一不进撤销栈的 —— 撤销粘贴就是删文件（红线 1）。
        #
        # **相册菜单里那一条也是它**（ADR 0018 决策 7）：那一行因此照常写着 ⌘V，而且
        # **按 ⌘V 与点它走的是同一个动作、同一个槽** —— 界面上写着什么，按下去就是什么，
        # 不会有两处说法。粘到哪里由 :meth:`_paste_target` 说了算（选中一个相册就粘进那个
        # 相册，否则粘进当前这一层），右键那一格本来就会把它选中。
        self._paste_action = QAction("粘贴", self)
        self._paste_action.setShortcut(QKeySequence("Ctrl+V"))
        self._paste_action.triggered.connect(self.paste_into_current_folder)
        # 剪贴板一变就重算 ⌘V：在访达里 ⌘C 之后切回 Gallery 直接按 ⌘V，得有反应
        # （不然它还停在「剪贴板是空的」那一次算出来的置灰上）。连接随窗口析构自动
        # 摘掉，见 ``closeEvent`` 那段注释。
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

        self._select_all_action = QAction("全选", self)
        self._select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        self._select_all_action.triggered.connect(self.select_all_entries)

        # 只活在右键菜单里的两条：它们离不开「当前这一张」，放进菜单栏反而没处可用。
        self._copy_metadata_action = QAction("复制元数据", self)
        self._copy_metadata_action.triggered.connect(self.copy_current_metadata)

        # 「编辑元数据…」⇧⌘I（ADR 0030 / 0032）：**本项目里第一个改动用户照片内容的
        # 命令**。与「重命名」一样只对单张照片成立，与「设为相册封面」一样只活在右键菜单
        # 里 —— 但它要一个真快捷键：改拍摄时间或位置是一件经常做的事，而访达的照片
        # 「简介」也是 ⌘I。
        self._edit_metadata_action = QAction("编辑元数据…", self)
        self._edit_metadata_action.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self._edit_metadata_action.triggered.connect(self.edit_metadata_of_current)
        self.addAction(self._edit_metadata_action)

        self._copy_group_action = QAction("复制分组条件", self)
        self._copy_group_action.triggered.connect(self.copy_current_group)

        # 「设为相册封面」/「取消相册封面」（ADR 0028）：同样只活在右键菜单里，理由与
        # 上面那两条一模一样 —— 都要「当前这一格」才有对象（前者是一张照片，后者是一个
        # 相册）。**刻意不给真快捷键**：⇧⌘C 已经是「拷贝路径」，而这两条一年也用不到
        # 几次，为它抢一个常用键位不值当。
        self._set_cover_action = QAction("设为相册封面", self)
        self._set_cover_action.triggered.connect(self.set_selected_as_album_cover)

        self._clear_cover_action = QAction("取消相册封面", self)
        self._clear_cover_action.triggered.connect(self.clear_album_cover)

        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._quit_action.triggered.connect(self.close)

        # ⌘F 与访达一致：不是弹一个对话框，是**把光标放进标题栏那个胶囊**。
        # 检索的那一套全在窗口顶上（ADR 0026），弹窗等于把界面挪到别处去。
        self._search_action = QAction("搜索…", self)
        self._search_action.setShortcut(QKeySequence("Ctrl+F"))
        self._search_action.triggered.connect(self.focus_search)
        # **必须挂到窗口上**（理由见 ``_reveal_action`` 那段）才按得出来。
        self.addAction(self._search_action)

        # 条件是临时的，但「我刚才搜的那一串」不该靠记忆留着 —— 一条命令清光它。
        # 没有条件时整条**不出现**（不是置灰）：菜单里那个位置平时就该是空的。
        self._clear_search_action = QAction("清除搜索条件", self)
        self._clear_search_action.triggered.connect(self.clear_search_conditions)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        # 「搜索…」单独成一段：它是窗口顶上那一个胶囊的入口，与「往库里放东西」
        # （新建相册、改名、移到废纸篓）不是一族。
        file_menu.addAction(self._search_action)
        # 「清除搜索条件」没有条件时**整条不出现**（``QAction.setVisible`` 就是
        # 「不进菜单」），与 ADR 0014 决策 4 那条取向一致：不该出现的一条摆着置灰，
        # 只会让人以为它此刻还能点什么。
        self._clear_search_action.setVisible(False)
        file_menu.addAction(self._clear_search_action)
        file_menu.addSeparator()
        file_menu.addAction(self._new_album_action)
        file_menu.addAction(self._rename_action)
        file_menu.addAction(self._trash_action)
        file_menu.addSeparator()
        file_menu.addAction(self._clear_cache_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        # 「编辑」：全选、拷贝、拷贝路径、粘贴、撤销 —— 与 macOS 的摆放一致
        # （ADR 0014 决策 5）。⌘Z 从「文件」搬过来了：撤销是编辑动作，放在文件菜单里
        # 会让 ⌘Z 与「打开 / 移到废纸篓」这些会动文件的命令混在一起。
        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self._select_all_action)
        edit_menu.addAction(self._copy_action)
        edit_menu.addAction(self._copy_path_action)
        edit_menu.addAction(self._paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self._undo_action)

        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self._back_action)
        view_menu.addAction(self._up_action)
        view_menu.addSeparator()
        browse_menu = view_menu.addMenu("浏览方式")
        # 顺序与访达一致：先「全部照片」（整库平铺，最常用），再「相册」（逐层进入），
        # 最后「日历」。菜单里的先后只影响眼睛扫过去的速度，三者是互斥的勾选。
        browse_menu.addAction(self._flat_action)
        browse_menu.addAction(self._albums_action)
        browse_menu.addAction(self._calendar_action)
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
        # 「放大 / 缩小缩略图」与「缩略图大小」是同一件事的两种说法：前两条挪一档，
        # 子菜单直接挑一档。挨着摆，用户按 ⇧⌘↑ 时不用先想「这条在哪一栏」。
        view_menu.addAction(self._thumb_zoom_in_action)
        view_menu.addAction(self._thumb_zoom_out_action)
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
        # 补发一次底色。菜单栏里这些 QMenu 是**此刻**才建出来的，而统一底色是在
        # ``_build_ui`` 里下发的（见 ``ui.theme``）—— 那时它们还不存在，于是各自
        # 用着系统菜单色（浅色 ``#ececec`` / 深色 ``#2b2b2b``），与窗口的纯色底差
        # 一档。按下菜单的那一瞬，割裂就是这么来的。
        #
        # 它们是菜单栏的子控件，重新下发就会走到它们；而这一发也让之后每一次
        # 系统换浅色 / 深色时，它们的底色跟着窗口一起变。
        theme.ensure_installed()

    # ---- 每一条命令此刻能不能按 ----------------------------------------------

    def _update_action_states(self) -> None:
        self._update_navigation_actions()
        in_detail = self._in_detail_page()
        in_grid = self._stack.currentWidget() is self._grid_page
        # 改名的过程中别启用别的会动文件的命令 —— 那时键盘归输入框，⌘⌫ 不该把正在编辑的
        # 那张删掉。**两个输入框都算**：网格那个与标题栏那个（ADR 0025 决策 6），
        # 标题栏右上角的搜索框也是（ADR 0026）—— 那时键盘归它，⌘⌫ 要删的是搜索框
        # 里刚敲的字。
        idle = (
            not self._grid.is_renaming()
            and not self._title_bar.is_editing()
            and not self._search_field.is_editing()
        )
        for action in (
            self._fit_action,
            self._actual_action,
            self._zoom_in_action,
            self._zoom_out_action,
        ):
            action.setEnabled(in_detail)
        # 缩略图那两条正好反过来，只在网格页亮着：大图页里 ⌘+ / ⌘- 才是缩放，
        # 而 ⇧⌘↑ ⇧⌘↓ 在那一页没有对应的意思（照片那边只有倍率，没有「档」）。
        # 改到一半也不亮：换档会重排网格，把正在编辑的那一格从输入框底下挪走。
        for action in (self._thumb_zoom_in_action, self._thumb_zoom_out_action):
            action.setEnabled(in_grid and idle)
        # 「退出」也是 Esc，所以改到一半要把它关掉：那一刻 Esc 是输入框的「取消改名」，
        # 与「上一级」在网格页上的让位是同一条理由（见 ``_update_navigation_actions``）。
        self._back_action.setEnabled(in_detail and idle)
        self._clear_cache_action.setEnabled(self._library is not None and self._cache_job is None)
        # 「清除搜索条件」**整条出现 / 消失**，不是置灰（ADR 0014 决策 4）：
        # 没有条件时它压根没有对象，摆一个灰着的「清除」只会让人去猜在清什么。
        self._clear_search_action.setVisible(bool(self._conditions))
        # 搜索框摆在标题栏那一行上（ADR 0026 决策 8），而**那一行只在 macOS 上存在**
        # （ADR 0025 决策 2）。别的平台上这一条老实置灰，而不是按了没反应。
        self._search_action.setEnabled(self._owns_title_bar)
        # 「改名」落在哪儿：大图页改的是窗口标题栏那一行（ADR 0025），网格里是行内编辑。
        # 没有标题栏那一段的平台上（见 ``theme.unify_title_bar``）大图页没有改名字段，
        # 那一侧仍旧是网格的事。
        renaming_here = in_grid or (in_detail and self._title_bar.isVisible())
        # 移到废纸篓两边都成立：大图页删的是正在看的那一张（``_selected_entries`` 认这一页）。
        #
        # **日历那一页两者都不是**：它的选区是「一段日子里的照片」，没有行号，
        # 而网格的当前项还停着上一次在网格上点的那一格 —— 拿它算可用性，那些
        # 按钮就会亮着一条指向别处的路。日历里点照片是直接进大图页，
        # 于是这一页上它们统统关掉（大图页上它们照常按正在看的那一张算）。
        in_calendar = self._stack.currentWidget() is self._calendar_page
        # 「用外部应用打开」**只在那一页有东西可开的时候出现**（ADR 0034）：日历页有
        # 「哪一层、哪一天」可言，其余页面有「当前在哪个文件夹」可言；还没打开库的欢迎
        # 页两样都没有，于是按钮整个收起来 —— 摆一个点开是空菜单的图标，用户只会
        # 以为坏了。
        #
        # 判据是 ``is_available()`` 而不是 ``in_calendar``：**摆不摆由下拉框里有没有
        # 条目说了算**，于是「哪些页面上有什么」只有那一处知道。
        # **摆位不在这里算**：``_layout_title_bar`` 看见它可见就会把它摆对并让出宽度。
        self._external_button.refresh()
        self._external_button.setVisible(
            self._owns_title_bar and self._external_button.is_available()
        )
        self._layout_title_bar()
        entries = [] if in_calendar else self._selected_entries()
        focus_row = -1 if in_calendar else self._focus_row()
        # 「改名」只对磁盘上的**某一个**东西成立：分组卡片不是磁盘上的东西，改不了名，
        # 一次也只改一条（见 ADR 0009 与 ADR 0004）。网格页要连**当前项**一起算
        # —— 它可能不在选区里；大图页指向的必然是正在看的那张照片，看它就够了。
        considered: tuple[int, ...] = (
            (*self._grid.selected_rows(), self._grid.current_row())
            if in_grid
            else (self._current_row,)
        )
        file_only = not any(
            isinstance(self._model.entry_at(row), GroupRef) for row in considered if row >= 0
        )
        # 「移到废纸篓」与「拷贝」一族作用于**摊平之后**的选区：选中一张分组卡片就是
        # 选中它代表的那批组员（ADR 0020），与「进去全选再删」是同一件事。大图页上是
        # 正在看的那一张，摊平对它没有影响。
        flat = flattened_entries(entries)
        self._rename_action.setEnabled(renaming_here and focus_row >= 0 and idle and file_only)
        self._trash_action.setEnabled(bool(flat) and idle)
        # 右键新增的那几条（ADR 0014）：可用性一律按「此刻作用于谁」算，
        # 菜单栏上的 ⌘A / ⇧⌘C 与右键菜单里的同一份动作因此永远说同一句话。
        paths = [entry.path for entry in flat]
        current = self._model.entry_at(focus_row) if focus_row >= 0 else None
        self._select_all_action.setEnabled(in_grid and self._model.rowCount() > 0)
        self._copy_path_action.setEnabled(bool(paths))
        self._copy_action.setEnabled(bool(paths))
        # ⌘V 与选区无关：只看「当前这一层有没有可粘的目录」与「剪贴板里有没有文件」。
        # 粘不了时它同时也不会出现在右键菜单里（见 ``_menu_actions``）。**大图页不粘**：
        # 那一页根本没有「当前这一层」，粘到哪要另说（ADR 0021）。
        self._paste_action.setEnabled(in_grid and self._can_paste())
        # ⇧⌘N 与选区无关：只看「当前这一层有没有这个目录」（ADR 0022 决策 1）。
        # 分组里没有 —— 那一层摆的全是照片，插一张相册卡片进去是错位的；
        # 改到一半也不行，与改名 / 删除同一个 ``idle``：那时键盘归输入框。
        self._new_album_action.setEnabled(in_grid and idle and self._can_create_album())
        # ⌘⏎ 与改名输入框抢键：改到一半键盘归输入框（与 rename / trash 同一个 ``idle``），
        # 不然「敲完名字顺手 ⌘⏎」会变成把访达叫起来。
        self._reveal_action.setEnabled(bool(paths) and idle)
        self._open_entry_action.setEnabled(current is not None)
        # ⇧⏎ 与 ⌘⏎ 同一套判据，只是更窄一层：当前这一格得是**照片**、且没在改名字。
        # 相册与分组卡片这一条不出现（见 ``open_current_in_default_app``）。**大图页上也可用**
        # （ADR 0021）：菜单里那一条亮着，按下去就该是那一行说的事 —— 顺带说一句，这会改掉
        # 「大图下按 ⇧⏎ 是回车」这个原先的巧合：现在 ⇧⏎ 交给系统，``Esc`` / ``⏎`` 才是退出。
        self._open_default_action.setEnabled(idle and isinstance(current, PhotoRef))
        self._copy_metadata_action.setEnabled(isinstance(current, PhotoRef))
        # 「编辑元数据」在右键菜单里是「不出现」，在菜单栏 / 快捷键这边是「置灰」——
        # 菜单栏那一处永远摆着（否则 ⇧⌘I 就没地方显示提示了），所以两条判据必须同源：
        # ``metadata_edit_candidate``。改到一半也关掉，与改名 / 删除同一个 ``idle``。
        self._edit_metadata_action.setEnabled(self.metadata_edit_candidate() is not None and idle)
        self._copy_group_action.setEnabled(in_grid and isinstance(current, GroupRef))
        # 「退出」只出现在大图页的右键菜单里（ADR 0021），所以它也只在那儿可用。
        self._close_entry_action.setEnabled(in_detail)
        self._update_undo_action()

    def _schedule_action_states(self, *_args: object) -> None:
        """选区变了：把全量重算**攒到这一轮事件循环结束时**做一次。"""
        self._action_states_timer.start()

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

    def enter_folder(self, directory: Path, *, focus: EntryKey | None = None) -> None:
        """进入一个相册（子文件夹）。库以外的路径一律拒绝。

        :param focus: 到了这一层该选中哪一格。``Esc`` 往上一层退时传的是「刚离开的那个
            相册」，于是退回去时网格上仍然停在它身上（见 :meth:`go_up`）。

        **进目录就是退出检索**：两者都是「一层」，面包屑上那一段只有一个是当前所在
        （ADR 0026）。条件不清掉的话，检索会在下一次 ``_start_scan`` 里抢在目录前面 ——
        用户点开一个相册，看到的却是全库的检索结果。

        **存进去的是解析过的路径**（与 ``Library.root``、扫描出来的条目、监听器报回来的
        变化同一套写法）。少了这一步，路径里带软链接的库（``/var`` → ``/private/var``、
        用户自己链过去的目录）会安静地出毛病：监听器报回来的是真路径，而当前这一层
        存的是原写法，于是「是不是在当前这一层」永远说不成 —— 访达里丢进去一张照片，
        网格当没看见（ADR 0018 的粘贴同样栽在这里，网格里长不出新照片）。
        """
        library = self._library
        if library is None:
            return
        target = Path(directory)
        if not paths.is_within(library.root, target):
            return
        self._current_dir = target.resolve()
        self._group_view = None
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None
        self._reload_level(focus=focus)

    def go_up(self) -> None:
        """回到上一层：有筛选条件就撤掉最后一条，否则按分组 / 相册往上退。

        分组比文件夹深一级（它是文件夹之上的一个视图），所以先退它；
        **筛选条件又深一级**（它跨着整个库，见 ADR 0026 决策 9）—— 与访达
        「一层一层退」是同一套心智，所以走的是同一个 ``Esc``。

        退上去时**把刚离开的那一级留在选中状态**：从相册退回去选中的是那个相册卡片，
        从分组退回去选中的是那张组卡片（访达里退上一层就是这个样子）。

        只此一处例外。再**点进去**是另一次浏览：那一层不记着自己上一次停在哪一格，
        网格上不会留着上一次的选中（见 :meth:`_arm_focus`）。
        """
        library = self._library
        if library is None:
            return
        if self._conditions:
            self._conditions.pop()
            self._editing_condition = None
            self._apply_conditions()
            return
        if self._drill_photos is not None:
            self._leave_calendar_drill()
            return
        if self._group_view is not None:
            self._leave_group()
            return
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return
        current = self._current_dir or library.root
        parent = parent_of(library.root, current)
        if parent is not None:
            self.enter_folder(parent, focus=path_key(current))

    def _can_go_up(self) -> bool:
        """现在退不退得回去。菜单里的「上一级」与双击空白处都问它。

        判据与 :meth:`go_up` 一致，否则菜单灰着、双击却退了回去（或者反过来）。
        """
        library = self._library
        if library is None or self._grid.is_renaming():
            return False
        if self._conditions:
            return True  # 有得撤：撤一条筛选条件就是「上一层」（与 go_up 同一套判据）
        if self._drill_photos is not None:
            return True  # 日历点出来的那一格，退回去回日历（与 go_up 同一套判据）
        if self._group_view is not None:
            return True
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return False
        return parent_of(library.root, self._current_dir or library.root) is not None

    def _on_background_activated(self) -> None:
        """双击了网格里没有格子的地方：退回上一层，与 `Esc` 是同一件事。

        这一层空着（空相册、空分组）时尤其需要它 —— 屏幕上根本没有可双击的东西，
        而这正是最想立刻退出去的时候。已经退不动时就安静地什么都不做：到了头本来就是
        一个合法状态，不该为它弹一句话提醒。
        """
        if self._grid.is_renaming():
            # 改到一半：这一下先归输入框。既不把人带走，也不打扰他。
            return
        if self._can_go_up():
            self.go_up()

    def _on_breadcrumb_requested(self, directory: object) -> None:
        if isinstance(directory, Path):
            self.enter_folder(directory)

    def _reload_level(self, *, focus: EntryKey | None = None) -> None:
        """重新列出当前层（切模式、进入或返回相册时都走这里）。

        :param focus: 到了这一层要选中哪一格。**只有往上一层退才会给**（那个相册 / 那个组），
            其余由 :meth:`_arm_focus` 自己判断：还是这一层就跟着停着的那一格走，换了层
            就谁也不选。
        """
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        self._arm_focus(focus)
        # 换代：让已经在路上的旧结果作废。
        self._generation += 1
        self._grid.cancel_rename()
        self._cancel_facet_job()
        self._facet_values = {}
        self._model.clear()
        # 网格先清干净，不靠模型 reset 的副作用：这一层可能一个格子都没有（空相册），
        # 而「谁也没选中」正是要摆出来给人看的一个状态。当前项留着的话，⌘R / ⌘⌫ 的可用性
        # 判据里它仍然算数 —— 菜单亮着，作用的对象却不存在。该恢复的那一格由
        # :meth:`_restore_focus` 在这一层排好之后重新选中。
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = []  # 换层要重扫，攒着，别先摆一屏旧顺序
        self._level_notice = False
        self._progress_notice = False
        # 换层只把横幅收掉，**不打断**全库解析：它是整个库的事，与当前在哪一层无关。
        self._place_notice_text = None
        self._scan_issue = False
        self._current_row = -1
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        self._stack.setCurrentWidget(self._grid_page)
        self._update_breadcrumb()
        self._update_action_states()
        self._start_scan()

    # ---- 到了这一层该选中哪一格 ----------------------------------------------
    #
    # 三种情形，三种答案：
    #
    # - **往上一层退**（``go_up`` / ``_leave_group`` 显式给出）：网格上仍停着刚离开的
    #   那个相册 / 那个组。不然每退一级都要重新找一遍自己刚才在哪儿。
    # - **还是这一层，重排一遍**（换排序 / 换分组 / 正序逆序 / 扫描在途时决定重来）：
    #   停着的那一格还在，跟着它走 —— 是同一批照片换了个位置，不该把人甩掉。
    # - **换了层**（进入相册 / 进入分组 / 切浏览方式，含退上去之后再点进去）：
    #   **谁也不选**。文件浏览里「重新进入一层」是一次新的浏览：把上一次的选择留着，
    #   用户看到的是上一趟留下的高亮，而随手一个 ⌘⌫ 删掉的是他这一趟并没有指过的
    #   东西 —— 选中框画在那儿，看着却像「我已经选好了」。
    #
    # 三者都靠**标识**而不是行号（重扫、重排、换排序之后行号就变了，标识没变），标识由
    # ``ui.navigation`` 的纯函数算（见 ``tests/ui/test_navigation.py``）。哪一条属于哪一
    # 种情形不靠调用方记得传参，而是比一比**层标识**—— 见 :meth:`_arm_focus`。

    def _arm_focus(self, focus: EntryKey | None = None) -> None:
        """记下到了新的一层该选中哪一格。

        :param focus: 显式指定（往上一层退时给的那个相册 / 那个组）。不给就是让这里自己
            判断：**还是刚才那一层**就把停着的那一格记下来跟着它走，**换了层**就谁也不选。

        判断靠比层标识（见 ``navigation.level_key``）：``enter_folder`` / ``enter_group``
        都是先改状态再重载，所以问「现在在哪一层」得到的是**新的**那一层 —— 正好拿来
        和 ``self._level_key``（模型里此刻摆着的那一层）比。

        **顺序要紧**：先把「还是同一层吗」问完，再把 ``self._level_key`` 换成新的那一层。
        反过来的话，两边永远相等，于是「换了层就什么都不记」这条永远轮不到执行 ——
        剩下的全靠「记下来的那一格在新的一层里恰好不存在」蒙对。
        """
        remembered = self._shown_entry_key()
        self._level_key = self._current_level_key()
        self._focus = focus if focus is not None else remembered
        self._focus_restored = False

    def _current_level_key(self) -> LevelKey:
        """当前这一层的标识（见 ``navigation.level_key``）。"""
        group = None if self._group_view is None else (self._group_view.facet, self._group_view.key)
        conditions = [condition.identity for condition in self._conditions]
        return level_key(self._browse_mode, self._current_dir, group=group, conditions=conditions)

    def _shown_entry_key(self) -> EntryKey | None:
        """网格上此刻停着的那一格是谁；不是同一层（或者压根没有）就是 ``None``。"""
        if self._level_key is None or self._level_key != self._current_level_key():
            return None
        entry = self._model.entry_at(self._grid.current_row())
        return None if entry is None else entry_key(entry)

    def _restore_focus(self) -> None:
        """这一层排好之后，把该选中的一格选中。

        **每一层只摆一次**：摆完之后就是用户自己的选择了，后台再重排一次（监听器来了
        新照片、换排序）不该把它拽回去 —— 那种重排由 ``_reorder_level`` 的锚点负责。

        找不到就什么都不选：那一格多半已经被改名、移走或删掉了，界面此时也说不清该选谁，
        与其挑一个错的，不如让这一层从零开始。
        """
        if self._focus_restored:
            return
        self._focus_restored = True
        key, self._focus = self._focus, None
        if key is None:
            return
        row = self._row_of_focus(key)
        if row >= 0:
            self._grid.select_row(row)  # 顺带滚过去：这一层可能长到一屏之外

    def _row_of_focus(self, key: EntryKey) -> int:
        """该选中的那一格现在在第几行（``-1`` = 这一层里已经没有了）。

        照片与相册走模型现成的路径索引；组卡片没有索引（它的键不是路径），扫一遍这一层
        —— 组卡片本身就不多，而这一层里的照片可能有几万张，所以两条路不能合成一条。
        """
        if key[0] == ENTRY_KIND_PATH:
            return self._model.row_of_path(Path(key[1]))
        return row_of_entry_key(self._model.entries(), key)

    def _group_view_label(self) -> str | None:
        """当前所在分组的显示名（没在分组里就是 ``None``）。"""
        return None if self._group_view is None else self._group_view.label

    def _update_breadcrumb(self) -> None:
        library = self._library
        # 平铺模式下平时不显示面包屑，但**分组里必须显示**：那里没有文件夹可指，
        # 只有一条「库名 ▸ 2024 年 5 月」才说得出自己在哪。
        #
        # **有筛选条件时一律显示**（ADR 0026 决策 9）：条件总得有个地方摆，
        # 而平铺模式下没有目录可指 —— 那正是「不显示」的理由，于是这里只剩它了。
        in_albums = self._browse_mode == BROWSE_MODE_ALBUMS
        conditions = [condition.chip_text for condition in self._conditions]
        if self._drill_photos is not None:
            # 日历点出来的那一格不是文件夹，面包屑没有一段能指；标题里已经写着
            # 「9月12日 19:00 · 48 张」，那一行摆在这里只会重复还多一条假路径。
            self._breadcrumb.hide()
            return
        if library is None or (not in_albums and self._group_view is None and not conditions):
            self._breadcrumb.hide()
            return
        if conditions:
            # **检索时只报库名：既不报当前文件夹，也不报当前那一组**。检索横跨整个库
            # （ADR 0026 决策 1），于是那一行若是「照片 ▸ 旅行 ▸ 2024 ▸ 搜索结果（4）」，
            # 读起来就是「在这个文件夹里搜到 4 张」；带着组名更糟 —— 「照片 ▸ 2024 年 5 月
            # ▸ 搜索结果（3）」读起来是「这一组里搜到 3 张」，而那 3 张大半在别的组、别的
            # 相册（实测：一个**空组**里搜出 3 张全库照片，面包屑还写着那个组）。
            #
            # 那一行在说一件不存在的事，比什么都不说更坏。范围要收窄自己加一条
            # 「#相册 …」，那一条就明明白白写在右端。
            #
            # ``_group_view`` 与 ``_current_dir`` **都留着**：撤光条件之后要回到原来
            # 那一组、那一层去（见 ``_start_scan``：条件为空时才轮到分组那条路）。
            parts = [(library.display_name, library.root)]
        else:
            parts = (
                breadcrumb_parts(library.root, self._current_dir or library.root)
                if in_albums
                else [(library.display_name, library.root)]
            )
        # 分组那一段同理：条件还在的时候它说的不是检索范围，而是「我进来过哪一组」。
        group = None if conditions else self._group_view_label()
        editing = self._editing_condition if conditions else None
        self._breadcrumb.set_path(
            parts,
            group=group,
            conditions=conditions,
            result_count=self._search_total if conditions else None,
            editing=editing,
        )
        self._breadcrumb.show()

    def _update_mode_actions(self) -> None:
        self._albums_action.setChecked(self._browse_mode == BROWSE_MODE_ALBUMS)
        self._flat_action.setChecked(self._browse_mode == BROWSE_MODE_FLAT)
        self._calendar_action.setChecked(self._browse_mode == BROWSE_MODE_CALENDAR)

    def _update_navigation_actions(self) -> None:
        # 「上一级」只在网格页启用：大图页的 Esc 是「返回网格」，两个动作共用一个 Esc，
        # 必须有一个是关着的，否则同一按键落到谁身上说不准。改名的过程中也不启用 ——
        # 那时 Esc 是输入框的「取消改名」（输入框还会先抢一次按键，见 RenameEdit.event）。
        # **两个输入框都算**（ADR 0025 决策 6）。
        in_grid = self._stack.currentWidget() is self._grid_page
        idle = not self._grid.is_renaming() and not self._title_bar.is_editing()
        self._up_action.setEnabled(in_grid and idle and self._can_go_up())

    # ---- 窗口 --------------------------------------------------------------

    def _is_title_bar(self, point: QPointF) -> bool:
        """这一点是不是落在标题栏那一段（窗口顶边与内容区之间）。"""
        central = self.centralWidget()
        return central is not None and point.y() < central.geometry().top()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """按住标题栏那一段拖动窗口。

        内容铺到标题栏底下之后（见 ``ui.theme.unify_title_bar``），那一条已经是我们的
        客户区，系统不再代劳拖动，得自己发起。落在标题栏里的按下就交给 ``startSystemMove``；
        它返回假（平台不支持、或系统其实自己会拖）就照常往下传，不影响别处的点击。

        **不用先问 ``TitleBar`` 在不在那儿**：不在编辑时它对鼠标是透明的（编辑时要摘掉，
        否则输入框收不到点击），按下照旧落到窗口自己身上。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._is_title_bar(event.position()):
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    # ---- 标题栏（见 ADR 0025）-----------------------------------------------

    def _in_detail_page(self) -> bool:
        """现在是不是大图页。「此刻作用于谁」的判据都问它（ADR 0021 决策 3）。"""
        return self._stack.currentWidget() is self._detail_page

    def _refresh_title(self) -> None:
        """窗口顶边那一行显示什么：**大图页是正在看的那张照片的文件名，其余是相册名**。

        判据只有这一段 —— 「现在在哪一页 + 那一格是谁」—— 于是翻图、退回网格、改名
        （含 ``⌘Z`` 撤回）、换库走的都是同一段算术，不会有一处忘了更新而标题还写着旧名
        （那恰恰是用户正盯着改的名字）。
        """
        photo = self._model.photo_at(self._current_row) if self._in_detail_page() else None
        if photo is not None:
            name = photo.name
        elif self._drill_photos is not None:
            # 从日历某一格摊开的那一层：标题要说得清在看哪一格，否则一屏照片
            # 与「全部照片」长得一模一样，用户不知道自己在日历里的哪个位置。
            name = f"{self._drill_label} · {len(self._drill_photos)} 张"
        else:
            name = self._library_title
        if self._owns_title_bar:
            self._title_bar.set_text(name)
        else:
            # 标题栏那一段不归我们（例如 Linux），照旧交给系统画（ADR 0025 决策 2）。
            self.setWindowTitle(name)

    def _layout_title_bar(self) -> None:
        """把 ``TitleBar`` 与搜索胶囊摆在窗口顶边那一条上。

        那一条的高度是**安全区**顶出来的那 28 点（见 ``theme.unify_title_bar``），不写死。
        全屏时系统自己也不画标题栏，安全区让位，于是这一条跟着收起来 —— 否则它会盖在
        照片上面（ADR 0025 决策 2）。

        **两个控件是摆在一起的，不是一套布局**：``TitleBar`` 那一行由标题文字与改名字段
        自己居中，胶囊要贴右边，两者抢同一份布局反而互相挪（ADR 0026 决策 8）。
        """
        if not self._owns_title_bar:
            return
        if self.isFullScreen():
            # 全屏时系统自己也不画标题栏，这一整条跟着收起来（ADR 0025 决策 2）：
            # 高度给 0，于是它与胶囊都不会盖在照片上。
            self._title_bar.setGeometry(0, 0, self.width(), 0)
            self._title_bar.hide()
            self._search_field.hide()
            self._external_button.hide()
            return
        central = self.centralWidget()
        top = central.geometry().top() if central is not None else 0
        # 安全区之外**再加** :data:`TITLE_BAR_EXTRA`：中央控件已经用同样的边距把三页
        # 往下推了（见 ``_build_ui``），于是多出来的那一段是真的空出来的，不是盖在
        # 内容上的 —— 胶囊是画的，压上去就等于在照片区顶边摆了一块底色。
        height = max(top + TITLE_BAR_EXTRA, TITLE_BAR_HEIGHT)
        self._title_bar.setGeometry(0, 0, self.width(), height)
        self._title_bar.setVisible(True)
        # 胶囊摆在**这一条**里：内容区从 ``top + EXTRA`` 开始，两者首尾相接。
        #
        # 再往下挪 :data:`SEARCH_FIELD_TEXT_ALIGN_DROP` 那一格：中线是**格子**的中线，
        # 而眼睛看的是标题那行字的墨迹 —— 截图量过两者的中线，差多少、往哪边差，
        # 都写在 ``search_field.TEXT_ALIGN_DROP`` 那里了。
        field_height = min(SEARCH_FIELD_HEIGHT, height - 4)
        search_x = self.width() - SEARCH_FIELD_WIDTH - SEARCH_FIELD_MARGIN_RIGHT
        self._search_field.setGeometry(
            search_x,
            (height - field_height) // 2 + SEARCH_FIELD_TEXT_ALIGN_DROP,
            SEARCH_FIELD_WIDTH,
            field_height,
        )
        self._search_field.show()
        # **要抬一次**：窗口布局跑完之后，中央控件与菜单栏会被抬到「最后创建的子控件」
        # 之上（实测它们就是这么盖住了胶囊），而胶囊是要画出来的 —— 被盖住就等于窗口上
        # 根本没有那个东西。``show()`` 对已经可见的控件不再发 ``showEvent``，所以抬的动作
        # 得写在这里，缩放窗口时也一并生效。
        self._search_field.raise_()

        # 「用外部应用打开」紧贴胶囊**左边**（ADR 0034）。**从胶囊那一块往左长出来**
        # （见 ``button_rect``），于是两者必然同高、同一基线 —— 各自拿标题条的高度
        # 各算一遍的话，胶囊为了字的中线往下挪过 ``TEXT_ALIGN_DROP`` 而按钮没挪，
        # 肉眼就是「歪了」。
        #
        # **摆不摆由 ``_update_action_states`` 说了算**（这一页有东西可开才有），
        # 这里只管在它该在的时候把它摆对 —— 于是显隐与几何各归一处，不互相猜。
        if self._external_button.isVisible():
            self._external_button.setGeometry(button_rect(self._search_field.geometry()))
            self._external_button.raise_()
        # 标题文字与 ``⌘R`` 的输入框都要给右上角这一整块让出来：胶囊**加上左边的按钮**
        # （不加上它的话，一个长相册名会从按钮底下穿过去）。
        self._title_bar.reserve_right(
            SEARCH_FIELD_WIDTH
            + 2 * SEARCH_FIELD_MARGIN_RIGHT
            + (BUTTON_WIDTH + BUTTON_GAP if self._external_button.isVisible() else 0)
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().showEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def _on_title_rename_committed(self, text: str) -> None:
        """标题栏里确认了一个新名字。

        **与网格那个输入框走同一个写操作入口**（``write_ops._on_rename_committed``），
        因此记账、横幅、撤销、监听器回声一样都不少 —— 换的只是「在哪一行上改」
        （红线 1、ADR 0025 决策 3）。
        """
        self._on_rename_committed(self._focus_row(), text)

        # ---- 用外部应用打开（ADR 0034）------------------------------------

    def _external_entries(self) -> tuple[ExternalEntry, ...]:
        """下拉框里那几项。**每一项自己判自己此刻该不该出现**（见
        :attr:`~gallery.ui.external_button.ExternalEntry.enabled`），于是列表不用随
        页面变化重建，翻页时只 :meth:`~gallery.ui.external_button.ExternalAppsButton.refresh`
        问一遍就够。
        """
        return (
            ExternalEntry(
                id="google_calendar",
                name="Google 日历",
                icon=calendar_icon(),
                enabled=self._on_calendar_page,
            ),
            ExternalEntry(
                id="finder",
                name="访达",
                icon=folder_icon(),
                # **与日历那一项互补**：日历页给 Google 日历，其余页给访达 ——
                # 每一页只有一样可开的东西，菜单里就不会出现两条与这一页无关的。
                enabled=lambda: not self._on_calendar_page() and self._current_folder() is not None,
            ),
        )

    def _on_external_entry(self, entry: object) -> None:
        """下拉框里点了哪一项。**按 id 分派**，不在按钮那一侧写死（理由见
        :class:`~gallery.ui.external_button.ExternalEntry`）。
        """
        if not isinstance(entry, ExternalEntry):
            return
        if entry.id == "google_calendar":
            self._open_google_calendar()
        elif entry.id == "finder":
            self._open_finder()

    def _open_google_calendar(self) -> None:
        """把 Google 日历开到与当前这一页一致的那一层、那一天。

        **日期取「选中的一天，没有选中就取锚点」** ——「有日期选择就用选择的」就落在
        这一行。周视图把那一天递过去即可：两边的周都从周日起，Google 自己会算出同一周。

        **整个过程进后台线程**：装没装 PWA 要扫一遍应用包，启动了还要等浏览器起来、
        等窗口出现，实测冷启动一两秒、机器忙时更久。主线程一秒都不该被占着。
        """
        page = self._calendar_page
        level = page.level()
        day = page.selected_day() or page.anchor()
        self._runner.submit(
            lambda _context: launch.open_google_calendar(level, day),
            on_result=self._on_external_opened,
            on_error=self._on_external_failed,
        )

    def _on_external_opened(self, outcome: object) -> None:
        if not isinstance(outcome, launch.OpenOutcome):
            return
        # **说过就走**：开都开完了，用户看的应该是那个窗口而不是这条消息（ADR 0029）。
        self._show_banner(banners.external_app_opened(outcome, "Google 日历"), auto_hide=True)

    def _on_calendar_page(self) -> bool:
        """此刻是不是日历那一页。**下拉框那两页互斥的判据都在这一句里。**"""
        return self._stack.currentWidget() is self._calendar_page

    def _current_folder(self) -> Path | None:
        """此刻所在的那个文件夹，**没有库就是 ``None``**。

        口径就是既有的那一句 ``_current_dir or library.root``（见 ``write_ops``）：
        全部照片视图下锚点是库根，相册视图下是当前那一层。

        **分组时用的也是它**：分组卡片自己不是磁盘上的东西，它代表的那批照片就摆在
        这个目录里 —— 于是「向上找到文件夹」找到的正是它，不必再往别处问（ADR 0020）。
        """
        library = self._library
        if library is None:
            return None
        return self._current_dir or library.root

    def _open_finder(self) -> None:
        """在访达里显示当前这个文件夹。

        走的是右键菜单那条 ``reveal_in_finder``（ADR 0014 决策 3）：**同一个入口，
        同一句话的措辞**，而不是另起一条 ``open -R``。
        """
        folder = self._current_folder()
        if folder is None:
            self._show_banner("还没有打开相册库，没有可以显示的文件夹。")
            return
        self._show_banner(desktop.reveal_in_finder([folder]), auto_hide=True)

    def _on_external_failed(self, message: str) -> None:
        # 走到这里的是「起进程这一步整个炸了」，而 :mod:`core.external.launch` 自己
        # 的降级路径不抛异常 —— 那一条已经回来说明了。
        self._show_banner(f"没能打开 Google 日历：{message}")

    # ---- 关闭 --------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self._save_session()
        self._teardown_library()
        # ``abandon`` 而不是「取消 + 等」：排队里还没开始的任务必须丢掉，否则每跑掉
        # 一个就再等一个（丢进来几百张照片时那是好几分钟）。已经跑起来的那几个靠令牌
        # 收工 —— 解码中途会问令牌（``core.media.decode``），所以连「云盘上还没下完、
        # open() 一直不返回」那种也能立刻放手，⌘Q 不会被拖住。
        if not self._runner.abandon():
            logger.warning("仍有后台任务未结束，已放弃等待")
        # 剪贴板那个连接**不显式断开**：PySide 会在接收者析构时自动摘掉（实测过，
        # 窗口销毁后再动剪贴板不会回调）。显式 disconnect 反而每次关窗都报一次
        # 「Failed to disconnect」—— 那个 bound method 已经不是当初连上去的那一个了。
        # 关窗到析构之间剪贴板若真的变了，回调只会动那条 QAction，窗口此时已不可见。
        super().closeEvent(event)


__all__ = ["MainWindow"]
