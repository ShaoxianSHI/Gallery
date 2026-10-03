"""网格测试共用的造法。

**只有一份** —— :func:`make_grid` 把「有模型、有条目、已排过版」的网格造出来，
好几处测试都要它。各写一份的问题与等待助手一样：改了一处不会改到其余几处，
于是「同一件事有两种写法」。

放在这里而不是 ``tests/ui/conftest.py``：它是**普通函数**而不是 fixture（调用方要
自己给 ``qt_app`` / ``tmp_path``），而且只服务 ``tests/ui/browser/`` 这一层。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from gallery.core.library.photos import PhotoRef
from gallery.ui.browser.grid import PhotoGrid
from gallery.ui.browser.model import PhotoListModel


def fake_photos(count: int, tmp_path: Path) -> list[PhotoRef]:
    return [
        PhotoRef(path=tmp_path / f"IMG_{index:04d}.jpg", size=1024, mtime_ns=index)
        for index in range(count)
    ]


def make_grid(qt_app: QApplication, tmp_path: Path, count: int, *, width: int = 1200) -> PhotoGrid:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append(fake_photos(count, tmp_path))
    grid.setModel(model)
    grid.resize(width, 800)
    grid.show()
    qt_app.processEvents()
    return grid


__all__ = ["fake_photos", "make_grid"]
