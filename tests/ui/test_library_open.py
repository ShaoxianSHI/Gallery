"""打开一个库时的三件事：不再弹窗、建不出来就降级、老偏好迁过来。

这是 ADR 0038 换掉的那一段行为，值得单独一个文件盯着：它同时改掉了「开场问缓存放
哪里」、把缩略图挪出照片目录、并且把库级界面偏好从 ``state.json`` 搬进了库里。
三件事里任何一件退回去，用户看到的都是同一种症状 ——「它又在问我 / 又在往我照片
文件夹里写小文件了」。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from waiting import wait_for_idle

from gallery.core import paths  # noqa: E402
from gallery.core.constants import CONFIG_DIR_NAME  # noqa: E402
from gallery.core.library.config import BROWSE_MODE_CALENDAR, load_config  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    SESSION_VERSION,
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> MainWindow:
    """一个还没有打开任何库的窗口。"""
    instance = MainWindow(session_base=tmp_path / "会话")
    instance.resize(900, 600)
    instance.show()
    yield instance  # type: ignore[misc]
    instance.close()


def test_opening_a_fresh_folder_asks_nothing(
    qt_app: QApplication, window: MainWindow, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**开场那个「缓存放哪里」的对话框没有了**（ADR 0038 决策 2）。

    用户在那条路上其实没有第三个有意义的选择：缩略图固定去系统目录，配置目录
    要么建得出来（照建），要么建不出来（退成内存索引）。弹窗只会让他在一个没有
    退路的对话框前点头。

    钉的是「一个模态框都没起」——直接让 ``exec`` 炸掉，比事后数调用次数更能说明
    「用户根本不会被拦在这儿」。
    """
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("一张.jpg", directory=root)

    def _refuse(*args: object, **kwargs: object) -> int:
        raise AssertionError("打开库的路上不该弹任何模态框")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(QMessageBox, "exec", _refuse)
        patch.setattr(QMessageBox, "question", _refuse)
        assert window.open_library(root) is True

    assert window._library is not None
    assert window._library.config_dir_ready is True


def test_opening_a_fresh_folder_creates_the_config_dir_and_no_thumbnails_in_it(
    qt_app: QApplication, window: MainWindow, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """配置目录自动建，而**缩略图不在里面**（ADR 0038 决策 1、决策 2）。

    这一条把用户最初看到的那份重复钉死：照片文件夹里只该多出一个配置目录，
    缩略图一律落在系统缓存里。
    """
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("一张.jpg", directory=root)

    assert window.open_library(root) is True
    wait_for_idle(qt_app, window, what="扫描与地点解析")

    assert paths.has_config_dir(root) is True
    # 照片文件夹里只有那个配置目录是 Gallery 建的，**没有 cache/ 那棵树**。
    assert not (root / CONFIG_DIR_NAME / "cache").exists()
    thumbs = window._library.thumbnails.directory
    assert root not in thumbs.parents, "缩略图不该落在用户的照片文件夹里"


def test_a_folder_that_cannot_be_written_still_opens(
    qt_app: QApplication, window: MainWindow, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """只读卷上的库**照常打开**，只在横幅上说一声（ADR 0038 决策 3）。

    退路是现成的：``FacetIndex`` 的 ``path=None`` 分支只在内存里缓存，本次会话能用。
    老行为是弹一个错误框然后拒绝打开 —— 而那台机器上用户别无选择。
    """
    root = tmp_path / "只读的照片"
    root.mkdir()
    make_photo("一张.jpg", directory=root)
    # 在配置目录该在的位置放一个**文件**：``mkdir`` 必然失败，而根目录本身可读可写，
    # 于是这正是「建不出来但库还能用」的形状（沙箱里造不出真的只读卷）。
    (root / CONFIG_DIR_NAME).write_bytes("挡住了".encode())

    assert window.open_library(root) is True
    wait_for_idle(qt_app, window, what="扫描与地点解析")

    assert window._library is not None
    assert window._library.config_dir_ready is False
    # 索引退成内存模式：能读能写，只是不落盘。
    assert window._library.facet_index.path is None
    assert window._banner.isVisible(), "降级了就该在横幅上说一声"
    assert "写不进去" in window._banner.text()


def test_a_library_that_cannot_be_read_reports_it_without_hanging(
    qt_app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    """库本身打不开（被移走 / 删掉）时要如实报错，而**用户点的那一下**必须得到回应。

    这一条与上面那条降级是一对：**降级的前提是库还能用**。库自己没了就不是降级，
    是打不开，必须说清是哪一条路走不通。

    ``_report_error`` 弹的是**模态** ``QMessageBox.critical`` —— 离屏下没人点确定，
    真弹出来这个用例会**永远挂住**（实测过：整个文件跑 5 分钟不返回）。所以这里把
    它换成一个记名函数的假弹窗：既证明「确实报了错」，又不会把测试挂死。
    """
    missing = tmp_path / "已经被移走的照片"
    reported: list[str] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(QMessageBox, "critical", lambda *args: reported.append(args[-1]))
        assert window.open_library(missing) is False

    assert window._library is None
    assert reported and "打不开这个文件夹" in reported[0]


def test_restoring_the_last_library_at_startup_is_silent(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """启动时自动恢复那个库**开不起来就静默留在欢迎页**，一个框都不弹。

    README 承诺的是「下次启动回到上次打开的库」，而那个库可能已经被移走 ——
    那是用户的日常操作，不是故障。**这一条曾经是个回归**：把 ``silent`` 去掉之后
    每次启动都被一个模态错误框拦住，而这个用例会直接挂住（没人点确定）。
    """
    session_base = tmp_path / "会话"
    missing = tmp_path / "已经被移走的照片"
    save_session(SessionState(recent_roots=(missing,)), base=session_base)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            QMessageBox,
            "critical",
            lambda *args: pytest.fail("自动恢复失败时不该弹模态框"),
        )
        instance = MainWindow(session_base=session_base)
        instance.resize(900, 600)
        instance.show()
    try:
        assert instance._library is None, "开不起来就该留在欢迎页"
    finally:
        instance.close()


def test_the_old_ui_preferences_are_migrated_into_the_library(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """从老版本升上来的用户，他上次选的浏览方式不会被静默重置（ADR 0038 决策 4）。

    那几项原本读自 ``state.json``，现在读自各个库的 ``config.toml``。库里还没有
    ``[ui]`` 表时要从旧会话文件搬一次 —— 搬完下一次打开就不必再搬。
    """
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("一张.jpg", directory=root)
    session_base = tmp_path / "会话"
    paths.app_state_file(base=session_base).parent.mkdir(parents=True)
    paths.app_state_file(base=session_base).write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "recent_roots": [str(root)],
                "browse_mode": BROWSE_MODE_CALENDAR,
                "sort_facet": "name",
            }
        ),
        encoding="utf-8",
    )

    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    try:
        wait_for_idle(qt_app, instance, what="扫描与地点解析")
        assert load_config(root).ui.browse_mode == BROWSE_MODE_CALENDAR
        assert instance._browse_mode == BROWSE_MODE_CALENDAR
        assert load_config(root).ui.sort_facet == "name"
        assert instance._sort_facet == "name"
    finally:
        instance.close()


def test_the_migration_happens_once_per_library(tmp_path: Path) -> None:
    """迁移**每个库只做一次**，于是「在 A 库迁来的偏好」不会顺着切库传染给 B 库。

    搬完库里就有 ``[ui]`` 表了，下一次打开 ``ui.is_empty`` 不成立，迁移不再发生。
    """
    from gallery.core.library.session import legacy_ui_state

    library_root = tmp_path / "照片"
    library_root.mkdir()
    session_base = tmp_path / "会话"
    paths.app_state_file(base=session_base).parent.mkdir(parents=True)
    paths.app_state_file(base=session_base).write_text(
        json.dumps({"version": SESSION_VERSION, "browse_mode": BROWSE_MODE_CALENDAR}),
        encoding="utf-8",
    )

    assert legacy_ui_state(base=session_base) is not None, "先确认确实有东西可迁"

    from gallery.core.library.config import LibraryUIState, prepare_library, save_config

    prepare_library(library_root)
    save_config(
        library_root,
        load_config(library_root).__class__(ui=LibraryUIState(browse_mode=BROWSE_MODE_CALENDAR)),
    )

    # 库里已经有 [ui] 表了 —— 迁移的判据（`ui.is_empty`）不再成立。
    assert load_config(library_root).ui.is_empty is False
