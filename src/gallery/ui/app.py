"""应用装配与启动。

这里刻意保持很薄：创建 ``QApplication``，构造主窗口，进入事件循环。
所有业务逻辑都在 ``core`` 与主窗口里，方便在没有图形环境时绕开这一层做测试。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gallery.core.constants import APP_NAME
from gallery.core.paths import app_icon_files, augment_external_tool_path
from gallery.ui.main_window import MainWindow


def apply_app_branding(app: QApplication) -> None:
    """给应用安上名字与图标。

    图标是窗口标题栏左侧那一枚（``⌘Tab``、访达里的那一枚属于 ``.app`` 包，
    见 ``gallery.macos_app``）。资源缺失时静默跳过：图标不该让应用起不来。
    """
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


def run(
    argv: Sequence[str] | None = None,
    *,
    library: Path | None = None,
    session_base: Path | None = None,
) -> int:
    """启动界面，返回进程退出码。"""
    # 在建 QApplication 之前补 PATH：从访达 / Dock 启动时 exiftool 不在系统目录里，
    # 不补的话主窗口一开就去读元数据，只会一路降级成只读模式。
    augment_external_tool_path()
    app = create_application(argv)
    window = MainWindow(session_base=session_base, initial_library=library)
    window.show()
    return app.exec()


__all__ = ["apply_app_branding", "create_application", "run"]
