"""「新建相册」⇧⌘N 的界面测试（ADR 0022）。

覆盖用户真正会经历的那条链：网格空白处右键（或按 ⇧⌘N）→ 磁盘上多出一个空文件夹 →
网格自己长出新的一格并选中 → **行内重命名的输入框已经在那一行上等着** → 回车，
名字改完，磁盘上与界面上是同一个名字。

还有四件同样重要的事：

- **建在当前这一层**（相册模式是当前目录，「全部照片」是库根）；
- **建不了就不出现**（分组视图里那一层不是文件夹）；
- **绝不覆盖**：连按两次得到「新建相册」与「新建相册 2」，用户自己那个目录一个字节都不动；
- **建完撤不掉**（撤销就是删文件夹），而**紧接着那一次改名能撤**（⌘Z 把它改回去）。

全部在 ``tmp_path`` 里进行，照片都是当场造的临时文件。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import wait_until

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata.journal import ACTION_CREATE, ACTION_RENAME, read_records  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描完成的时限。

#: 新建出来的相册默认叫什么。
DEFAULT_NAME = "新建相册"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个库：``京都`` 两张照片，``奈良`` 空着。"""
    library = tmp_path / "照片"
    (library / "京都").mkdir(parents=True)
    (library / "奈良").mkdir()
    for index, name in enumerate(("一张.jpg", "另一张.jpg")):
        Image.new("RGB", (16, 12), (200, 120, 40 + index)).save(library / "京都" / name)
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    instance = _open(qt_app, root, browse_mode=BROWSE_MODE_ALBUMS)
    yield instance
    instance.close()


def _open(qt_app: QApplication, root: Path, *, browse_mode: str = BROWSE_MODE_ALBUMS) -> MainWindow:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    assert wait_until(qt_app, lambda: instance._model.rowCount() > 0), "扫描没有在时限内完成"
    return instance


def _context_on_blank(window: MainWindow) -> QMenu:
    """模拟一次真实的空白处右键：视图先把选区清干净，窗口再拼菜单（两步是分开的）。"""
    window._grid.select_for_context(-1)
    menu = window.context_menu_for(-1)
    assert menu is not None
    return menu


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions()]


def _enter(qt_app: QApplication, window: MainWindow, folder: Path) -> None:
    window.enter_folder(folder)
    assert wait_until(qt_app, lambda: window._scan_job is None), "这一层没有列完"


def _commit_editor(qt_app: QApplication, window: MainWindow, name: str) -> None:
    """把行内输入框里的字改成 ``name`` 并回车提交。"""
    editor = window._grid._rename_edit
    assert editor.is_editing(), "没有行内输入框在等着"
    editor.setText(name)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    qt_app.processEvents()


# ---- 菜单上那一项 ------------------------------------------------------------


def test_it_sits_at_the_top_of_the_blank_menu(window: MainWindow) -> None:
    """摆在空白处菜单的最上面，与访达一致（访达那一条也在第一条）。"""
    labels = _texts(_context_on_blank(window))

    assert labels[0] == "新建相册"
    assert labels.index("新建相册") < labels.index("全选")


def test_it_is_absent_from_the_entry_menus(qt_app: QApplication, window: MainWindow) -> None:
    """照片、相册、分组卡片那三张菜单里都没有这一条 —— 与访达一致。

    右键一个东西时用户要的是「对这个东西做什么」，新建是「对这一层做什么」。
    """
    album_row = window._model.row_of_path(window._library.root / "京都")
    assert album_row >= 0

    album = window.context_menu_for(album_row)
    photo = window.context_menu_for(album_row + 1)

    assert album is not None and photo is not None
    assert "新建相册" not in _texts(album)
    assert "新建相册" not in _texts(photo)


def test_it_lives_in_the_file_menu_with_the_finder_shortcut(window: MainWindow) -> None:
    """「文件 → 新建相册」也有这一条，快捷键与访达一样是 ⇧⌘N（ADR 0022）。"""
    file_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "文件" and action.menu() is not None
    )

    assert file_menu is not None
    labels = [action.text() for action in file_menu.actions()]
    assert "新建相册" in labels
    assert labels.index("新建相册") < labels.index("重命名…")
    assert window._new_album_action.shortcut() == QKeySequence("Ctrl+Shift+N")


def test_the_shortcut_is_registered_on_the_window(window: MainWindow) -> None:
    """快捷键必须**挂在窗口上**才按得出来：右键菜单是用完即弃的，靠它注册等于没注册。

    与 ``⌘⏎``（「在访达中显示」）同一件事。
    """
    assert window._new_album_action in window.actions()


# ---- 建在哪 ------------------------------------------------------------------


def test_it_builds_in_the_folder_being_browsed(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """站在「奈良」里建，文件夹就落在奈良底下 —— 当前这一层，不是库根。"""
    _enter(qt_app, window, root / "奈良")

    window.create_album()

    assert (root / "奈良" / DEFAULT_NAME).is_dir()
    assert not (root / DEFAULT_NAME).exists()
    assert not (root / "京都" / DEFAULT_NAME).exists()


def test_at_the_top_level_it_builds_in_the_library_root(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """停在顶层时，「当前这一层」就是库根。"""
    window.create_album()

    assert (root / DEFAULT_NAME).is_dir()


def test_all_photos_mode_builds_in_the_library_root(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """「全部照片」是平铺的，唯一说得清的目录是库根（与粘贴同一判据）。"""
    window.set_browse_mode(BROWSE_MODE_FLAT)
    assert wait_until(qt_app, lambda: window._scan_job is None)

    window.create_album()

    assert (root / DEFAULT_NAME).is_dir()


def test_it_is_absent_inside_a_group(qt_app: QApplication, window: MainWindow) -> None:
    """分组视图里**不建**：那一层不是文件夹，网格上摆的也全是照片（ADR 0022 决策 1）。

    与粘贴不同 —— 粘贴在「这一组照片本来就在同一个相册」时还有地方可粘，新建相册没有。
    """
    photo = PhotoRef.from_path(window._library.root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    window.enter_group(group)
    qt_app.processEvents()

    assert window._level_directory() is None
    assert window._can_create_album() is False
    assert window._new_album_action.isEnabled() is False
    assert "新建相册" not in _texts(_context_on_blank(window))


# ---- 建完：上屏 + 立刻改名 ---------------------------------------------------


def test_the_new_album_shows_up_selected(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """建完那一格立刻就在网格上，而且被选中 —— 不必等监听器慢慢报上来。"""
    _enter(qt_app, window, root / "奈良")
    before = window._model.rowCount()

    window.create_album()
    qt_app.processEvents()

    row = window._model.row_of_path(root / "奈良" / DEFAULT_NAME)
    assert row >= 0
    assert window._model.rowCount() == before + 1
    assert isinstance(window._model.entry_at(row), FolderRef)
    assert row in window._grid.selected_rows()


def test_the_empty_level_notice_goes_away(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """空文件夹里建了相册，「这一层是空的」那句提示就该收起来。

    监听器不会替我们做这件事 —— 自家写的那次已经被摘掉了（否则它会重复应用）。
    """
    _enter(qt_app, window, root / "奈良")
    assert "既没有子文件夹" in window._banner.text()

    window.create_album()
    qt_app.processEvents()

    assert window._banner.isVisible() is False


def test_the_rename_editor_is_waiting_on_that_cell(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """**建完就在改名状态** —— 访达那个手感，也是用户明确要的那一条。

    光标已经在等，而不是先摆一个「新建相册」让人再去点一次重命名。
    """
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()

    editor = window._grid._rename_edit
    assert editor.is_editing() is True
    assert editor.text() == DEFAULT_NAME
    row = window._model.row_of_path(root / "奈良" / DEFAULT_NAME)
    assert editor.geometry().intersects(window._grid.visualRect(window._model.index(row, 0)))


def test_the_whole_name_is_selected_so_typing_replaces_it(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """相册名整段选中：直接打字就是在改名字（相册没有「扩展名」这回事）。"""
    window.create_album()
    qt_app.processEvents()

    assert window._grid._rename_edit.selectedText() == DEFAULT_NAME


def test_return_renames_the_folder_it_just_made(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """端到端：回车之后磁盘上、网格上、索引里是同一个新名字。"""
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()
    _commit_editor(qt_app, window, "2025 苏州")

    assert (root / "奈良" / "2025 苏州").is_dir()
    assert not (root / "奈良" / DEFAULT_NAME).exists()
    row = window._model.row_of_path(root / "奈良" / "2025 苏州")
    assert row >= 0
    assert window._model.entry_at(row).name == "2025 苏州"


def test_escape_leaves_the_empty_folder_alone(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """按 Esc 取消改名：文件夹还在（它是建出来的，不是打字打出来的）。

    要去掉这个空文件夹得自己选中它按 ⌘⌫ —— 那条路有日志、能再撤销。
    """
    window.create_album()
    qt_app.processEvents()

    QTest.keyClick(window._grid._rename_edit, Qt.Key.Key_Escape)
    qt_app.processEvents()

    assert window._grid.is_renaming() is False
    assert (root / DEFAULT_NAME).is_dir()
    assert window._model.row_of_path(root / DEFAULT_NAME) >= 0


def test_the_watcher_does_not_add_a_second_row(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """自家写的那一次要记进「自家写操作」：监听器稍后报上来的同一件事不能重复应用。

    不然网格里会出现两个同名相册卡片，而磁盘上只有一个文件夹。
    """
    before = window._model.rowCount()

    window.create_album()
    qt_app.processEvents()
    # 给监听器留出跑一趟的时间（它有合并去抖，见 ADR 0013）。
    assert wait_until(qt_app, lambda: window._model.rowCount() > before, timeout=2.0)

    assert window._model.rowCount() == before + 1
    assert window._model.row_of_path(root / DEFAULT_NAME) >= 0


# ---- 绝不覆盖 ---------------------------------------------------------------


def test_a_second_album_leaves_the_first_one_alone(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """建第二个相册时，第一个连同它里面的照片原封不动。

    顺便验证建完那次改名是真的落到了磁盘上 —— 第二个相册是在「京都 2025」之后
    才建的，两者互不干扰。
    """
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()
    _commit_editor(qt_app, window, "京都 2025")
    Image.new("RGB", (16, 12), (10, 10, 10)).save(root / "奈良" / "京都 2025" / "我的照片.jpg")

    window.create_album()
    qt_app.processEvents()

    assert (root / "奈良" / "京都 2025" / "我的照片.jpg").is_file(), "第一个相册不该被动过"
    assert (root / "奈良" / DEFAULT_NAME).is_dir()
    assert window._model.row_of_path(root / "奈良" / DEFAULT_NAME) >= 0


def test_a_duplicate_default_name_gets_numbered(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """默认名被占了就编号，与粘贴共用同一份规矩（``naming.free_name_in``）。"""
    (root / "奈良" / DEFAULT_NAME).mkdir()
    (root / "奈良" / DEFAULT_NAME / "用户的照片.jpg").write_bytes(b"x")
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()

    assert (root / "奈良" / f"{DEFAULT_NAME} 2").is_dir()
    assert sorted(p.name for p in (root / "奈良" / DEFAULT_NAME).iterdir()) == ["用户的照片.jpg"]


# ---- 记账与撤销 -------------------------------------------------------------


def test_creating_and_renaming_are_both_journaled(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """两条记录都在：一次 ``create``、一次 ``rename``。"""
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()
    _commit_editor(qt_app, window, "2025 苏州")

    actions = [record.action for record in read_records(root)]
    assert actions == [ACTION_CREATE, ACTION_RENAME]
    assert read_records(root)[1].is_dir is True


def test_undo_takes_the_name_back_but_keeps_the_folder(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """⌘Z 撤的是**那一次改名**：名字改回去，空文件夹留在原地。

    撤掉文件夹等于删除，红线 1 说那只能由用户亲手触发（选中它按 ⌘⌫，那条路有日志）。
    """
    _enter(qt_app, window, root / "奈良")

    window.create_album()
    qt_app.processEvents()
    _commit_editor(qt_app, window, "2025 苏州")
    assert (root / "奈良" / "2025 苏州").is_dir()

    window.undo_last_write()
    qt_app.processEvents()

    assert (root / "奈良" / DEFAULT_NAME).is_dir()
    assert not (root / "奈良" / "2025 苏州").exists()
    assert window._model.row_of_path(root / "奈良" / DEFAULT_NAME) >= 0


# ---- 建不出来的时候 ---------------------------------------------------------


def test_saying_so_when_the_folder_is_gone(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """这一层在磁盘上没了（用户刚在访达里把它删了）：如实说，不静默。"""
    _enter(qt_app, window, root / "奈良")
    shutil.rmtree(root / "奈良")

    window.create_album()

    assert "不是一个文件夹" in window._banner.text()
    assert not (root / DEFAULT_NAME).exists()


def test_it_is_disabled_in_the_viewer(qt_app: QApplication, window: MainWindow, root: Path) -> None:
    """大图页里没有「当前这一层」可言：快捷键是灰的。"""
    _enter(qt_app, window, root / "京都")
    assert window._model.rowCount() == 2

    window._open_detail(window._model.index(0, 0))
    qt_app.processEvents()

    assert window._new_album_action.isEnabled() is False


def test_it_is_disabled_while_renaming(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """改到一半再来 ⇧⌘N：不能让两个行内输入框同时活着。

    键盘正归输入框（与 rename / trash 同一个 ``idle``），此时按 ⇧⌘N 什么也不发生。
    """
    album_row = window._model.row_of_path(root / "京都")
    assert album_row >= 0
    window._grid.select_row(album_row)
    window.rename_current_entry()
    qt_app.processEvents()
    assert window._grid.is_renaming() is True

    assert window._new_album_action.isEnabled() is False
    window.create_album()
    qt_app.processEvents()

    assert not (root / DEFAULT_NAME).exists(), "改着名的时候不该凭空多一个相册"
    assert window._grid.is_renaming() is True
