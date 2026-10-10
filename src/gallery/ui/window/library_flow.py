"""打开一个库、把它的内容列出来、以及把这一次运行的状态记进会话。

三种浏览方式各走一遍列举（分组视图铺开组内、平铺递归整库、相册模式只列当前层），流式分批
送回，扫完再交给排序那一条链。**切换库的那一刀**（:meth:`LibraryFlowMixin._teardown_library`）
也在这里：它要作废的东西都是上面这些流程起头的 —— 尤其是那个会往目标目录里**落文件**的
⌘V / 「移动到相册」。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, overload

from PySide6.QtCore import QObject, QRect
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import QFileDialog, QMenu, QWidget

from gallery.core.errors import GalleryError
from gallery.core.events.orphans import OrphanScan
from gallery.core.library.config import BROWSE_MODE_ALBUMS, BROWSE_MODE_CALENDAR, LibraryUIState
from gallery.core.library.facade import Library, PlacePass
from gallery.core.library.groups import SortOrder, sort_photos
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import ScanReport
from gallery.core.library.session import (
    SessionState,
    WindowGeometry,
    legacy_ui_state,
    save_session,
)
from gallery.core.metadata.facets import PhotoFacets
from gallery.core.metadata.report import MetadataReport
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui.browser.model import PhotoListModel
from gallery.ui.calendar.page import CalendarPage
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumb_sizes import edge_for_index, nearest_index
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.viewer.detail import DetailView
from gallery.ui.viewer.metadata import MetadataPanel
from gallery.ui.window.filters import FilterState
from gallery.ui.window.focus import FocusState
from gallery.ui.window.jobs import LIBRARY
from gallery.ui.window.navigation import EntryKey
from gallery.ui.window.state.app_session import AppSession
from gallery.ui.window.state.banner_text import BannerText
from gallery.ui.window.state.level_stack import LevelStack, RootKind, root_for
from gallery.ui.window.state.library_slot import LibrarySlot
from gallery.ui.window.state.pending_watch import PendingWatch
from gallery.ui.window.state.staging import Staging
from gallery.ui.window.watch import WatchBridge
from gallery.ui.window.widgets.banner_overlay import BannerOverlay
from gallery.ui.window.widgets.welcome import WelcomeView

logger = logging.getLogger(__name__)

#: 扫描时每积累多少张就送回主线程一次（流式出图，不等整库扫完）。
SCAN_BATCH_SIZE = 64


class LibraryFlowMixin:
    """guard: allow-long-comment（两族各在哪个对象、转发层为什么留着，值得说满）

    打开库 / 关闭库 / 列内容 / 记会话。窗口的入口流程都在这里。

    「现在开着哪个库」与「这一次运行」这两族的状态住在
    :class:`~gallery.ui.window.library_slot.LibrarySlot` 与
    :class:`~gallery.ui.window.app_session.AppSession` 里（ADR 0058 阶段 3），
    本 mixin 只管流程。窗口上留着一组同名 property 转发 ——
    **转发不产生第二份存储**。
    """

    _slot: LibrarySlot
    _app_session: AppSession

    def _init_opening(self, session_base: Path | None, cache_base: Path | None) -> None:
        """建这两族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._slot = LibrarySlot()
        self._app_session = AppSession(session_base=session_base, cache_base=cache_base)

    # guard: allow-long-comment（7 组转发，拆开就没法一眼看出「名字 ↔ 槽」一一对应）
    # **为什么留这一层** —— 测试直接摸 ``window._library`` / ``_session``，
    # 而属性名一个字都不改是本包的规矩。

    @property
    def _library(self) -> Library | None:
        return self._slot._library

    @_library.setter
    def _library(self, value: Library | None) -> None:
        self._slot._library = value

    @property
    def _pipeline(self) -> ThumbnailPipeline | None:
        return self._slot._pipeline

    @_pipeline.setter
    def _pipeline(self, value: ThumbnailPipeline | None) -> None:
        self._slot._pipeline = value

    @property
    def _library_title(self) -> str:
        return self._slot._library_title

    @_library_title.setter
    def _library_title(self, value: str) -> None:
        self._slot._library_title = value

    @property
    def _legacy_ui(self) -> LibraryUIState | None:
        return self._slot._legacy_ui

    @_legacy_ui.setter
    def _legacy_ui(self, value: LibraryUIState | None) -> None:
        self._slot._legacy_ui = value

    @property
    def _session(self) -> SessionState:
        return self._app_session._session

    @_session.setter
    def _session(self, value: SessionState) -> None:
        self._app_session._session = value

    @property
    def _metadata_visible(self) -> bool:
        return self._slot._metadata_visible

    @_metadata_visible.setter
    def _metadata_visible(self, value: bool) -> None:
        self._slot._metadata_visible = value

    @property
    def _calendar_level(self) -> str:
        return self._slot._calendar_level

    @_calendar_level.setter
    def _calendar_level(self, value: str) -> None:
        self._slot._calendar_level = value

    @property
    def _session_base(self) -> Path | None:
        return self._app_session._session_base

    @property
    def _cache_base(self) -> Path | None:
        return self._app_session._cache_base

    # 声明在类级别：关库与换库在这里给好几个字段置 ``None``，而声明写在方法里时 mypy 会把
    # **那一句**当成「这个 mixin 把它定义成了 ``None``」，于是同一字段在两个基类里类型不一致。

    # 声明在类级别：关库与换库在这里给好几个字段置 ``None``，而声明写在方法里时 mypy 会把
    # **那一句**当成「这个 mixin 把它定义成了 ``None``」，于是同一字段在两个基类里类型不一致。
    _group_facet: str | None
    _sort_facet: str
    _facet_values: dict[Path, PhotoFacets]

    # ---- 宿主提供的另一样东西（ADR 0057）------------------------------------
    #
    # 上面那些是**这一间自己初始化**的字段，声明在这里是因为关库/换库会给它们置 ``None``。
    # 下面这些是**窗口上别的 mixin 初始化**的：写入口在那边，这一间只读。
    _metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]]
    _place_pass_roots: set[Path]
    _preview_cache: OrderedDict[Path, QImage]
    _watch_echoes: dict[Path, float]
    # 转发过来的那几个：真存储在别处（ADR 0058），而 **property 只对它自己那间可见**,
    # 这一间单独查时它们仍然不存在，所以这里各写一份标注（与真定义同型即可）。
    _staging: Staging
    _pending_watch: PendingWatch
    _finish_job: Callable[[str], None]
    _scan_job: int | None
    _orphan_job: int | None
    _place_job: int | None
    _facet_job: int | None
    _sort_descending: bool
    _size_index: int
    _staged: list[GridEntry] | None
    _generation: int
    _level_stack: LevelStack
    _model: PhotoListModel
    _watch: WatchBridge
    _panel: MetadataPanel
    _welcome: WelcomeView
    _recent_menu: QMenu
    _calendar_page: CalendarPage
    _runner: TaskRunner
    _banner: BannerOverlay
    _metadata_action: QAction
    _detail: DetailView
    _filters: FilterState
    _focus_state: FocusState
    _banner_text: BannerText
    # guard: allow-long-comment（方法桩共三十多个，拆成几个块就看不出哪些是同一族的了）
    #
    # **方法用 ``TYPE_CHECKING`` 桩，不是类级注解**：注解只能写成 ``Callable[...]``，
    # 而那与真定义对不上 —— 组合根会报「两个基类里定义不一致」（实测）。
    # 桩必须**逐字复刻**真签名，包括关键字字与默认值。
    #
    # 桩写错的后果是**当场报红**，不是悄悄放过：把 ``_show_banner`` 的 ``text`` 写成 ``int``，
    # 组合根立刻在三个调用点报 ``arg-type``。所以这份复制品是被守着的。
    if TYPE_CHECKING:

        def _apply_thumbnail_size(self) -> None: ...
        def _arm_focus(self, focus: EntryKey | None = None) -> None: ...
        def _cancel_tier(self, group: str) -> None: ...
        def _clear_index_notice(self) -> None: ...
        def _confirm_orphan_cleanup(self, scan: OrphanScan) -> None: ...
        @property
        def _search_scope(self) -> str: ...
        @property
        def _browse_mode(self) -> str: ...
        @property
        def _current_dir(self) -> Path | None: ...
        def _current_order(self) -> SortOrder: ...
        @property
        def _drill_photos(self) -> tuple[PhotoRef, ...] | None: ...
        @property
        def _group_view(self) -> GroupRef | None: ...
        def _is_library_search(self) -> bool: ...
        def _maybe_start_photo_index(self) -> None: ...
        def _on_calendar_page(self) -> bool: ...
        def _on_thumbnail_failed(self, path: object, message: str) -> None: ...
        def _on_thumbnail_ready(self, path: object, image: object, edge: int = 0) -> None: ...
        def _order_now(self) -> None: ...
        def _place_lookup(self) -> LazyPlaceResolver: ...
        def _refresh_thumbnail_window(self) -> None: ...
        def _refresh_undo_label(self) -> None: ...
        def _report_error(self, message: str) -> None: ...
        def _set_root(self, root: RootKind) -> None: ...
        def _show_banner(
            self, text: str, *, background: bool = False, auto_hide: bool = False
        ) -> bool: ...
        def _show_calendar(self) -> None: ...
        def _start_calendar_load(self) -> None: ...
        def _start_facet_read(self) -> None: ...

        def _start_job(
            self,
            name: str,
            work: Callable[[TaskContext], Any],
            *,
            on_result: Callable[[object], None] | None = None,
            on_error: Callable[[str], None] | None = None,
            on_progress: Callable[[object], None] | None = None,
        ) -> None: ...

        def _start_search(self, library: Library) -> None: ...
        def _sync_search_scope(self) -> None: ...
        def _sync_ui(self) -> None: ...
        def _update_metadata_editable(self) -> None: ...
        def _update_mode_actions(self) -> None: ...

        # ``_reload_level`` 的真定义在 ``navigation_flow.py``（那里带完整的
        # ``EntryKey | None`` 标注）；``level_stack_mixin.py`` 里另有一份 ``object`` 标注的
        # 声明。**这里跟着真定义写**，否则组合根会报两个基类不一致。
        def _reload_level(self, *, focus: EntryKey | None = None) -> None: ...

        # 这四个是 ``QMainWindow`` 带来的（Qt 基类），**不是别的 mixin**。
        # 声明它们是为了这一间单独查时能过。签名**逐字复刻 Qt 的 .pyi**（含位置参数标记
        # ``/`` 与重载）—— 写松一点组合根就会报「与 ``QWidget`` 的定义不一致」。
        # 注意 ``geometry()`` 在 ``QWidget`` 上返回 ``QRect``、在 ``QGraphicsItem`` 上才是
        # ``QRectF``：这里要跟 ``QWidget`` 对齐。
        def geometry(self, /) -> QRect: ...
        def isMaximized(self, /) -> bool: ...  # noqa: N802 - 名字是 Qt 定的
        # 重载要多写一个实现行，否则 mypy 报 ``no-overload-impl``（运行时不执行）。
        @overload
        def setGeometry(self, arg__1: QRect, /) -> None: ...  # noqa: N802 - 名字是 Qt 定的
        @overload
        def setGeometry(  # noqa: N802 - 名字是 Qt 定的
            self, x: int, y: int, w: int, h: int, /
        ) -> None: ...
        def setGeometry(self, *args: object, **kwargs: object) -> None: ...  # noqa: N802
        def showMaximized(self, /) -> None: ...  # noqa: N802 - 名字是 Qt 定的  # noqa: N802 - 名字是 Qt 定的

    # ---- 恢复上一次的运行状态 ------------------------------------------------

    def _restore_session(self) -> None:
        """恢复窗口位置与最近打开列表。

        **库级那几个偏好不在这里**（浏览方式、排序、日历层级、缩略图档位）：它们住在那个库
        自己的 ``config.toml`` 里，而「是哪个库」要等 :meth:`open_library` 才知道。
        """
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
        # ``cast`` ：``self`` 静态上是这个 mixin，而运行时是 ``MainWindow``（一个
        # ``QMainWindow``）。ADR 0057 记了为什么这里只能 cast 而不能声明。
        chosen = QFileDialog.getExistingDirectory(cast("QWidget", self), "选择照片文件夹", start)
        if chosen:
            self.open_library(Path(chosen))

    def open_library(self, root: Path, *, silent: bool = False) -> bool:
        """打开一个库。返回是否成功。

        **不再问「缓存放哪里」**（ADR 0038 决策 2）：配置目录由 :meth:`Library.open` 自动建，
        缩略图固定在系统目录；**建不出来时只在横幅上说一声**（决策 3），只读卷上浏览照旧。

        :param silent: 启动时自动恢复上次的库时为 ``True``：开不起来也不弹框，只记日志。"""
        try:
            library = Library.open(Path(root), cache_base=self._cache_base)
        except GalleryError as exc:
            if silent:
                logger.info("自动打开上次的库失败（%s）：%s", root, exc.message)
            else:
                self._report_error(exc.message)
            return False
        self._activate_library(library)
        if not library.config_dir_ready:
            # 摆在 ``_activate_library`` **之后**：它开头就把横幅收干净了。
            self._show_banner(
                f"「{library.display_name}」这个文件夹写不进去，Gallery 没法在里面建配置目录。"
                "浏览、分组、搜索都正常，只是元数据索引这次不落盘 —— 下次打开要重读一遍。"
            )
        return True

    def _apply_ui_state(self, library: Library) -> bool:
        """把这个库记着的界面偏好套到窗口上，返回**缩略图档位有没有变**。

        **套的时机在这里而不是 ``_restore_session``**：偏好存在这个库自己的 ``config.toml``
        里，「是哪个库」要等 :meth:`open_library` 才知道。顺带做一次性迁移（ADR 0038 决策 4）：
        库里还没有 ``[ui]`` 表时，把老版本排序与浏览方式搬过来，返回值用于省一次多余的换档。
        """
        ui = self._adopt_legacy_ui(library)
        changed_edge = edge_for_index(self._size_index) != edge_for_index(
            self._edge_to_index(ui.thumbnail_edge)
        )
        self._set_root(root_for(ui.browse_mode or self._browse_mode))
        # 检索作用域（ADR 0040）：**每个库各记各的** —— 「我习惯用筛选」是关于这个库的偏好，
        # 与分组、排序那几项一样，切库不该把 A 库的用法传染给 B 库。
        self._filters.set_scope(ui.resolved_search_scope())
        self._size_index = self._edge_to_index(ui.thumbnail_edge)
        self._metadata_visible = bool(ui.metadata_visible)
        self._group_facet = ui.group_facet
        self._sort_facet = ui.resolved_sort_facet()
        self._sort_descending = bool(ui.sort_descending)
        self._calendar_level = ui.resolved_calendar_level()
        return changed_edge

    def _adopt_legacy_ui(self, library: Library) -> LibraryUIState:
        """库里还没有 ``[ui]`` 表时，从旧 ``state.json`` 里把偏好搬过来。

        **每个库只搬一次**：搬完 ``config.toml`` 里就有那张表了，于是「在 A 库迁来的排序
        偏好」不会顺着切库一路传染到 B 库。读旧会话也只这一次（``_legacy_ui`` 记着）。
        """
        if not library.ui.is_empty:
            return library.ui
        if self._legacy_ui is None:
            self._legacy_ui = legacy_ui_state(base=self._session_base) or LibraryUIState()
        if self._legacy_ui.is_empty:
            return library.ui
        library.set_ui_state(self._legacy_ui)
        return self._legacy_ui

    def _edge_to_index(self, edge: int | None) -> int:
        """配置里记的缩略图长边 → 档位序号（就近取档）。

        **不合法就回默认档**：``[ui]`` 表是软件自己写的，手改坏一个值不该让网格开天窗。
        """
        return nearest_index(edge)

    def _save_ui_state(self) -> None:
        """把窗口这几个库级偏好回写到当前库的 ``config.toml``（排序、日历层级、浏览方式、档位）。

        **没库时什么都不做**，**值没变时也不写** —— ``config.toml`` 每次改写都要一次
        ``fsync``，而切层、扫描完这些都跟偏好无关。两条在 :meth:`Library.set_ui_state`
        里还会再判一次。
        """
        library = self._library
        if library is None:
            return
        library.set_ui_state(
            library.ui.with_browse_mode(self._browse_mode)
            .with_metadata_visible(self._metadata_visible)
            .with_calendar_level(self._calendar_level)
            .with_thumbnail_edge(edge_for_index(self._size_index))
            .with_ordering(self._group_facet, self._sort_facet, self._sort_descending)
            .with_search_scope(self._search_scope)
        )

    def _activate_library(self, library: Library) -> None:
        self._teardown_library()
        self._library = library
        self._generation += 1
        self._banner_text.reset_thumbnail_failures()
        self._refresh_undo_label()
        self._focus_state.clear_current()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._facet_values = {}
        self._model.clear()

        self._pipeline = ThumbnailPipeline(library, self._runner, cast("QObject", self))
        self._pipeline.ready.connect(self._on_thumbnail_ready)
        self._pipeline.failed.connect(self._on_thumbnail_failed)

        self._banner.hide()
        # 切库时真正清空大图区，别让上一个库的全分辨率副本一直占着内存。
        self._detail.clear_image()
        self._panel.clear()
        self._update_metadata_editable()
        # **库级偏好要一次套上**（ADR 0038 决策 4）；只调用一次 —— 它带副作用（会写 config.toml）。
        changed_edge = self._apply_ui_state(library)
        # 作用域是**刚换过来的**（换库时每个库各记各的），胶囊上那句话与按钮的勾要跟着换 ——
        # 与换分组 / 排序之后要 ``_sync_order_actions`` 同一个道理。
        self._sync_search_scope()
        # 缩略图档位变了才走 ``_apply_thumbnail_size``（它是「换档」的唯一一条路，网格算完会回来
        # 把模型也换掉）。**档位没变就别叫它** —— 那会让网格带动画重排一下而尺寸一模一样。
        if changed_edge:
            self._apply_thumbnail_size()
        # 分组 / 排序那两张菜单的勾跟着换：`_apply_ui_state` 刚把这两个字段读成新库的，
        # 而开窗时那句 `_sync_order_actions` 勾的是上一个库那一项。末尾的 `_sync_ui` 一并重算。
        self._metadata_action.setChecked(self._metadata_visible)
        self._panel.setVisible(self._metadata_visible)
        # 栈：开库 = 从头开始（只留根帧）。与「换浏览方式清栈」是同一件事，都归 :meth:`_set_root`。
        self._set_root(self._level_stack.root)
        self._arm_focus()  # 打开库走的是自己那一套，不经过 _reload_level
        # 每一层都要重排（排序字段总是有的），所以扫出来的一律先暂存，等顺序定下来。
        self._staging.stage([])
        self._banner_text.reset_level()
        self._banner_text.clear_scan_issue()
        self._update_mode_actions()
        self._library_title = library.display_name
        self._calendar_page.set_level(self._calendar_level)
        # **只换页**：末尾本来就有一句 `_start_scan()`，再起一遍就是两遍扫描同时塞进模型。
        # 换库之后标题栏回到相册名（ADR 0025 决策 4）—— `_refresh_title` 在 `_sync_ui` 里。
        if self._browse_mode == BROWSE_MODE_CALENDAR:
            self._show_calendar()
        else:
            self._sync_ui()
        self._remember(library.root)
        self._watch.start(library)
        self._start_scan()
        self._start_orphan_check(library)

    # ---- 孤儿绑定（ADR 0043 决策 9）--------------------------------------------

    def _start_orphan_check(self, library: Library) -> None:
        """开库时查一遍有没有指向已不存在照片的绑定（ADR 0043 决策 9）。

        **每次开库都查**：这一遍要抓的正是「Gallery 没开着的时候」发生的改动，记「上次查过没有」的
        标记反而会漏掉它；扫描在后台线程里，用户看不见。**事件库不可用就压根不起这个任务**（那种库
        上没有事件，一次开库不该白付磁盘开销，ADR 0043 决策 1）。
        """
        if not library.events_available:
            return
        generation = self._generation

        def work(context: TaskContext) -> object:
            return library.find_orphan_bindings(should_cancel=context.is_cancelled)

        self._start_job(
            "_orphan_job",
            work,
            on_result=partial(self._on_orphan_checked, generation),
            on_error=self._on_orphan_check_failed,
        )

    def _on_orphan_checked(self, generation: int, payload: object) -> None:
        """查完了（**主线程**）。**什么都没找到就彻底静默** —— 不弹框、不挂横幅、不记日志。"""
        self._finish_job("_orphan_job")
        if generation != self._generation or self._library is None:
            return
        if not isinstance(payload, OrphanScan) or not payload.found:
            return
        self._confirm_orphan_cleanup(payload)

    def _on_orphan_check_failed(self, message: str) -> None:
        """这一遍查不下去不该挡住浏览 —— 用户没损失什么，只是这一次的孤儿留着。"""
        self._finish_job("_orphan_job")
        logger.info("查孤儿绑定失败：%s", message)

    def _teardown_library(self) -> None:
        # 监听器第一刀：它还在跑的时候，下面就把库和索引都拆了。
        self._watch.stop()
        self._watch_echoes.clear()
        self._pending_watch.forget_all()
        # 「到了这一层该选中哪一格」与「现在摆的是哪一层」都是上一个库留下的，
        # 不作废会把上一库的选中搬过来。
        self._focus_state.forget_level()
        # guard: allow-long-comment（约束 1：为什么写操作必须在这里取消，换层时又为什么不能）
        # 切库与关窗取消**全部**任务，包括 :attr:`_write_job` —— ⌘V /「移动到相册」在旧库的
        # ``Library`` 上跑，不取消的话收尾时会去问新库「下一次 ⌘Z 撤什么」，而记录在旧库里。
        # **换层那两组不含它**（见 :mod:`gallery.ui.window.jobs`），否则文件会搬到一半。
        self._cancel_tier(LIBRARY)
        self._clear_index_notice()
        if self._pipeline is not None:
            self._pipeline.shutdown()
            self._pipeline.deleteLater()
            self._pipeline = None
        if self._library is not None:
            self._library.close_index()
        self._library = None
        # 栈：关库 = 回到只有根帧（否则上一座库的相册 / 分组 / 日历格还挂着，而它们引用的
        # 都是已经关掉的索引）。与「开库从头开始」共用 :meth:`_set_root` 这一句。
        self._set_root(self._level_stack.root)
        self._facet_values = {}
        self._update_metadata_editable()
        # 条件是**临时的**（ADR 0026 决策 9）：换库之后还留着上一座库的胶囊，
        # 检索结果就属于一个已关掉的文件夹。
        self._filters.clear()

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        """按当前浏览方式列出内容：平铺模式递归整库，相册模式只列当前一层。

        **只有整库检索才接管这一层**（ADR 0040 决策 1）：筛选照常枚举这一层、随后收窄
        （收窄在那条链的末尾，见 :meth:`OrderingMixin._order_now`），所以这里问的是
        :meth:`_is_library_search` 而不是「有没有条件」—— 问错了，月视图里加一条条件就会
        被换成网格页。已经进入某个分组时不再扫盘，直接铺开。"""
        library = self._library
        if library is None:
            return
        # 搜索优先于分组：分组那一族是「这一层里怎么归拢」，而检索结果是**一层**。
        if self._is_library_search():
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

        self._start_job(
            "_scan_job",
            work,
            on_result=partial(self._on_group_listed, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _on_group_listed(self, generation: int, payload: object) -> None:
        """分组视图的照片进网格，然后按「扫描完成」那一套收尾。"""
        if generation != self._generation:
            return
        entries = payload if isinstance(payload, tuple) else ()
        if not self._staging.is_staging:
            self._model.append(entries)
        else:
            self._staging.extend(entries)
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

        self._start_job(
            "_scan_job",
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

        self._start_job(
            "_scan_job",
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _feed_calendar(self) -> None:
        """扫完之后把整库交给日历分桶。

        **只在日历那一页上做**：同样的整库在「全部照片」下已经在跑第二遍读元数据，日历再起
        一次就是白读一次 EXIF。
        """
        if self._browse_mode != BROWSE_MODE_CALENDAR or self._library is None:
            return
        # **日历那一页不在前面时不喂**：模型每次重排都会走到这里，而用户可能在日历点出来的那一层。
        if not self._on_calendar_page():
            return
        # **展开的那一层更不能喂**：那一层是日历上一格里的几张，拿它分桶等于告诉日历「整个库就
        # 这几张」。换页发生在摆内容**之后**（``_list_drill_photos``），上面那条拦不住。
        if self._drill_photos is not None:
            return
        if self._scan_job is not None:  # 还没扫完，等 _on_scan_done
            return
        self._start_calendar_load()

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        if self._staging.is_staging:
            # 扫描期间一律先暂存：**边扫边出图在这里等于闪一下** —— 先看到一屏按扫描顺序排的
            # 照片，下一秒全变样。
            self._staging.extend(batch)
            return
        self._model.append(batch)
        self._refresh_thumbnail_window()

    def _on_scan_done(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._finish_job("_scan_job")
        report = payload if isinstance(payload, ScanReport) else ScanReport()
        if report.has_issues:
            self._banner_text.note_scan_issue()
            self._show_banner(f"部分内容没能读取：{report.summary}")
        # 「这一层空不空」**不在这里判断**：要重排时条目还暂存着没显示到界面（见 _staged），
        # 照模型判断会把相册 / 组卡片误报成「空」。
        if self._pending_watch.has_pending():
            # 扫描进行中时收到过外部变化：流式扫描没法在半路上改，干脆重来这一层。
            self._pending_watch.take()
            self._reload_level()
            return
        self._refresh_thumbnail_window()
        self._start_facet_read()  # 里面会走到 `_apply_ordering` → 喂日历
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _on_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._finish_job("_scan_job")
        self._banner_text.note_scan_issue()
        self._show_banner(f"扫描失败：{message}")
        # 已经累积到的条目照常摆出来：扫到一半失败时给一屏能看的东西，比一屏空白有用。
        self._order_now()

    # ---- 打开库时把地点解析一遍（ADR 0010）------------------------------------

    def _maybe_start_place_pass(self) -> None:
        """第一次打开一个库时，在后台把整库的地点算出来存进索引。

        **为什么是打开时而不是等用户去分组**：分组与元数据面板要的是同一份反查结论，两处各算一次
        就必然漂（面板写着杭州、分组写着 Bordeaux，ADR 0010）。一个库里只跑一次
        （``_place_pass_roots`` 记着起过哪些库）；扫描出问题、或本层正在读元数据时先让着。
        """
        library = self._library
        if (
            library is None
            or library.root in self._place_pass_roots
            or not self._banner_text.may_show_progress
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

        # **横幅不在这儿亮**：这一遍多半是空跑，先亮等于在说一件没有发生的事。
        self._start_job(
            "_place_job",
            work,
            on_progress=self._on_place_pass_progress,
            on_result=self._on_place_pass_done,
            on_error=self._on_place_pass_failed,
        )

    def _on_place_pass_progress(self, payload: object) -> None:
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "work":
            self._on_place_pass_work(payload[1])
            return
        if not self._banner_text.place.is_ours or not self._banner_text.may_show_progress:
            return
        if isinstance(payload, str):
            self._show_place_notice(payload)  # 「正在加载城市库…」这类说明
            return
        if isinstance(payload, int) and payload > 0:
            self._show_place_notice(f"正在解析照片的地点…已处理 {payload} 张")

    def _on_place_pass_work(self, count: object) -> None:
        """真的找到活干了才亮横幅：空跑的库不该让用户看见「正在解析…」。"""
        if not self._banner_text.may_show_progress or not isinstance(count, int):
            return
        self._show_place_notice(f"正在解析 {count} 张照片的地点…")

    def _on_place_pass_done(self, payload: object) -> None:
        self._finish_job("_place_job")
        self._clear_place_notice()
        result = payload if isinstance(payload, PlacePass) else PlacePass()
        logger.info("地点解析完成：扫过 %d 张，反查 %d 张", result.scanned, result.resolved)
        self._maybe_start_photo_index()

    def _on_place_pass_failed(self, message: str) -> None:
        self._finish_job("_place_job")
        self._clear_place_notice()
        # 解析失败不影响浏览：分组与面板都会在自己要用的时候再试一次。
        logger.info("解析地点失败：%s", message)
        self._maybe_start_photo_index()

    def _show_place_notice(self, text: str) -> None:
        """进度消息**不许抢**横幅：上面正有别人的话就等它说完。

        抢了会怎样：用户刚丢掉三张照片，横幅上「已把 3 项移到废纸篓」被后台进度顶掉，那条确认就此
        消失。两条判据：上面已经有别人的话（比文字），或刚刚有用户动作（比时间，见
        :data:`BANNER_STICKY_SECONDS`）；前者由
        :class:`~gallery.ui.window.banner_text.NoticeSlot` 记账。
        """
        self._banner_text.place.show(text)

    def _clear_place_notice(self) -> None:
        """收掉自己那一条 —— **而且只在自己还占着的时候收**（理由见 ``NoticeSlot.clear``）。"""
        self._banner_text.place.clear()

    def _forget_place_pass_root(self) -> None:
        """把当前库从「地点解析起过」那份名单里拿掉。

        **只有取消才调**：半途取消等于那一遍**没做完**，而根还留在集合里它就再也起不来 ——
        面板不出地名、按地点分组也空，而这两处都不报错。
        """
        if self._library is not None:
            self._place_pass_roots.discard(self._library.root)

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
            empty = QAction("（还没有）", cast("QWidget", self))
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for root in roots:
            action = QAction(str(root), cast("QWidget", self))
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
