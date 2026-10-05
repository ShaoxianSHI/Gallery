"""应用装配与启动。

创建 ``QApplication``、构造主窗口、进入事件循环；**窗口的生老病死也归这一层**
（:class:`WindowHost`）：「⌘W 关窗」与「⌘Q 退出」是应用级的事。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gallery.core.constants import APP_NAME
from gallery.core.paths import app_icon_files, augment_external_tool_path
from gallery.ui.main_window import MainWindow


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

    @property
    def window(self) -> MainWindow | None:
        """此刻手上那一个窗口（``None`` = 一个都还没有）。"""
        return self._window

    def open_window(self) -> MainWindow:
        """开出一个新窗口并摆出来，返回它。"""
        window = MainWindow(
            session_base=self._session_base,
            cache_base=self._cache_base,
            initial_library=self._library,
        )
        window.show()
        self._window = window
        return window

    def reopen_if_needed(self) -> None:
        """应用回到前台而屏幕上一个窗口都没有时，重新开一个（**还在就不动**，否则用户面前会
        凭空多出一扇窗）。"""
        window = self._window
        if window is not None and window.isVisible():
            return
        if window is not None:
            # 关掉的那一个已经收干净了，留着只是个永远不会再显示的顶层控件。
            # **排队删而不是直接删**：直接删会拆掉正在派发的那一轮事件里的控件。
            window.deleteLater()
            self._window = None
        self.open_window()

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
    # 在建 QApplication 之前补 PATH：从访达 / Dock 启动时 exiftool 不在系统目录里，
    # 不补的话主窗口一开就去读元数据，会一路降级成只读模式。
    augment_external_tool_path()
    app = create_application(argv)
    # ⌘W 关窗与 ⌘Q 退出的分工（ADR 0037）。**必须在造窗口之前**。
    keep_running_without_windows(app)
    host = WindowHost(app, session_base=session_base, cache_base=cache_base, library=library)
    host.open_window()
    # ``host`` 是 ``applicationStateChanged`` 的接收者：它作为局部变量活到 ``exec()`` 返回。
    return app.exec()


__all__ = [
    "WindowHost",
    "apply_app_branding",
    "create_application",
    "keep_running_without_windows",
    "run",
]
