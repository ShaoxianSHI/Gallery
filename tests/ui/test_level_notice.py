"""「这一层空不空」的横幅。

判据必须是**网格上有没有条目**，而不是「有没有相册 / 照片」：分组会把照片换成组卡片，
要重排时条目还会先暂存不显示。这两条都让「数相册和照片」在那一瞬间数出 0，于是会把一屏
相册 / 组卡片误报成「这个文件夹里既没有子文件夹，也没有可显示的照片」。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from waiting import wait_for_idle

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.scanner import ScanReport  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待后台扫描 / 元数据读取的时限。


def _open(
    qt_app, root: Path, *, session_base: Path, browse_mode: str, group_facet: str | None
) -> MainWindow:
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode, group_facet=group_facet)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    _settle(qt_app, window)
    return window


def _settle(qt_app, window: MainWindow) -> None:
    """等扫描、分组、地点那一遍结束。

    判据与轮询循环都在 :mod:`waiting` 里（五个模块共用同一份，见那里的
    :func:`waiting.wait_for_idle`）—— 不成立**直接抛**，而不是超时也返回。
    """
    wait_for_idle(qt_app, window, what="扫描、分组与地点解析")


def _banner(window: MainWindow) -> str:
    return window._banner.text() if window._banner.isVisible() else ""


def _row_of(window: MainWindow, name: str) -> int:
    return next(index for index, entry in enumerate(window._model.entries()) if entry.name == name)


def test_albums_are_not_mistaken_for_an_empty_folder(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """回归测试：根目录摆着两个相册，却报「既没有子文件夹，也没有可显示的照片」。

    **开着分组**时最容易命中：要重排的层级在判断那一刻还没显示，模型里一条也没有。
    """
    root = tmp_path / "照片"
    for album in ("西安", "巴黎"):
        folder = root / album
        folder.mkdir(parents=True)
        make_photo("p.jpg", directory=folder)
    prepare_library(root)

    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )

    assert [entry.name for entry in window._model.entries()] == ["巴黎", "西安"]
    assert _banner(window) == ""
    window.close()


def test_group_cards_are_not_mistaken_for_an_empty_folder(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """整层都是组卡片时同样不算空（分组模式下照片全被换成了卡片）。"""
    from gallery.core.metadata import batch

    def fake(self, paths, *, should_cancel=None):  # noqa: ANN001, ANN202
        for path in paths:
            yield path, {"EXIF:DateTimeOriginal": "2024:05:16 10:00:00"}

    from PySide6.QtWidgets import QApplication

    del qt_app
    app = QApplication.instance()
    original = batch.ExifToolBatchSource.read_many
    batch.ExifToolBatchSource.read_many = fake
    try:
        root = tmp_path / "照片"
        root.mkdir()
        make_photo("a.jpg", directory=root)
        prepare_library(root)
        window = _open(
            app,  # type: ignore[arg-type]
            root,
            session_base=tmp_path / "状态",
            browse_mode="flat",
            group_facet="captured_month",
        )

        assert window._model.group_count() == 1
        assert _banner(window) == ""
        window.close()
    finally:
        batch.ExifToolBatchSource.read_many = original


def test_a_truly_empty_folder_still_says_so(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """修的只是误报：真空的文件夹仍然要如实说。"""
    root = tmp_path / "照片"
    (root / "真空的").mkdir(parents=True)
    (root / "西安").mkdir()
    make_photo("p.jpg", directory=root / "西安")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet=None,
    )

    window._on_entry_activated(window._model.index(_row_of(window, "真空的"), 0))
    _settle(qt_app, window)

    assert _banner(window) == "这个文件夹里既没有子文件夹，也没有可显示的照片。"
    window.close()


def test_the_notice_is_retracted_when_content_arrives(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """进到一个空文件夹再退回来，那句提示必须跟着收回去。"""
    root = tmp_path / "照片"
    (root / "真空的").mkdir(parents=True)
    (root / "西安").mkdir()
    make_photo("p.jpg", directory=root / "西安")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )

    window._on_entry_activated(window._model.index(_row_of(window, "真空的"), 0))
    _settle(qt_app, window)
    assert _banner(window) != ""

    window.go_up()
    _settle(qt_app, window)

    assert window._model.rowCount() > 0
    assert _banner(window) == ""
    window.close()


def test_a_scan_problem_is_not_replaced_by_the_empty_notice(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """扫描出问题时，那句比「这里是空的」更该说，不许被它盖掉。"""
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("a.jpg", directory=root)
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet=None,
    )
    broken = ScanReport(unreadable_files=["/照片/坏文件.jpg（权限不足）"])
    window._on_scan_done(window._generation, broken)

    assert "部分内容没能读取" in _banner(window)
    window.close()


def test_scan_problem_wins_over_the_empty_notice(
    qt_app, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """一层确实什么都没有、扫描又出了问题：说的是问题，不是「这里是空的」。"""
    root = tmp_path / "照片"
    (root / "空的").mkdir(parents=True)
    (root / "西安").mkdir()
    make_photo("p.jpg", directory=root / "西安")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet=None,
    )
    window._on_entry_activated(window._model.index(_row_of(window, "空的"), 0))
    _settle(qt_app, window)
    assert window._model.rowCount() == 0

    broken = ScanReport(skipped_dirs=["/照片/没权限（权限不足）"])
    window._on_scan_done(window._generation, broken)

    assert "部分内容没能读取" in _banner(window)
    assert "既没有子文件夹" not in _banner(window)
    window.close()
