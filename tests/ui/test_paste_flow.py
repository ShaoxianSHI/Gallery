"""⌘V 粘贴的界面测试。

覆盖用户真正会经历的那条链：拷贝 → 换到另一个相册 → ⌘V → 文件落到那个相册、
网格自己长出新的一行、横幅说清粘了什么、源文件没被动。

还有四件同样重要的事：

- **粘不了的时候压根不出现**（剪贴板里没有文件、或当前这一层不对应一个真实文件夹）；
- **菜单里摆在哪、粘到哪由落点定**（ADR 0018 决策 7）：空白处粘进当前这一层、右键一个
  相册则粘进**那个相册内部**、按在照片上不摆这一条（⌘V 仍然照常用）；
- **在同一个相册里 ⌘C 再 ⌘V 什么也不做**（用户明确要求）；
- **按磁盘上的真实位置算** —— 同一个相册的两个不同分组（按相机 / 按年份）
  认成同一个目录，散在多个相册的组则没有地方可粘。

全部在 ``tmp_path`` 里进行，照片都是当场造的临时文件。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.facade import Library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.ui import desktop  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描 / 粘贴完成的时限。
WAIT_SECONDS = 8.0


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def empty_clipboard(qt_app: QApplication) -> Iterator[None]:
    """每条用例都从「剪贴板是空的」开始。

    剪贴板是**进程级**的，同一个 worker 上跑完的用例会把它留给下一条：不摆这一下，
    「菜单里有没有『粘贴』」就成了时序的函数。
    """
    QGuiApplication.clipboard().clear()
    yield
    QGuiApplication.clipboard().clear()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个库：``京都`` 两张照片、``奈良`` 空着。"""
    from PIL import Image

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
    save_session(SessionState(recent_roots=(root,), browse_mode=browse_mode), base=session_base)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    assert _wait(qt_app, lambda: instance._model.rowCount() > 0), "扫描没有在时限内完成"
    return instance


def _wait(qt_app: QApplication, predicate: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    qt_app.processEvents()
    return predicate()


def _enter(qt_app: QApplication, window: MainWindow, folder: Path) -> None:
    window.enter_folder(folder)
    assert _wait(qt_app, lambda: window._scan_job is None), "这一层没有列完"


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions()]


def _paste_and_wait(qt_app: QApplication, window: MainWindow) -> str:
    window.paste_into_current_folder()
    assert _wait(qt_app, lambda: window._write_job is None), "粘贴没有在时限内结束"
    return window._banner.text()


def _trigger_paste(qt_app: QApplication, window: MainWindow, menu: QMenu) -> str:
    """点菜单里那一条「粘贴」，等它把文件落完。"""
    action = next(item for item in menu.actions() if item.text() == "粘贴")
    action.trigger()
    assert _wait(qt_app, lambda: window._write_job is None), "粘贴没有在时限内结束"
    return window._banner.text()


def _select_rows(window: MainWindow, rows: tuple[int, ...]) -> None:
    """选中若干行（等价于 ⌘ 连点）—— 「多选」这件事不能靠默认选区去赌。"""
    model = window._model
    window._grid.setCurrentIndex(model.index(rows[0], 0))
    selection = window._grid.selectionModel()
    assert selection is not None
    selection.clearSelection()
    for row in rows:
        selection.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


# ---- 菜单上那一项：摆在哪、粘到哪（ADR 0018 决策 7）---------------------------


def test_paste_sits_under_copy_path(qt_app: QApplication, window: MainWindow) -> None:
    """右键一个**相册**：排在「拷贝路径」正下方，凑成访达的那一组（ADR 0017 / 0018）。

    位置没变，变的是**粘到哪**（下面那几条）。
    """
    desktop.copy_files_to_clipboard([window._library.root / "京都" / "一张.jpg"])
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    labels = _texts(menu)
    assert labels.index("拷贝") < labels.index("拷贝路径") < labels.index("粘贴")
    assert labels.index("粘贴") < labels.index("重命名…")


def test_the_album_menu_paste_carries_that_album_as_its_target(
    qt_app: QApplication, window: MainWindow
) -> None:
    """相册菜单里那一条，目标是**右键按着的那个相册**，且**不带快捷键**。

    ⌘V 归菜单栏那一条，说的却是「粘进当前这一层」—— 停在库根时那是库根，与这一条不是
    一回事，不能让一枚键指两处。
    """
    desktop.copy_files_to_clipboard([window._library.root / "京都" / "一张.jpg"])
    row = window._model.row_of_path(window._library.root / "奈良")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    action = next(item for item in menu.actions() if item.text() == "粘贴")
    assert action is window._paste_into_album_action
    assert action.data() == window._library.root / "奈良"
    assert action.data() != window._library.root, "落进库根就是「同级」，不是用户要的"
    assert action.shortcut().isEmpty()
    assert window._paste_action.shortcut() == QKeySequence("Ctrl+V")


def test_pasting_on_an_album_lands_inside_it(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """停在库根时右键「奈良」再粘贴 → 落进**奈良**，不是落进它所在的库根。

    这正是「粘到相册内部，而不是同级」：旧行为一律粘进 ``_paste_target()``（当前这一层），
    停在库根时就是库的顶层，于是多出一堆散片照片。
    """
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    row = window._model.row_of_path(root / "奈良")
    assert row >= 0
    menu = window.context_menu_for(row)
    assert menu is not None

    message = _trigger_paste(qt_app, window, menu)

    assert (root / "奈良" / "一张.jpg").is_file(), "必须落在相册内部"
    assert not (root / "一张.jpg").exists(), "不能落在与相册同级的库根里"
    assert (root / "京都" / "一张.jpg").is_file(), "源必须还在原地"
    assert message == "已粘贴 1 项到「奈良」。"


def test_pasting_on_a_nested_album_lands_inside_that_album(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """进了「京都」再右键里面的「东山」→ 粘进东山，不是粘进京都本身。

    相册可以只有一层：停在库根时粘进「奈良」，这里再验一次「不是粘进父相册」。
    """
    nested = root / "京都" / "东山"
    nested.mkdir()
    (root / "奈良" / "待粘.jpg").write_bytes((root / "京都" / "一张.jpg").read_bytes())
    _enter(qt_app, window, root / "京都")
    desktop.copy_files_to_clipboard([root / "奈良" / "待粘.jpg"])
    row = window._model.row_of_path(nested)
    assert row >= 0
    menu = window.context_menu_for(row)
    assert menu is not None

    _trigger_paste(qt_app, window, menu)

    assert (nested / "待粘.jpg").is_file()
    assert not (root / "京都" / "待粘.jpg").exists(), "不能落在父相册里"
    assert not (root / "待粘.jpg").exists(), "更不能落在顶层"


def test_the_blank_area_menu_pastes_into_the_level_you_are_looking_at(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """空白处的菜单**有**「粘贴」，摆在「新建相册」下面，粘进当前这一层。

    空白处就是「就粘到我现在看着的这一层里」，那里没有「按在哪一张上」要猜，因此这是除
    ⌘V 之外最没有歧义的一个落点（ADR 0018 决策 7）。它用的就是菜单栏那枚 ⌘V，所以快捷键
    那一列照常写着 ⌘V。
    """
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "奈良")
    window._grid.select_for_context(-1)
    assert window._grid.selected_rows() == []

    menu = window.context_menu_for(-1)

    assert menu is not None
    labels = _texts(menu)
    assert labels[:3] == ["新建相册", "粘贴", ""], "与「新建相册」同属最上面那一组"
    assert next(item for item in menu.actions() if item.text() == "粘贴") is window._paste_action

    message = _trigger_paste(qt_app, window, menu)

    assert (root / "奈良" / "一张.jpg").is_file()
    assert message == "已粘贴 1 项到「奈良」。"


def test_the_blank_menu_leaves_no_empty_slot_when_paste_is_the_only_one(
    qt_app: QApplication, window: MainWindow
) -> None:
    """「新建相册」与「粘贴」共处最上面那一组：少一条时**开头不能挂一道空档**。

    分组视图里建不了相册（那一层不是文件夹，ADR 0022），于是那一组只剩「粘贴」；两条都
    没有时（剪贴板空着）整组连同分隔线一起没有 —— 否则菜单第一行就是一道谁也看不懂的
    横线。相册模式里那一条在第几行，另见本文件里
    ``test_the_blank_area_menu_pastes_into_the_level_you_are_looking_at``。
    """
    photo = PhotoRef.from_path(window._library.root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    window.enter_group(group)
    qt_app.processEvents()
    assert window._can_create_album() is False

    (window._library.root / "奈良" / "一张.jpg").write_bytes(
        (window._library.root / "京都" / "一张.jpg").read_bytes()
    )
    desktop.copy_files_to_clipboard([window._library.root / "奈良" / "一张.jpg"])
    with_paste = window.context_menu_for(-1)
    assert with_paste is not None
    assert _texts(with_paste)[:2] == ["粘贴", ""]

    QGuiApplication.clipboard().clear()
    without_paste = window.context_menu_for(-1)
    assert without_paste is not None
    assert _texts(without_paste)[0] == "全选", "整组都没有时不该留一道开头的分隔线"


def test_pasting_on_a_photo_is_not_offered(qt_app: QApplication, window: MainWindow) -> None:
    """按在一张照片上（单选 / 多选都是）**不出现**「粘贴」，而 ⌘V 照旧能用。

    用户明确要求的：这一格未必属于当前这一层（全部照片、分组那几屏都是各相册混在一起
    的），摆出来只会让人猜「粘到哪」。「拿进拿出」那一组里其余几行与分隔线照旧
    （ADR 0023 决策 2）。
    """
    _enter(qt_app, window, window._library.root / "京都")
    # 源放在**另一个**相册里：在京都里 ⌘C 再 ⌘V 什么也不做（ADR 0018 决策 2），那样
    # 证明不了 ⌘V 还能用。
    (window._library.root / "奈良" / "一张.jpg").write_bytes(
        (window._library.root / "京都" / "一张.jpg").read_bytes()
    )
    desktop.copy_files_to_clipboard([window._library.root / "奈良" / "一张.jpg"])
    first = window._model.row_of_path(window._library.root / "京都" / "一张.jpg")
    second = window._model.row_of_path(window._library.root / "京都" / "另一张.jpg")
    assert first >= 0 and second >= 0
    window._grid.select_for_context(first)

    single = window.context_menu_for(first)
    assert single is not None
    assert "粘贴" not in _texts(single)
    assert "移动到相册" in _texts(single), "少的是「粘贴」那一行，不是整组"

    _select_rows(window, (first, second))
    multiple = window.context_menu_for(first)
    assert multiple is not None
    assert "粘贴" not in _texts(multiple)

    # ⌘V 不受菜单摆不摆的影响：粘进当前这一层（这里就是京都），动作也仍然是通的。
    assert window._can_paste() is True
    assert window._paste_action.isEnabled() is True
    _paste_and_wait(qt_app, window)
    assert (window._library.root / "京都" / "一张 2.jpg").is_file()


def test_multi_album_selection_has_no_paste(qt_app: QApplication, window: MainWindow) -> None:
    """多选相册：没有「粘贴」—— 粘进哪一个相册说不清，于是整条不出现。

    与「打开」「重命名」同一个取向（ADR 0014 决策 4）：摆一条点不动的，不如不出现。
    """
    desktop.copy_files_to_clipboard([window._library.root / "京都" / "一张.jpg"])
    first = window._model.row_of_path(window._library.root / "京都")
    second = window._model.row_of_path(window._library.root / "奈良")
    assert first >= 0 and second >= 0
    _select_rows(window, (first, second))

    menu = window.context_menu_for(first)

    assert menu is not None
    assert "粘贴" not in _texts(menu)


def test_paste_is_absent_when_the_clipboard_has_no_files(
    qt_app: QApplication, window: MainWindow
) -> None:
    """剪贴板里是文字时**不出现这一项**，而不是摆一条点不动的。

    相册与空白处两处都如此（照片、分组卡片、多选相册那几处本来就不摆）。
    """
    QGuiApplication.clipboard().setText("只是文字")
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0

    album = window.context_menu_for(row)
    blank = window.context_menu_for(-1)

    assert album is not None and blank is not None
    assert "粘贴" not in _texts(album)
    assert "粘贴" not in _texts(blank)
    assert window._paste_action.isEnabled() is False


def test_paste_lives_in_the_edit_menu(qt_app: QApplication, window: MainWindow) -> None:
    """⌘V 与 ⇧⌘C 相邻摆在「编辑」里（ADR 0014 决策 5：只活在右键菜单的命令没人记得住）。"""
    edit = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "编辑" and action.menu() is not None
    )

    assert edit is not None
    labels = [action.text() for action in edit.actions()]
    assert labels.index("拷贝路径") < labels.index("粘贴")
    assert window._paste_action.shortcut() == QKeySequence("Ctrl+V")


# ---- 目标目录：一切按磁盘上的真实位置算 --------------------------------------


def test_pastes_into_the_folder_being_browsed(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """在「京都」里 ⌘C，去「奈良」⌘V —— 落到奈良，源还在京都。"""
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "奈良")

    message = _paste_and_wait(qt_app, window)

    assert (root / "奈良" / "一张.jpg").is_file()
    assert (root / "京都" / "一张.jpg").is_file(), "源必须还在原地"
    assert message == "已粘贴 1 项到「奈良」。"


def test_pasting_in_the_same_folder_does_nothing(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """同一个相册里 ⌘C 再 ⌘V 什么也不做（用户明确要求），横幅要说清为什么。"""
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "京都")

    message = _paste_and_wait(qt_app, window)

    assert sorted(p.name for p in (root / "京都").iterdir()) == ["一张.jpg", "另一张.jpg"]
    assert message == "1 项本来就在「京都」里。"


def test_a_group_inside_one_album_pastes_into_that_album(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """分组不是文件夹，但**它底下只有一个相册时**，那个相册就是它的目录。

    这正是用户要的「不同分组如果是同一个相册，还是算同一目录」：按相机和按年份
    各开一张卡片，只要底下都是京都，粘进去的就是京都。
    """
    outside = root / "奈良" / "外面拷来的.jpg"
    outside.write_bytes(b"\xff\xd8\xff\xdb not really a jpeg")
    photo = PhotoRef.from_path(root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    window.enter_group(group)
    desktop.copy_files_to_clipboard([outside])

    assert window._paste_target() == root / "京都"
    assert window._can_paste() is True
    message = _paste_and_wait(qt_app, window)

    assert (root / "京都" / "外面拷来的.jpg").is_file()
    assert (root / "奈良" / "外面拷来的.jpg").is_file(), "源必须还在原地"
    assert message == "已粘贴 1 项到「京都」。"


def test_pasting_a_photo_of_that_same_album_from_a_group_does_nothing(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """分组指向的相册，就是「已经在同一目录」判据里的那个目录。"""
    photo = PhotoRef.from_path(root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    window.enter_group(group)
    desktop.copy_files_to_clipboard([root / "京都" / "另一张.jpg"])

    message = _paste_and_wait(qt_app, window)

    assert sorted(p.name for p in (root / "京都").iterdir()) == ["一张.jpg", "另一张.jpg"]
    assert message == "1 项本来就在「京都」里。"


def test_a_group_spanning_albums_has_nowhere_to_paste(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """散在多个相册里的组不对应任何一个文件夹：没有「粘贴」这回事。"""
    (root / "奈良" / "y.jpg").write_bytes(b"\xff\xd8\xff\xdb not really a jpeg")
    group = GroupRef(
        facet="camera",
        key="canon",
        label="Canon",
        photos=(
            PhotoRef.from_path(root / "京都" / "一张.jpg"),
            PhotoRef.from_path(root / "奈良" / "y.jpg"),
        ),
    )
    window._model.reorder([group])
    qt_app.processEvents()
    window.enter_group(group)
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])

    assert window._paste_target() is None
    assert window._can_paste() is False
    assert window._paste_action.isEnabled() is False


def test_all_photos_pastes_into_the_library_root(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """「全部照片」是平铺的，唯一说得清的目录是库根。"""
    window.set_browse_mode(BROWSE_MODE_FLAT)
    assert _wait(qt_app, lambda: window._scan_job is None)
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])

    assert window._paste_target() == window._library.root
    _paste_and_wait(qt_app, window)
    assert (root / "一张.jpg").is_file()


# ---- 真的会粘 ----------------------------------------------------------------


def test_new_file_shows_up_in_the_grid(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """粘完不用手动刷新：监听器（ADR 0013）自己把新照片添进来。

    这条正是监听器当初要解决的场景（访达拷一批照片进来），所以这里钉住它。
    """
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "奈良")
    before = window._model.rowCount()

    _paste_and_wait(qt_app, window)

    assert _wait(qt_app, lambda: window._model.rowCount() == before + 1)
    assert window._model.row_of_path(root / "奈良" / "一张.jpg") >= 0


def test_renamed_duplicate_is_named_in_the_banner(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """为了不覆盖而改了名，横幅必须把新名字说出来。"""
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "奈良")
    _paste_and_wait(qt_app, window)

    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    message = _paste_and_wait(qt_app, window)

    assert (root / "奈良" / "一张 2.jpg").is_file()
    assert message == "已粘贴 1 项到「奈良」（「一张.jpg」改名为「一张 2.jpg」）。"


def test_saying_so_when_the_clipboard_is_empty(qt_app: QApplication, window: MainWindow) -> None:
    """⌘V 键直接按下去（编辑菜单那条）也要有话可说，不能静默。"""
    _enter(qt_app, window, window._library.root)

    message = _paste_and_wait(qt_app, window)

    assert message == "剪贴板里没有可粘贴的文件。"


def test_a_second_paste_does_not_race_the_first(
    qt_app: QApplication, window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个粘贴同时跑会争同一个目标名字，后到的会把先到的盖掉。

    所以上一次没结束就再按 ⌘V，要被挡住。**把第一次复制卡住**才是确定性的写法 ——
    靠「文件小、任务快」去撞，那个偶发会随机器负载变。
    """
    started = threading.Event()
    release = threading.Event()
    real = Library.copy_into

    def slow_copy(self, target, sources, *, should_cancel=None):  # noqa: ANN001, ANN202
        started.set()
        release.wait(WAIT_SECONDS)
        return real(self, target, sources, should_cancel=should_cancel)

    monkeypatch.setattr(Library, "copy_into", slow_copy)
    desktop.copy_files_to_clipboard([root / "京都" / "一张.jpg"])
    _enter(qt_app, window, root / "奈良")

    window.paste_into_current_folder()
    assert started.wait(WAIT_SECONDS), "第一次粘贴没有开始"
    window.paste_into_current_folder()

    assert window._banner.text() == "上一次操作还没结束。"
    release.set()
    assert _wait(qt_app, lambda: window._write_job is None)
    assert sorted(p.name for p in (root / "奈良").iterdir()) == ["一张.jpg"]
