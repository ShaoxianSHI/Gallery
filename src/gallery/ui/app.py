"""应用装配与启动。

这里刻意保持很薄：创建 ``QApplication``，构造主窗口，进入事件循环。
所有业务逻辑都在 ``core`` 与主窗口里，方便在没有图形环境时绕开这一层做测试。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtWidgets import QApplication

from gallery.ui.main_window import MainWindow


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """返回（必要时创建）全局唯一的 ``QApplication``。"""
    existing = QApplication.instance()
    if existing is not None:
        return cast(QApplication, existing)
    app = QApplication(list(argv) if argv is not None else list(sys.argv))
    app.setApplicationName("Gallery")
    app.setApplicationDisplayName("Gallery")
    app.setOrganizationName("Gallery")
    return app


def run(
    argv: Sequence[str] | None = None,
    *,
    library: Path | None = None,
    session_base: Path | None = None,
) -> int:
    """启动界面，返回进程退出码。"""
    app = create_application(argv)
    window = MainWindow(session_base=session_base, initial_library=library)
    window.show()
    return app.exec()


__all__ = ["create_application", "run"]
