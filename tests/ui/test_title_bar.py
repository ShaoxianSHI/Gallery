"""窗口标题栏那一行：大图页显示照片文件名，``⌘R`` 改的就是它（ADR 0025）。

分三块：

1. **摆位置的算术**（:func:`title_bar.editor_rect`）—— 纯函数，单独测，不必造窗口。
2. **``TitleBar`` 自己**：显示、过长省略、编辑的提交与取消。
3. **窗口上的行为**：标题写什么、``⌘R`` 落在哪一格、改完名磁盘与标题栏是不是都变了。

第3 块里每条写操作都对着 ``tmp_path`` 里的副本，并且断言 journal 里记了一笔
（红线 4：任何写回都必须可回滚）。

**这一条只存在于 macOS**（ADR 0025 决策 2）：标题栏的高度来自安全区。所以下面凡是要
断言「大图页能改名」的用例，都先摆明 ``window._owns_title_bar`` —— 拿一个不成立的前提
去赌平台，失败时看不出是自己写错了还是平台不对。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS, wait_until  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.core.metadata.journal import ACTION_RENAME, read_records  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.title_bar import (  # noqa: E402
    HEIGHT,
    MAX_EDITOR_WIDTH,
    TRAFFIC_LIGHTS_WIDTH,
    TitleBar,
    editor_rect,
)

#: 测试库里的照片名。
PHOTO = "海边日落.jpg"


# ---- 1. 摆位置的算术 --------------------------------------------------------


def _area(width: int = 1200, height: int = HEIGHT) -> QRect:
    return QRect(0, 0, width, height)


def test_the_editor_sits_in_the_middle_of_the_title_bar(qt_app: QApplication) -> None:
    """系统标题本来就是居中的，输入框不居中就会在 ``⌘R`` 之后看得见那一下「跳」。"""
    area = _area()
    metrics = QFontMetrics(TitleBar().font())

    rect = editor_rect(area, PHOTO, metrics)

    assert rect.top() >= area.top() and rect.bottom() <= area.bottom()
    left_gap = rect.left() - area.left()
    right_gap = area.right() - rect.right()
    assert abs(left_gap - right_gap) <= 1, f"输入框没有居中：左 {left_gap}、右 {right_gap}"


def test_the_editor_never_covers_the_traffic_lights(qt_app: QApplication) -> None:
    """左上角那三个按钮画在最上层，输入框压上去就点不动了。"""
    metrics = QFontMetrics(TitleBar().font())

    rect = editor_rect(_area(width=300), "一张名字很长很长很长的照片.jpg", metrics)

    assert rect.left() >= TRAFFIC_LIGHTS_WIDTH
    assert rect.right() <= _area(width=300).right()


def test_a_wide_title_still_fits_inside_the_bar(qt_app: QApplication) -> None:
    """名字再长也不许横穿出标题栏：宽度有上限。"""
    area = _area()
    metrics = QFontMetrics(TitleBar().font())

    rect = editor_rect(area, "很长的名字" * 40, metrics)

    assert rect.width() <= min(MAX_EDITOR_WIDTH, area.width() - 2 * TRAFFIC_LIGHTS_WIDTH)


def test_the_editor_grows_with_the_name(qt_app: QApplication) -> None:
    """短名字不该配一个横穿半屏的空框 —— 显示与编辑之间要看不出换了控件。"""
    metrics = QFontMetrics(TitleBar().font())

    short = editor_rect(_area(), "a.jpg", metrics)
    long = editor_rect(_area(), "海边日落的那一张照片名字真的很长很长.jpg", metrics)

    assert long.width() > short.width()
    assert short.width() >= 260, "再短也得能打几个字"


# ---- 2. TitleBar 自己 ------------------------------------------------------


def _shown_bar(qt_app: QApplication, text: str) -> TitleBar:
    """一条**摆出来**的标题栏。

    子控件的 ``show()`` 在父控件还不可见时是白叫的 —— 所以控件级用例必须先把它摆出来，
    否则 ``is_editing()`` 恒为假，测的就不是「有没有进编辑」而是「父控件可不可见」。
    """
    bar = TitleBar()
    bar.resize(600, HEIGHT)
    bar.set_text(text)
    bar.show()
    qt_app.processEvents()
    return bar


def test_it_shows_the_name_it_was_given(qt_app: QApplication) -> None:
    bar = TitleBar()

    bar.set_text("2025 苏州")

    assert bar.current_text == "2025 苏州"
    assert not bar.is_editing()


def test_a_long_name_is_elided_but_stays_readable_in_the_tooltip(qt_app: QApplication) -> None:
    """省略的是那一行，全文挂在提示气泡上（与系统标题一个做法）。"""
    bar = TitleBar()
    bar.resize(400, HEIGHT)
    bar.set_text("很长的名字" * 20)

    assert bar.toolTip() == "很长的名字" * 20
    assert bar._label.text() != bar.toolTip(), "这么窄的标题栏不可能一个字都没省略"
    assert "…" in bar._label.text()


def test_editing_shows_the_name_with_the_extension_left_out(qt_app: QApplication) -> None:
    bar = _shown_bar(qt_app, PHOTO)

    bar.begin_edit()

    assert bar.is_editing() is True
    assert bar._editor.text() == PHOTO
    assert bar._editor.selectedText() == "海边日落", "扩展名不该被一起选中"


def test_return_hands_the_new_name_over(qt_app: QApplication) -> None:
    bar = _shown_bar(qt_app, PHOTO)
    committed: list[str] = []
    bar.rename_committed.connect(committed.append)

    bar.begin_edit()
    bar._editor.setText("礁石.jpg")
    QTest.keyClick(bar._editor, Qt.Key.Key_Escape)  # 先确认取消是另一条路
    assert committed == []

    bar.begin_edit()
    bar._editor.setText("礁石.jpg")
    QTest.keyClick(bar._editor, Qt.Key.Key_Return)

    assert committed == ["礁石.jpg"]
    assert bar.is_editing() is False


def test_escape_puts_the_half_typed_name_back(qt_app: QApplication) -> None:
    bar = _shown_bar(qt_app, PHOTO)
    events: list[str] = []
    bar.rename_committed.connect(lambda text: events.append(f"commit:{text}"))
    bar.rename_cancelled.connect(lambda: events.append("cancel"))

    bar.begin_edit()
    bar._editor.setText("敲了一半")
    QTest.keyClick(bar._editor, Qt.Key.Key_Escape)

    assert events == ["cancel"]
    assert bar.current_text == PHOTO, "取消之后标题栏不该留着敲了一半的字"


def test_only_the_editing_title_bar_takes_mouse_events(qt_app: QApplication) -> None:
    """不在编辑时对鼠标透明，那一段才仍然是拖窗口的地方（ADR 0025 决策 6）。

    反过来，编辑时必须摘掉 —— 那个标志会连子控件一起罩住，摘不掉的话输入框收不到点击。
    """
    bar = _shown_bar(qt_app, PHOTO)

    assert bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True

    bar.begin_edit()
    assert bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False

    bar.stop_edit()
    assert bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True


def test_nothing_to_rename_means_no_editor(qt_app: QApplication) -> None:
    """没有名字可改时（欢迎页）不进编辑，否则会凭空多出一个空输入框。"""
    bar = TitleBar()

    bar.begin_edit()

    assert bar.is_editing() is False


# ---- 3. 窗口上的行为 --------------------------------------------------------


def _settle(qt_app: QApplication, window: MainWindow) -> None:
    """等扫描、分组、地点那一遍都收工（那几个任务都在后台线程上）。"""
    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._scan_job is None and window._facet_job is None and window._place_job is None:
            qt_app.processEvents()
            return
        time.sleep(0.02)
    qt_app.processEvents()


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    """一个只有一张照片的库，停在库根（照片直接落在根下，走「全部照片」模式）。"""
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    make_photo(PHOTO, directory=root)
    prepare_library(root)
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_FLAT), base=tmp_path / "状态"
    )
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    instance.resize(900, 600)
    instance.show()
    _settle(qt_app, instance)
    assert instance._owns_title_bar, "这一条标题栏只在 macOS 上存在（ADR 0025 决策 2）"
    yield instance
    instance.close()


def _open_detail(qt_app: QApplication, window: MainWindow) -> int:
    """打开那张照片的大图，等预览落屏。"""
    row = window._model.row_of_path(window._library.root / PHOTO)
    assert row >= 0
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image), "照片没有在时限内显示出来"
    return row


def _editor(window: MainWindow):
    """标题栏里那个输入框（提交与取消都走它，见 ADR 0025 决策 5）。"""
    return window._title_bar._editor


def test_the_title_is_the_photo_name_while_looking_at_it(
    qt_app: QApplication, window: MainWindow
) -> None:
    """大图页是照片文件名，退回网格是相册名 —— 判据只有 :meth:`_refresh_title` 一处。"""
    assert window._title_bar.current_text == window._library.display_name

    _open_detail(qt_app, window)
    assert window._title_bar.current_text == PHOTO

    window.show_grid()
    qt_app.processEvents()
    assert window._title_bar.current_text == window._library.display_name


def test_the_title_follows_the_photo_you_step_to(qt_app: QApplication, window: MainWindow) -> None:
    """翻到下一张，标题栏跟着换 —— 那一行写的必须是正在看的那一张。"""
    second = window._library.root / "另一张.jpg"
    second.write_bytes(window._library.root.joinpath(PHOTO).read_bytes())
    window._reload_level()
    assert wait_until(qt_app, lambda: window._model.rowCount() == 2)

    _open_detail(qt_app, window)
    row = window._current_row
    other = window._neighbour_photo_row(row, 1)
    assert other is not None

    window.step_detail(1)
    qt_app.processEvents()

    assert window._title_bar.current_text == "另一张.jpg"


def test_rename_shortcut_edits_the_title_bar_in_the_detail_view(
    qt_app: QApplication, window: MainWindow
) -> None:
    """大图页的 ``⌘R`` 改的是标题栏那一行；网格那一侧一个字没动（仍在格子下面）。"""
    _open_detail(qt_app, window)
    window._update_action_states()
    assert window._rename_action.isEnabled() is True, "「重命名…」在大图页是亮着的（ADR 0025）"

    window.rename_current_entry()

    assert window._title_bar.is_editing() is True
    assert window._grid.is_renaming() is False, "网格那个输入框不该在这一页冒出来"
    assert window._rename_action.isEnabled() is False, "改到一半它自己让开：那时键盘归输入框"


def test_rename_shortcut_still_uses_the_grid_in_the_grid_view(
    qt_app: QApplication, window: MainWindow
) -> None:
    window._grid.select_row(0)
    qt_app.processEvents()

    window.rename_current_entry()

    assert window._grid.is_renaming() is True
    assert window._title_bar.is_editing() is False


def test_other_writes_yield_while_the_title_is_being_edited(
    qt_app: QApplication, window: MainWindow
) -> None:
    """改到一半键盘归输入框：⌘⌫ 不该把正在改名的照片删掉（网格那一侧一直如此）。"""
    _open_detail(qt_app, window)
    window.rename_current_entry()
    qt_app.processEvents()

    assert window._trash_action.isEnabled() is False
    assert window._reveal_action.isEnabled() is False

    QTest.keyClick(_editor(window), Qt.Key.Key_Escape)
    window._update_action_states()
    assert window._trash_action.isEnabled() is True


def test_return_in_the_title_bar_really_renames_the_file(
    qt_app: QApplication, window: MainWindow
) -> None:
    """端到端：磁盘上真的改名了、journal 里记了一笔、标题栏与横幅都跟着更新。"""
    picture = window._library.root / PHOTO
    _open_detail(qt_app, window)

    window.rename_current_entry()
    _editor(window).setText("礁石.jpg")
    QTest.keyClick(_editor(window), Qt.Key.Key_Return)
    qt_app.processEvents()

    assert (window._library.root / "礁石.jpg").exists()
    assert not picture.exists()
    assert window._title_bar.current_text == "礁石.jpg"
    assert window._model.entry_at(window._current_row).name == "礁石.jpg"
    assert "已重命名为" in window._banner.text()
    assert [record.action for record in read_records(window._library.root)] == [ACTION_RENAME]


def test_escape_in_the_title_bar_changes_nothing(qt_app: QApplication, window: MainWindow) -> None:
    """取消就是取消：磁盘没动、标题栏回到原来的名字，人还留在大图页。

    **留在大图页这一条是最容易做反的**：大图页的 Esc 平时是「退出」，所以改到一半按 Esc
    很可能顺手把人带回网格 —— 与网格那一侧「Esc 不跳上一层」是同一条边界（ADR 0025 决策 6）。
    """
    picture = window._library.root / PHOTO
    _open_detail(qt_app, window)

    window.rename_current_entry()
    _editor(window).setText("礁石.jpg")
    QTest.keyClick(_editor(window), Qt.Key.Key_Escape)
    qt_app.processEvents()

    assert picture.exists()
    assert not (window._library.root / "礁石.jpg").exists()
    assert window._title_bar.current_text == PHOTO
    assert window._stack.currentWidget() is window._detail_page, "Esc 取消了改名，不该顺手退回网格"
    assert read_records(window._library.root) == []


def test_an_illegal_name_leaves_the_file_alone_and_says_why(
    qt_app: QApplication, window: MainWindow
) -> None:
    """名字非法时绝不能动文件，标题栏也不能跟着显示一个没发生过的名字。"""
    picture = window._library.root / PHOTO
    _open_detail(qt_app, window)

    window.rename_current_entry()
    _editor(window).setText("礁石/日落.jpg")
    QTest.keyClick(_editor(window), Qt.Key.Key_Return)
    qt_app.processEvents()

    assert picture.exists()
    assert "不能带" in window._banner.text()
    assert window._title_bar.current_text == PHOTO


def test_undo_puts_both_the_name_and_the_title_back(
    qt_app: QApplication, window: MainWindow
) -> None:
    """⌘Z 撤回改名之后，标题栏跟着回到旧名 —— 用户盯着的就是这一行。"""
    picture = window._library.root / PHOTO
    _open_detail(qt_app, window)
    window.rename_current_entry()
    _editor(window).setText("礁石.jpg")
    QTest.keyClick(_editor(window), Qt.Key.Key_Return)
    qt_app.processEvents()

    window.undo_last_write()
    qt_app.processEvents()

    assert picture.exists()
    assert window._title_bar.current_text == PHOTO
    assert window._model.entry_at(window._current_row).name == PHOTO
    assert "已撤销" in window._banner.text()
