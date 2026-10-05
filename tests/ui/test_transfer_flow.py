"""「复制到相册 / 移动到相册」的界面测试（ADR 0019）。

覆盖用户真正会经历的那条链：选中照片 → 右键 → 挑一个相册 → 文件落到那个相册、
横幅说清做了什么、能不能撤销。

还有四件同样重要的事：

- **菜单上那两条摆在「拷贝路径」正下方**（用户指定的位置）；
- **子菜单里列的是全库的相册**，标签是相对路径；
- **库里一个相册都没有时那两条压根不出现**（空子菜单是个空框）；
- **移动是能 ⌘Z 撤销的** —— 用户敢用它全靠这一条（红线 1）。

全部在 ``tmp_path`` 里进行，照片都是当场造的临时文件。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS, wait_until

from gallery.core.constants import CONFIG_DIR_NAME  # noqa: E402
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.facade import Library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.paths import config_dir  # noqa: E402
from gallery.ui import desktop  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描 / 复制 / 移动完成的时限。


@pytest.fixture(autouse=True)
def empty_clipboard(qt_app: QApplication) -> Iterator[None]:
    """每条用例都从「剪贴板是空的」开始。

    剪贴板是**进程级**的，同一个 worker 上跑完的用例会把它留给下一条：不摆这一下，
    「菜单里有没有『粘贴』」就成了时序的函数（与 ``test_paste_flow`` 同一个理由）。
    """
    QGuiApplication.clipboard().clear()
    yield
    QGuiApplication.clipboard().clear()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个库：``京都`` 两张照片、``奈良`` 空着、``2025/苏州`` 空着（深层相册）。"""
    from PIL import Image

    library = tmp_path / "照片"
    (library / "京都").mkdir(parents=True)
    (library / "奈良").mkdir()
    (library / "2025" / "苏州").mkdir(parents=True)
    for index, name in enumerate(("一张.jpg", "另一张.jpg")):
        Image.new("RGB", (16, 12), (200, 120, 40 + index)).save(library / "京都" / name)
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    instance = _open(qt_app, root)
    yield instance
    instance.close()


@pytest.fixture
def flat_window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """一个**没有相册**的库（照片平铺在根目录）—— 用来验「那两条压根不出现」。"""
    from PIL import Image

    library = tmp_path / "平铺库"
    library.mkdir()
    Image.new("RGB", (16, 12), (9, 9, 9)).save(library / "散着的一张.jpg")
    prepare_library(library)
    instance = _open(qt_app, library)
    yield instance
    instance.close()


def _open(qt_app: QApplication, root: Path) -> MainWindow:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    assert wait_until(qt_app, lambda: instance._model.rowCount() > 0), "扫描没有在时限内完成"
    return instance


def _enter(qt_app: QApplication, window: MainWindow, folder: Path) -> None:
    window.enter_folder(folder)
    assert wait_until(qt_app, lambda: window._scan_job is None), "这一层没有列完"


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions()]


def _select_photo(window: MainWindow, name: str) -> int:
    """选中 ``京都`` 里的某张照片，返回它的行号（先摆前置条件，界面状态不是默认值）。"""
    path = window._library.root / "京都" / name
    row = window._model.row_of_path(path)
    assert row >= 0, f"网格里没有 {path}"
    window._grid.select_row(row)
    assert window._grid.selected_rows() == [row]
    return row


def _select_rows(window: MainWindow, *rows: int) -> None:
    """选中若干行（等价于 ⌘ 点 / Shift 连选），并把它们设为当前行。"""
    model = window._model
    selection = window._grid.selectionModel()
    assert selection is not None
    # 顺序要紧：setCurrentIndex 会先把已有选择清掉，所以当前行必须先定下来。
    window._grid.setCurrentIndex(model.index(rows[0], 0))
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows:
        selection.select(model.index(row, 0), flags)
    QApplication.processEvents()


def _submenu(menu: QMenu, title: str) -> QMenu:
    """从菜单里取出标题为 ``title`` 的那个子菜单，**并按一下展开它**。

    展开是必要的一步，不是多余的：相册清单是在 ``aboutToShow`` 里现列的
    （见 :func:`gallery.ui.menus.build_lazy_album_picker`）—— 右键的那一刻只建出两个
    空壳子菜单，用户真的把鼠标移上去才去列清单。这个助手发的就是那一下，于是
    「用户看到什么」与菜单构造那一刻的状态不再是两件事。
    """
    for action in menu.actions():
        if action.text() == title:
            found = action.menu()
            assert found is not None
            found.aboutToShow.emit()
            return found
    raise AssertionError(f"菜单里没有「{title}」，实际有：{_texts(menu)}")


def _album_item(menu: QMenu, label: str):
    """子菜单里那一项，并确认它确实是个子菜单项（不是分隔线、不是别的）。"""
    for action in menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"相册清单里没有「{label}」，实际有：{[a.text() for a in menu.actions()]}")


# ---- 菜单上那两条 ------------------------------------------------------------


def test_both_sit_under_copy_path(qt_app: QApplication, window: MainWindow) -> None:
    """紧挨在「拷贝路径」正下方（用户指定的位置，ADR 0019）。

    下面**不再是**「粘贴」——按在照片上不摆那一条了（ADR 0018 决策 7），这一张菜单里
    「把东西放到别处去」只剩这两条。
    """
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")
    # 剪贴板里得有文件，相册那一份菜单里的「粘贴」才会出现（ADR 0018 决策 6）——
    # 照片这一份与剪贴板无关了，粘不粘都摆。
    desktop.copy_files_to_clipboard([window._library.root / "京都" / "另一张.jpg"])

    menu = window.context_menu_for(row)

    assert menu is not None
    labels = _texts(menu)
    assert labels.index("拷贝") < labels.index("拷贝路径")
    assert labels.index("复制到相册") == labels.index("拷贝路径") + 1
    assert labels.index("移动到相册") == labels.index("复制到相册") + 1
    assert "粘贴" not in labels


def test_both_are_submenus_not_plain_rows(qt_app: QApplication, window: MainWindow) -> None:
    """两条都是**子菜单**（要挑一个相册），不是按下去就直接执行的动作。"""
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")

    menu = window.context_menu_for(row)

    assert menu is not None
    for title in ("复制到相册", "移动到相册"):
        assert _submenu(menu, title).isEnabled()


def test_the_album_list_is_the_whole_library(qt_app: QApplication, window: MainWindow) -> None:
    """列的是**全库**的相册（含深层的），标签是相对库根的路径。

    深层相册也要能直接挑到 —— 用户要的是「挪到某个相册去」，不是「挪到这一层能看见的
    那几个去」（ADR 0019 决策 1）。
    """
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")

    menu = window.context_menu_for(row)
    assert menu is not None
    labels = [action.text() for action in _submenu(menu, "复制到相册").actions()]

    assert labels == ["2025", "2025/苏州", "京都", "奈良"]


def test_every_item_carries_its_target(qt_app: QApplication, window: MainWindow) -> None:
    """每一项都把目标路径挂在 ``data()`` 上（槽靠它知道往哪儿搬）。"""
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")

    menu = window.context_menu_for(row)
    assert menu is not None

    action = _album_item(_submenu(menu, "移动到相册"), "2025/苏州")
    assert action.data() == window._library.root / "2025" / "苏州"


def test_a_new_album_shows_up_without_restarting(qt_app: QApplication, window: MainWindow) -> None:
    """清单**每次弹出时现列**：在访达里新建的相册，下一次右键就能选到。"""
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")
    before = window.context_menu_for(row)
    assert before is not None
    assert "新建的相册" not in [a.text() for a in _submenu(before, "复制到相册").actions()]

    (window._library.root / "新建的相册").mkdir()

    after = window.context_menu_for(row)
    assert after is not None
    assert "新建的相册" in [a.text() for a in _submenu(after, "复制到相册").actions()]


def test_no_album_means_no_menu_at_all(qt_app: QApplication, flat_window: MainWindow) -> None:
    """库里一个相册都没有时那两条**压根不出现** —— 空子菜单点开是个空框。

    与「粘不了就不出现」同一个取向（ADR 0014 决策 4 / ADR 0018 决策 6）。
    """
    row = flat_window._model.row_of_path(flat_window._library.root / "散着的一张.jpg")
    assert row >= 0
    flat_window._grid.select_row(row)

    menu = flat_window.context_menu_for(row)

    assert menu is not None
    assert "复制到相册" not in _texts(menu)
    assert "移动到相册" not in _texts(menu)


def test_album_menu_does_not_offer_them(qt_app: QApplication, window: MainWindow) -> None:
    """相册菜单里没有这两条：搬相册会动到整棵子树，是另一笔账（ADR 0019 决策 5）。"""
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    assert wait_until(qt_app, lambda: window._model.rowCount() > 0)
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0
    window._grid.select_row(row)

    menu = window.context_menu_for(row)

    assert menu is not None
    assert "复制到相册" not in _texts(menu)
    assert "移动到相册" not in _texts(menu)


# ---- 真的搬 ------------------------------------------------------------------


def test_switching_libraries_mid_transfer_does_not_leave_a_phantom_undo(
    qt_app: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """搬一半换库：闸门必须跟着收，否则横幅骗人说「⌘Z 可以撤销」。

    缺陷现场：⌘V 复制几百 MB 跑到一半，用户用「文件 → 打开文件夹…」切了另一个库。
    换库时 `_teardown_library` 忘了取消写操作闸门，于是那次复制接着在**旧库**上跑完；
    收尾时 `_on_transfer_done` 拿着**新库**去问「下一次 ⌘Z 撤什么」—— 记录明明
    记在旧库的 journal 里，界面上却一条都撤不掉。
    """
    from PIL import Image

    other = tmp_path / "另一个库"
    (other / "京都").mkdir(parents=True)
    Image.new("RGB", (16, 12), (1, 2, 3)).save(other / "京都" / "一张.jpg")
    prepare_library(other)
    started = threading.Event()
    real_copy_into = Library.copy_into

    def slow_copy(self: Library, *args: object, **kwargs: object) -> object:
        started.set()
        # 让任务真的跨过「提交 → 换库 → 收尾」这三步，别靠时间碰运气。
        for _ in range(40):
            qt_app.processEvents()
            if window._library is not None and window._library.root == other:
                break
            time.sleep(0.005)
        return real_copy_into(self, *args, **kwargs)

    monkeypatch.setattr(Library, "copy_into", slow_copy)
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "奈良", move=False)
    assert started.wait(5.0), "复制没有起步"
    assert window.open_library(other) is True

    assert window._write_job is None, "换库时写操作闸门必须收掉，否则旧库的那次会接着跑"
    assert window._library is not None and window._library.root == other
    window.deleteLater()


def test_copying_puts_a_copy_in_the_target(qt_app: QApplication, window: MainWindow) -> None:
    """「复制到相册」：目标里多一份，**原处一个字节都没动**。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")
    source = window._library.root / "京都" / "一张.jpg"
    before = source.read_bytes()

    window._transfer_selected(window._library.root / "奈良", move=False)

    assert wait_until(qt_app, lambda: window._write_job is None), "复制没有在时限内结束"
    copied = window._library.root / "奈良" / "一张.jpg"
    assert copied.read_bytes() == before
    assert source.exists(), "复制不是移动：源必须还在原地"
    assert "已复制 1 项到「奈良」" in window._banner.text()


def test_moving_takes_the_photo_out_of_the_source_album(
    qt_app: QApplication, window: MainWindow
) -> None:
    """「移动到相册」：目标里有了，**原处不再有** —— 这是它与复制的唯一分界。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "奈良", move=True)

    assert wait_until(qt_app, lambda: window._write_job is None), "移动没有在时限内结束"
    assert (window._library.root / "奈良" / "一张.jpg").exists()
    assert not (window._library.root / "京都" / "一张.jpg").exists()
    assert "已移动 1 项到「奈良」" in window._banner.text()


def test_the_banner_says_it_can_be_undone(qt_app: QApplication, window: MainWindow) -> None:
    """移动的横幅要说清「⌘Z 可以撤销」—— 用户敢用它全靠这一条。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "奈良", move=True)
    assert wait_until(qt_app, lambda: window._write_job is None)

    assert "⌘Z 可以撤销" in window._banner.text()


def test_a_move_can_actually_be_undone(qt_app: QApplication, window: MainWindow) -> None:
    """⌘Z 把它挪回原来的相册。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "奈良", move=True)
    assert wait_until(qt_app, lambda: window._write_job is None)

    window.undo_last_write()

    assert (window._library.root / "京都" / "一张.jpg").exists()
    assert not (window._library.root / "奈良" / "一张.jpg").exists()
    assert "已撤销" in window._banner.text()


def test_the_undo_label_mentions_the_move(qt_app: QApplication, window: MainWindow) -> None:
    """「撤销」那一项要说明撤的是「移动到相册」，用户按之前就知道会撤掉什么。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "奈良", move=True)
    assert wait_until(qt_app, lambda: window._write_job is None)

    window._refresh_undo_label()
    assert "移动到相册" in window._undo_action.text()


def test_picking_from_the_submenu_runs_the_move(qt_app: QApplication, window: MainWindow) -> None:
    """走完整那条链：右键 → 挑子菜单里那一项 → 真的搬过去了。"""
    _enter(qt_app, window, window._library.root / "京都")
    row = _select_photo(window, "一张.jpg")
    menu = window.context_menu_for(row)
    assert menu is not None

    _album_item(_submenu(menu, "移动到相册"), "奈良").trigger()

    assert wait_until(qt_app, lambda: window._write_job is None), "移动没有在时限内结束"
    assert (window._library.root / "奈良" / "一张.jpg").exists()
    assert not (window._library.root / "京都" / "一张.jpg").exists()


def test_moving_the_whole_selection(qt_app: QApplication, window: MainWindow) -> None:
    """多选就是整批一起搬 —— 这正是多选最想干的事。"""
    _enter(qt_app, window, window._library.root / "京都")
    assert window._model.rowCount() == 2
    _select_rows(window, 0, 1)

    window._transfer_selected(window._library.root / "奈良", move=True)

    assert wait_until(qt_app, lambda: window._write_job is None)
    assert sorted(p.name for p in (window._library.root / "奈良").iterdir()) == [
        "一张.jpg",
        "另一张.jpg",
    ]


def test_nothing_is_overwritten_in_the_target(qt_app: QApplication, window: MainWindow) -> None:
    """目标已有同名时改叫「一张 2.jpg」，**绝不覆盖**（访达的规矩）。"""
    from PIL import Image

    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")
    taken = window._library.root / "奈良" / "一张.jpg"
    Image.new("RGB", (16, 12), (7, 7, 7)).save(taken)
    before = taken.read_bytes()

    window._transfer_selected(window._library.root / "奈良", move=True)

    assert wait_until(qt_app, lambda: window._write_job is None)
    assert (window._library.root / "奈良" / "一张 2.jpg").exists()
    assert taken.read_bytes() == before, "目标里原有的那个文件不能被覆盖"
    assert "改名为「一张 2.jpg」" in window._banner.text()


def test_moving_into_the_album_it_is_already_in(qt_app: QApplication, window: MainWindow) -> None:
    """挑了它已经在的那个相册：什么都不做，如实说清为什么。"""
    _enter(qt_app, window, window._library.root / "京都")
    _select_photo(window, "一张.jpg")

    window._transfer_selected(window._library.root / "京都", move=True)

    assert wait_until(qt_app, lambda: window._write_job is None)
    assert (window._library.root / "京都" / "一张.jpg").exists()
    assert "本来就在「京都」里" in window._banner.text()


def test_a_group_card_moves_the_photos_inside(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片摊平成组员再搬（ADR 0020）：搬的是**照片**，不是它所在的目录。

    ADR 0019 决策 5 挡的是「搬相册」那一半（一次牵动整棵子树），摊平出来的一律是
    ``photos``，所以那半条依然成立。
    """
    photo = window._library.root / "京都" / "一张.jpg"
    group = GroupRef(
        facet="camera",
        key="canon",
        label="Canon",
        photos=(PhotoRef(path=photo, size=1, mtime_ns=1),),
    )
    window._model.reorder([group])
    qt_app.processEvents()
    _select_rows(window, 0)

    window._transfer_selected(window._library.root / "奈良", move=True)

    assert wait_until(qt_app, lambda: window._write_job is None)
    assert (window._library.root / "奈良" / "一张.jpg").exists()
    assert not photo.exists()


def test_paste_and_copy_do_not_run_at_the_same_time(
    qt_app: QApplication, window: MainWindow
) -> None:
    """三个写入类任务共用一道闸门：并发时后到的会把先到的盖掉（ADR 0019 决策 4）。"""
    started = threading.Event()
    release = threading.Event()
    real = Library.move_into

    def slow_move(self, target, sources, *, should_cancel=None):  # noqa: ANN001, ANN202
        started.set()
        release.wait(DEFAULT_TIMEOUT_SECONDS)
        return real(self, target, sources, should_cancel=should_cancel)

    monkey = Library.move_into
    Library.move_into = slow_move  # type: ignore[method-assign]
    try:
        _enter(qt_app, window, window._library.root / "京都")
        _select_photo(window, "一张.jpg")

        window._transfer_selected(window._library.root / "奈良", move=True)
        assert started.wait(DEFAULT_TIMEOUT_SECONDS), "第一次移动没有开始"
        window._transfer_selected(window._library.root / "奈良", move=False)

        assert window._banner.text() == "上一次操作还没结束。"
    finally:
        Library.move_into = monkey  # type: ignore[method-assign]
        release.set()

    assert wait_until(qt_app, lambda: window._write_job is None)


def test_config_directory_is_never_offered(tmp_path: Path) -> None:
    """``.gallery`` 绝不出现在相册清单里 —— 否则用户可能把照片挪进配置目录。

    那样照片就再也不会被扫描到（扫描跳过隐藏项），等于被 Gallery 自己弄丢了。
    """
    from PIL import Image

    library_root = tmp_path / "库"
    (library_root / "京都").mkdir(parents=True)
    Image.new("RGB", (4, 3), (1, 1, 1)).save(library_root / "京都" / "a.jpg")
    prepare_library(library_root)
    assert config_dir(library_root).is_dir()

    opened = Library.open(library_root)

    labels = [opened.album_label(f.path) for f in opened.albums()]
    assert labels == ["京都"]
    assert not any(CONFIG_DIR_NAME in label for label in labels), (
        f"配置目录名漏进了相册标签里：{labels}"
    )
