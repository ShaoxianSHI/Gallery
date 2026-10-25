"""应用装配与启动。

创建 ``QApplication``、构造主窗口、进入事件循环；**窗口的生老病死也归这一层**
（:class:`WindowHost`）：「⌘W 关窗」与「⌘Q 退出」是应用级的事；从外面指过来一个文件夹
（Dock 右键菜单里那份最近项目）也落在这儿 —— 它可能**在窗口还没有的时候到达**。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QEvent, QLibraryInfo, QLocale, QObject, Qt, QTimer, QTranslator
from PySide6.QtGui import QFileOpenEvent, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from gallery.core.constants import APP_NAME
from gallery.core.i18n import current_language, resolve_language, set_language, t
from gallery.core.library.session import load_session
from gallery.core.paths import app_icon_files, augment_external_tool_path

if TYPE_CHECKING:
    from gallery.ui.main_window import MainWindow


def disable_menu_action_images() -> None:
    """关掉 macOS 26 自动加到菜单项前的系统图标。

    用 ``registerDefaults`` 而不是写全局 defaults：只影响本进程，也不落盘。
    **必须在 QApplication / 菜单创建之前调用**，否则这一轮已经画上了。
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSUserDefaults
    except ImportError:  # pragma: no cover - macOS 上 pyobjc 是依赖
        return
    NSUserDefaults.standardUserDefaults().registerDefaults_({"NSMenuEnableActionImages": False})


def apply_app_branding(app: QApplication) -> None:
    """给应用安上名字与图标（``⌘Tab``、访达里那一枚属于 ``.app`` 包）。资源缺失时静默跳过 ——
    图标不该让应用起不来。"""
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    icon = QIcon()
    for path in app_icon_files():
        if path.is_file():
            icon.addFile(str(path))
    if not icon.isNull():
        app.setWindowIcon(icon)


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """返回（必要时创建）全局唯一的 ``QApplication``。"""
    existing = QApplication.instance()
    if existing is not None:
        return cast(QApplication, existing)
    app = QApplication(list(argv) if argv is not None else list(sys.argv))
    apply_app_branding(app)
    return app


def activate_language(session_base: Path | None = None) -> str:
    """在导入界面模块之前定语言（ADR 0106 决策 6）。

    有些文案常量在模块导入时求值；语言必须在第一次 import 界面之前就位。
    """
    preferences = load_session(base=session_base).preferences
    return set_language(resolve_language(preferences.language, QLocale.system().name()))


def install_qt_translations(app: QApplication) -> None:
    """让 Qt 自己的标准按钮跟着应用语言走（ADR 0106 决策 6）。

    这一套与 ``core/locales`` 是两份来源：``QMessageBox`` / ``QFileDialog`` 的按钮由 Qt 提供。
    找不到对应的 ``.qm`` 就静默跳过 —— 界面照常起来。
    """
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translator = QTranslator(app)
    if translator.load(f"qtbase_{current_language()}", path):
        app.installTranslator(translator)


def _drop_autofill_and_below(menu: object) -> None:
    """删掉 macOS 往「编辑」菜单末尾塞的 AutoFill 一整段（含它下面的系统项）。

    系统项不是我们的 ``QAction``：标题 ``AutoFill``、action ``submenuAction:``。**从它开始往下
    全删**，再把末尾多出来的分隔线收掉。标题被本地化时，退回「撤销之后第一个系统子菜单」。
    """
    items = list(menu.itemArray())  # type: ignore[attr-defined]
    start = next((index for index, item in enumerate(items) if item.title() == "AutoFill"), None)
    if start is None:
        # AutoFill 被删掉之后系统仍可能继续往下加子项：撤销是本菜单的最后一条，后面全是系统项。
        undo = t("menu.undo")
        undo_index = next(
            (index for index, item in enumerate(items) if item.title().startswith(undo)), None
        )
        if undo_index is None:
            return
        start = undo_index + 1
    for item in items[start:]:
        menu.removeItem_(item)  # type: ignore[attr-defined]
    while menu.numberOfItems() > 0:  # type: ignore[attr-defined]
        last = menu.itemArray()[-1]  # type: ignore[attr-defined]
        if not last.isSeparatorItem():
            break
        menu.removeItem_(last)  # type: ignore[attr-defined]


def _drop_full_screen(menu: object) -> None:
    """删掉 macOS 往「显示」菜单里塞的 Enter Full Screen。

    它带系统图标，和有勾选项的那一组并排时会把整组推到图标右侧；本应用的「铺满窗口」已经
    负责同一件事。按 action 认，标题被本地化也删得掉。**系统只在菜单标题是 ``View`` 时塞它**
    （中文界面的「显示」不会出现），但删的时候两种界面都问一遍。
    """
    removed = False
    for item in list(menu.itemArray()):  # type: ignore[attr-defined]
        if str(item.action()) == "toggleFullScreen:" or item.title() in {
            "Enter Full Screen",
            "Exit Full Screen",
        }:
            menu.removeItem_(item)  # type: ignore[attr-defined]
            removed = True
    if not removed:
        return
    while menu.numberOfItems() > 0:  # type: ignore[attr-defined]
        last = menu.itemArray()[-1]  # type: ignore[attr-defined]
        if not last.isSeparatorItem():
            break
        menu.removeItem_(last)  # type: ignore[attr-defined]


def _hide_autofill_and_below(menu: object) -> None:
    items = list(menu.itemArray())  # type: ignore[attr-defined]
    start = next((index for index, item in enumerate(items) if item.title() == "AutoFill"), None)
    if start is None:
        undo = t("menu.undo")
        undo_index = next(
            (index for index, item in enumerate(items) if item.title().startswith(undo)), None
        )
        if undo_index is None:
            return
        start = undo_index + 1
    for item in items[start:]:
        item.setHidden_(True)


def _hide_full_screen(menu: object) -> None:
    for item in list(menu.itemArray()):  # type: ignore[attr-defined]
        if str(item.action()) == "toggleFullScreen:" or item.title() in {
            "Enter Full Screen",
            "Exit Full Screen",
        }:
            item.setHidden_(True)


def _after_system_insert(menu: object, drop: object, hide: object) -> None:
    """**不能在增加通知里直接 removeItem**：AppKit 正在同一轮插入，删了会抛
    ``NSInternalInconsistencyException``。先隐藏（只改属性），下一轮主队列里再删。"""
    from Foundation import NSOperationQueue

    hide(menu)  # type: ignore[operator]
    NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: drop(menu))  # type: ignore[operator]


def _observe_additions(menu: object, drop: object, hide: object) -> object:
    """系统往这个菜单里加东西时立刻删一遍；返回的 token 必须由调用方保活。"""
    from Foundation import NSNotificationCenter

    def _on_added(
        _note: object, *, target: object = menu, fn: object = drop, hide: object = hide
    ) -> None:
        _after_system_insert(target, fn, hide)

    return NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
        "NSMenuDidAddItemNotification", menu, None, _on_added
    )


def _install_system_menu_cleanup(window: QMainWindow) -> None:
    """去掉 macOS 自动塞进菜单栏的两段：编辑里的 AutoFill、显示里的 Full Screen。

    **不能替换 NSMenu 的 delegate**：Qt 自己的 ``QCocoaNSMenuDelegate`` 就在那里，换掉它菜单
    动作会失效。系统项是在菜单展开时加的，所以除了启动时删一次，还要盯着
    ``NSMenuDidAddItemNotification`` —— 它一到就删。
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication
    except ImportError:  # pragma: no cover - macOS 上 pyobjc 是依赖
        return

    main_menu = NSApplication.sharedApplication().mainMenu()
    if main_menu is None:
        return
    observers: list[object] = list(getattr(window, "_system_menu_observers", []))
    installed: set[str] = set(getattr(window, "_system_menu_installed", set()))
    for title, drop in (
        (t("menu.edit"), _drop_autofill_and_below),
        (t("menu.view"), _drop_full_screen),
    ):
        menu = next(
            (
                action.menu()
                for action in window.menuBar().actions()
                if action.menu() is not None and action.text() == title
            ),
            None,
        )
        item = main_menu.itemWithTitle_(title)
        if menu is None or item is None:
            continue
        native = item.submenu()
        if native is None:
            continue
        if title in installed:
            continue
        drop(native)
        menu.aboutToShow.connect(lambda target=native, fn=drop: fn(target))
        observers.append(
            _observe_additions(
                native,
                drop,
                _hide_autofill_and_below if drop is _drop_autofill_and_below else _hide_full_screen,
            )
        )
        installed.add(title)
    window._system_menu_observers = observers  # type: ignore[attr-defined]
    window._system_menu_installed = installed  # type: ignore[attr-defined]


def schedule_system_menu_cleanup(window: QMainWindow) -> None:
    """菜单栏要等这一轮事件走完才在原生侧建好，所以晚一点装；没装全再试几次。"""
    attempts = 10

    def attempt() -> None:
        nonlocal attempts
        try:
            _install_system_menu_cleanup(window)
        except RuntimeError:  # 窗口已经关掉（Python 包装还在、C++ 侧没了）
            return
        done = getattr(window, "_system_menu_installed", set())
        attempts -= 1
        if len(done) >= 2 or attempts <= 0:
            return
        QTimer.singleShot(100, attempt)

    QTimer.singleShot(0, attempt)


def keep_running_without_windows(app: QApplication) -> None:
    """关掉最后一个窗口**不退出应用** —— ``⌘W`` 与 ``⌘Q`` 的分工（ADR 0037）。

    Qt 默认 ``quitOnLastWindowClosed`` 是开的（最后一个窗口一关事件循环就退），而 macOS 上
    关窗与退出本来就是两件事。关掉这一条之后**退出就得自己说一声**（:meth:`MainWindow.quit_app`
    里的 ``app.quit()``）：只关不退出，⌘Q 按了没反应；只退不关，⌘W 会顺手把应用带走。
    """
    app.setQuitOnLastWindowClosed(False)


class WindowHost(QObject):
    """窗口没了之后应用还活着：叫得回来。

    ``⌘W`` 关掉的窗口是**真关掉了**（``closeEvent`` 已停掉监听、收掉后台任务），从 Dock 点
    回来时开的是**新一个窗口**，会话里记着上次打开的库。**它必须活到事件循环结束**：它是
    ``applicationStateChanged`` 的接收者，一被回收，从 Dock 点回来就没有窗口了。
    """

    def __init__(
        self,
        app: QApplication,
        *,
        session_base: Path | None = None,
        cache_base: Path | None = None,
        library: Path | None = None,
    ) -> None:
        super().__init__(app)
        self._session_base = session_base
        self._cache_base = cache_base
        self._library = library
        self._window: MainWindow | None = None
        app.applicationStateChanged.connect(self._on_application_state_changed)
        # 从应用外面指过来一个文件夹时，Qt 把 Apple Event 转成 ``QFileOpenEvent`` 发给
        # ``QApplication``；窗口可能一个都还没有，所以接在这层（ADR 0062）。
        app.installEventFilter(self)

    @property
    def window(self) -> MainWindow | None:
        """此刻手上那一个窗口（``None`` = 一个都还没有）。"""
        return self._window

    def open_window(self, library: Path | None = None) -> MainWindow:
        """开出一个新窗口并摆出来，返回它。

        :param library: 这个窗口先打开哪个库；省略则用启动时指定的那个。
        """
        # 延迟导入：语言要在 ``MainWindow`` 那一串模块导入之前定下来（见 activate_language）。
        from gallery.ui.main_window import MainWindow

        window = MainWindow(
            session_base=self._session_base,
            cache_base=self._cache_base,
            initial_library=self._library if library is None else library,
        )
        window.show()
        self._window = window
        schedule_system_menu_cleanup(window)
        return window

    def open_library(self, root: Path) -> MainWindow:
        """把 ``root`` 摆到用户面前：窗口在就换库，不在就开一个带上它。

        这条路只有「应用外面的人点了这个库」会走（Dock 右键菜单、访达的「打开方式」）。
        """
        window = self._window
        if window is not None and window.isVisible():
            window.open_library(root)
            return window
        self._drop_closed_window()
        return self.open_window(root)

    def reopen_if_needed(self) -> None:
        """应用回到前台而屏幕上一个窗口都没有时，重新开一个（**还在就不动**，否则用户面前会
        凭空多出一扇窗）。"""
        window = self._window
        if window is not None and window.isVisible():
            return
        self._drop_closed_window()
        self.open_window()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        """接住「从外面打开一个文件夹」（见 :meth:`open_library`）。"""
        if event.type() == QEvent.Type.FileOpen and isinstance(event, QFileOpenEvent):
            root = Path(event.url().toLocalFile() or event.file())
            if root.is_dir():
                self.open_library(root)
                return True
        return super().eventFilter(watched, event)

    def _drop_closed_window(self) -> None:
        """把关掉的那个窗口排进删除队列（直接删会拆掉正在派发的那一轮事件里的控件）。"""
        window = self._window
        if window is None:
            return
        self._window = None
        window.deleteLater()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.reopen_if_needed()


def run(
    argv: Sequence[str] | None = None,
    *,
    library: Path | None = None,
    session_base: Path | None = None,
    cache_base: Path | None = None,
) -> int:
    """启动界面，返回进程退出码。"""
    # 系统菜单图标要在造菜单之前关掉；它只影响本进程，不改用户的全局 defaults。
    disable_menu_action_images()
    # 在建 QApplication 之前补 PATH：从访达 / Dock 启动时 exiftool 不在系统目录里，
    # 不补的话主窗口一开就去读元数据，会一路降级成只读模式。
    activate_language(session_base)
    augment_external_tool_path()
    app = create_application(argv)
    install_qt_translations(app)
    # ⌘W 关窗与 ⌘Q 退出的分工（ADR 0037）。**必须在造窗口之前**。
    keep_running_without_windows(app)
    host = WindowHost(app, session_base=session_base, cache_base=cache_base, library=library)
    host.open_window()
    # ``host`` 是 ``applicationStateChanged`` 的接收者：它作为局部变量活到 ``exec()`` 返回。
    return app.exec()


__all__ = [
    "WindowHost",
    "activate_language",
    "apply_app_branding",
    "create_application",
    "disable_menu_action_images",
    "install_qt_translations",
    "keep_running_without_windows",
    "run",
    "schedule_system_menu_cleanup",
]
