"""大图页编辑的端到端测试（ADR 0027）。

覆盖用户真正会走的那条路：**转一下 → 看得见 → ⌘Z 撤回 → 再转 → ⌘S 存成新文件**，
以及几处「原文件不能动」的红线断言。

照片都是 ``tmp_path`` 里当场造的，exiftool 缺席时降级那一档也一并跑过 ——
写操作的那一半不依赖本机有没有装 exiftool。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.core.media.edit import Rotate, orientation_of  # noqa: E402
from gallery.core.metadata.journal import ACTION_EXPORT, read_records  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

NAMES = ("一张.jpg", "两张.jpg")

#: 旋转那些用例用的小图（60×30：预览不会缩它，于是显示尺寸就是原尺寸，好断言）。
SMALL = (60, 30)
#: 裁剪那些用例要用的图 —— **必须够大**：裁剪有两道下限（最短边 320、总像素 40 万），
#: 而它们是产品行为，不该为了测试而放松。一张 60×30 的图裁多少次都会被挡下来。
LARGE = (1600, 1200)


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    """一个「一个相册 + 两张照片」的库（小图）。"""
    yield from _library(qt_app, tmp_path, make_photo, SMALL)


@pytest.fixture
def big_window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    """同上，但照片够大，裁剪那几道下限拦不住它。"""
    yield from _library(qt_app, tmp_path, make_photo, LARGE)


def _library(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    size: tuple[int, int],
) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    album = root / "京都"
    album.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, size=size, directory=album)
    prepare_library(root)
    save_session(SessionState(recent_roots=(root,)), base=tmp_path / "状态")
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_until(qt_app, lambda: instance._library is not None)
    yield instance
    # 关窗会问「有未保存的编辑」（ADR 0027 决策 10），而那是模态框 —— 用例结束时
    # 若还留着草稿，测试进程会一直等一个没有人来点的按钮。那一条另有专用的用例，
    # 它把 ``QMessageBox.exec`` 换掉再走一遍。
    instance._drafts.clear_all()
    instance.close()


def _enter(qt_app: QApplication, window: MainWindow) -> int:
    """进相册并打开第一张的大图，等画面落屏，返回它的行号。"""
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    window.enter_folder(window._library.root / "京都")
    wait_until(qt_app, lambda: window._model.rowCount() > 0)
    row = window._model.row_of_path(window._library.root / "京都" / NAMES[0])
    assert row >= 0
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image), "照片没有在时限内显示出来"
    return row


def _path(window: MainWindow, name: str = NAMES[0]) -> Path:
    """库里那张照片的路径（按 ``window._library.root`` 拼，解析过的那一份）。"""
    return window._library.root / "京都" / name


# ---- 图标组 ----------------------------------------------------------------------


def test_the_icons_show_up_on_the_way_into_the_detail_view(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**不许手动摆位** —— 双击一张照片既不显示窗口也不改它的大小。

    摆位曾经只挂在 ``showEvent`` / ``resizeEvent`` 上，于是打开大图之后那四枚图标一直是
    藏着的；而测试里顺手调了一次 ``_layout_title_bar()``，于是测试全绿、界面上什么都没有。
    这一条刻意不碰那个方法：它量的是**真实那一步之后**的状态。
    """
    assert not window._edit_toolbar.isVisible()
    _enter(qt_app, window)
    qt_app.processEvents()
    assert window._edit_toolbar.isVisible()
    assert all(button.isEnabled() for button in window._edit_toolbar.buttons)


def test_the_icons_follow_the_photo_and_leave_with_the_page(
    qt_app: QApplication, window: MainWindow
) -> None:
    """翻图时跟着走（位置要重摆：文件名长度变了），退回网格就收起来。"""
    _enter(qt_app, window)
    assert window._edit_toolbar.isVisible()
    window.step_detail(1)
    assert wait_until(qt_app, lambda: window._detail.has_image)
    assert window._edit_toolbar.isVisible()

    window.show_grid()
    qt_app.processEvents()
    assert window._edit_toolbar.isVisible() is False


def test_the_title_bar_height_is_zero_when_it_is_not_ours(
    qt_app: QApplication, window: MainWindow
) -> None:
    """标题栏不归我们（Linux）或全屏时高度是 0 —— 摆位全靠它算，两处都要答得出来。"""
    assert window._owns_title_bar or window._title_bar_height() == 0
    assert window._title_bar_height() > 0


def test_the_filename_moves_to_the_left_in_the_detail_view(
    qt_app: QApplication, window: MainWindow
) -> None:
    """大图页靠左、网格页居中（ADR 0027 决策 7）。判据只有「现在在哪一页」。"""
    window._stack.setCurrentWidget(window._grid_page)
    window._refresh_title()
    assert window._title_bar.aligned_left is False

    _enter(qt_app, window)
    assert window._title_bar.aligned_left is True


def test_the_icons_sit_beside_the_name_not_on_top_of_it(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**图标跟在文件名右边**（ADR 0027 决策 7），且中间留着间距。"""
    from gallery.ui.title_bar import TEXT_TO_ICONS_GAP

    _enter(qt_app, window)
    qt_app.processEvents()
    name_right = window._title_bar.text_right()
    assert window._edit_toolbar.x() == name_right + TEXT_TO_ICONS_GAP
    assert window._edit_toolbar.y() + window._edit_toolbar.height() > 0, "在标题栏那一行上"


# ---- 编辑 ----------------------------------------------------------------------


def test_rotating_changes_the_picture_and_leaves_the_file_alone(
    qt_app: QApplication, window: MainWindow
) -> None:
    """转一下立刻看得见，而**磁盘上那个文件一个字节都没动**（红线 2）。"""
    _enter(qt_app, window)
    path = _path(window)
    before = path.read_bytes()

    window._apply_edit_step(Rotate(90))
    assert wait_until(qt_app, lambda: window._detail.image_size == (30, 60))

    assert path.read_bytes() == before
    assert window._drafts.has(path)


def test_undo_puts_the_picture_back(qt_app: QApplication, window: MainWindow) -> None:
    """⌘Z 撤的是**编辑**，而且撤得干净（不是把它存成别的样子）。"""
    _enter(qt_app, window)
    path = _path(window)
    window._apply_edit_step(Rotate(90))
    assert wait_until(qt_app, lambda: window._detail.image_size == (30, 60))

    window.undo_last_write()
    assert window._drafts.has(path) is False
    assert wait_until(qt_app, lambda: window._detail.image_size == (60, 30))


def test_undo_names_the_step_it_is_undoing(qt_app: QApplication, window: MainWindow) -> None:
    """菜单上写着「撤销 左转 90°」—— 用户按之前就知道会撤掉什么。"""
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    assert window._undo_action.text() == "撤销 左转 90°"
    window.undo_last_write()
    assert window._undo_action.text() == "撤销"


def test_edits_are_not_available_on_the_grid(qt_app: QApplication, window: MainWindow) -> None:
    """网格页那一层是多选，而编辑还没有批量那一套（ADR 0027 决策 3）。"""
    window._update_action_states()
    assert window._rotate_left_action.isEnabled() is False
    assert window._crop_action.isEnabled() is False
    assert window._save_edit_action.isEnabled() is False


def test_the_unsaved_mark_appears_and_disappears(qt_app: QApplication, window: MainWindow) -> None:
    _enter(qt_app, window)
    window._refresh_title()
    assert window._title_bar._marker.isVisible() is False

    window._apply_edit_step(Rotate(90))
    window._refresh_title()
    assert window._title_bar._marker.isVisible() is True

    window.undo_last_write()
    window._refresh_title()
    assert window._title_bar._marker.isVisible() is False


# ---- 裁剪 ------------------------------------------------------------------------


def _press(qt_app: QApplication, window: MainWindow, x: float, y: float) -> None:
    """在裁剪浮层上按一下（发真的鼠标事件，走的是控件自己的那一套坐标换算）。"""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    overlay = window._crop_overlay
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPoint(0, 0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qt_app.sendEvent(overlay, event)


def test_crop_mode_opens_over_the_photo(qt_app: QApplication, big_window: MainWindow) -> None:
    window = big_window
    _enter(qt_app, window)
    window.begin_crop()
    assert window._crop_overlay.isVisible()
    assert window._drafts.has(_path(window)) is False, "进裁剪模式还没改任何东西 —— 回车才改"


def test_pressing_on_the_overlay_does_not_blow_up(
    qt_app: QApplication, big_window: MainWindow
) -> None:
    """按一下浮层要走完整的命中判定 —— 那条路上曾把纯函数的参数摊开调错过，
    而几何用例只喂纯函数，抓不到。"""
    window = big_window
    _enter(qt_app, window)
    window.begin_crop()
    _press(qt_app, window, 200.0, 200.0)


def test_escape_leaves_the_draft_alone(qt_app: QApplication, big_window: MainWindow) -> None:
    window = big_window
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    window.begin_crop()
    window.cancel_crop()
    assert window._crop_overlay.isVisible() is False
    assert window._drafts.steps(_path(window)) == (Rotate(90),)


def test_confirming_a_crop_records_it_even_though_the_overlay_is_still_up(
    qt_app: QApplication, big_window: MainWindow
) -> None:
    window = big_window
    """**回车那一刻浮层还没收起**（信号先发、再 ``finish``），于是这一档不能用
    「裁一半不算可编辑」那个判据 —— 否则用户回车之后什么也没发生。"""
    _enter(qt_app, window)
    window.begin_crop()
    window._on_crop_committed(0.0, 0.0, 1200.0, 900.0)
    assert window._drafts.steps(window._library.root / "京都" / NAMES[0])


def test_a_crop_that_is_too_small_is_refused(qt_app: QApplication, big_window: MainWindow) -> None:
    """裁成一条 3 像素的缝要挡下来，并说清楚 —— 存下去的话副本基本等于没有。"""
    window = big_window
    _enter(qt_app, window)
    window.begin_crop()
    window._on_crop_committed(0.0, 0.0, 1.0, 1.0)
    assert window._drafts.steps(_path(window)) == ()
    assert window._crop_overlay.isVisible() is False


def test_editing_commands_stand_down_while_cropping(
    qt_app: QApplication, big_window: MainWindow
) -> None:
    """裁一半时不能再转 —— 那个框是按「已经叠加了草稿」的坐标系定的。"""
    window = big_window
    _enter(qt_app, window)
    window.begin_crop()
    window._update_action_states()
    assert window._rotate_left_action.isEnabled() is False
    assert window._crop_action.isEnabled() is False


# ---- 另存副本 --------------------------------------------------------------------


def test_saving_writes_a_new_file_and_never_touches_the_original(
    qt_app: QApplication, window: MainWindow
) -> None:
    """⌘S 的核心承诺：原文件原封不动，旁边多出一份 ``-edited``。"""
    _enter(qt_app, window)
    path = _path(window)
    before = path.read_bytes()
    window._apply_edit_step(Rotate(90))
    assert wait_until(qt_app, lambda: window._detail.image_size == (30, 60))

    window.save_edited_draft()

    copy = path.with_name("一张-edited.jpg")
    assert copy.is_file()
    assert path.read_bytes() == before
    assert window._model.row_of_path(copy) >= 0, "新文件要立刻出现在网格里"
    assert window._drafts.has(path) is False, "存完这一张就没有草稿了"
    assert [r.action for r in read_records(window._library.root) if r.action == ACTION_EXPORT]


def test_saving_nothing_says_so_instead_of_writing_a_copy(
    qt_app: QApplication, window: MainWindow
) -> None:
    """转了 90° 又转回来：屏幕上没变化，就不该凭空多出一份文件。"""
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    window._apply_edit_step(Rotate(270))

    window.save_edited_draft()

    assert list((window._library.root / "京都").glob("*-edited*")) == []


def test_the_saved_copy_shows_the_edit(qt_app: QApplication, window: MainWindow) -> None:
    """存出来的那份**本身就是转过的**：方向记在标签里，别的软件也认。"""
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    window.save_edited_draft()

    copy = _path(window, "一张-edited.jpg")
    assert orientation_of(Image.open(copy)) == 8
    assert Image.open(copy).size == (60, 30), "像素没动，只是标签说该转"


def test_closing_with_an_unsaved_edit_asks_before_throwing_it_away(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**关窗要问一句。** 草稿只在内存里，而横幅会被下一次操作顶掉 ——
    用户丢掉的可能是十分钟的活（ADR 0027 决策 10）。
    """
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    asked: list[int] = []

    def _discard(self: QMessageBox) -> int:
        asked.append(1)
        return int(QMessageBox.StandardButton.Discard)

    monkeypatch.setattr(QMessageBox, "exec", _discard)

    assert window._confirm_discard_drafts() is True
    assert asked, "有草稿却没问，用户就这样被当成放弃了"


def test_closing_without_drafts_asks_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enter(qt_app, window)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("没有草稿却弹了确认框"))

    assert window._confirm_discard_drafts() is True


def test_choosing_save_stores_it_before_letting_the_window_close(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """答「保存」就先真存一份，存完才放行 —— 替他做放弃的决定不行。"""
    _enter(qt_app, window)
    window._apply_edit_step(Rotate(90))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: int(QMessageBox.StandardButton.Save))

    assert window._confirm_discard_drafts() is True
    assert _path(window, "一张-edited.jpg").is_file()


def test_the_menu_offers_the_same_actions_as_the_icons(
    qt_app: QApplication, window: MainWindow
) -> None:
    """菜单与图标是**同一批 QAction** —— 两处不可能对不上号（ADR 0027 决策 8）。"""
    _enter(qt_app, window)
    actions = window._menu_actions(None)
    assert actions.edit is not None
    assert actions.edit.rotate_left is window._rotate_left_action
    assert actions.edit.save is window._save_edit_action


def test_edits_are_not_offered_on_the_grid_menu(qt_app: QApplication, window: MainWindow) -> None:
    window._stack.setCurrentWidget(window._grid_page)
    assert window._menu_actions(None).edit is None
