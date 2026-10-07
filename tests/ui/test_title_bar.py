"""窗口标题栏那一行：大图页显示照片文件名，``⌘R`` 改的就是它（ADR 0025）。

分三块：

1. **摆位置的算术**（:func:`title_bar.editor_rect`）—— 纯函数，单独测，不必造窗口。
2. **``TitleBar`` 自己**：显示、过长省略、编辑的提交与取消。
3. **窗口上的行为**：标题写什么、``⌘R`` 落在哪一格、改完名磁盘与标题栏是不是都变了。

另有一段落在最后：**两处的改名输入框必须是同一个外观**。用户在网格里改缩略图名时看到的
那个输入框，与标题栏里那一个必须是同一样东西（ADR 0025 决策 5）—— 圆角、底色、
高亮都只有一份。它们仍在这里而不是另开一个 ``test_rename_edit.py``，是因为这条断言要
的正是「两页看起来一样」，拆开就没有对象可断言了。

第 5 块是**那一段上还发生的另外两件事**：按住拖窗口、双击**铺满**窗口（ADR 0037）。
它们与改名一样归那一段管，所以也钉在这里 —— 而拖动与双击靠**移动距离**分家：
手没怎么动的那一下算双击，动得够远才算拖（理由写在 ``MainWindow.mouseMoveEvent`` 里，
症状是「双击没事、拖动却一点不跟手」）。

**「铺满」不是 macOS 的真全屏**，这两条要分清：铺满是窗口占满菜单栏与 Dock 之间的那一块，
两样都还在；真全屏是另一个 Space，菜单栏与 Dock 都收起来了。用户要的是前者，
所以 :func:`test_double_clicking_the_title_bar_fills_the_window_and_back` 两行都断言 ——
把动作换成 ``showFullScreen`` 时它必须变红。

第3 块里每条写操作都对着 ``tmp_path`` 里的副本，并且断言 journal 里记了一笔
（红线 4：任何写回都必须可回滚）。

**这一条只存在于 macOS**（ADR 0025 决策 2）：标题栏的高度来自安全区。所以下面凡是要
断言「大图页能改名」的用例，都先摆明 ``window._owns_title_bar`` —— 拿一个不成立的前提
去赌平台，失败时看不出是自己写错了还是平台不对。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics, QKeySequence, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.library.config import BROWSE_MODE_FLAT, prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.core.metadata.journal import ACTION_RENAME, read_records  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.rename_edit import RADIUS, RenameEdit  # noqa: E402
from gallery.ui.title_bar import (  # noqa: E402
    EXTRA_PADDING,
    HEIGHT,
    MAX_EDITOR_WIDTH,
    TRAFFIC_LIGHTS_WIDTH,
    TitleBar,
    editor_rect,
)
from gallery.ui.window.title_bar_flow import DRAG_THRESHOLD  # noqa: E402

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
    """等扫描、分组、地点那一遍都收工（那几个任务都在后台线程上）。

    判据与轮询循环都在 :mod:`waiting` 里（五个模块共用同一份，见那里的
    :func:`waiting.wait_for_idle`）—— 不成立**直接抛**，而不是超时也返回。
    """
    wait_for_idle(qt_app, window, what="扫描、分组与地点解析")


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    """一个只有一张照片的库，停在库根（照片直接落在根下，走「全部照片」模式）。"""
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    make_photo(PHOTO, directory=root)
    prepare_library(root)
    save_session(SessionState(recent_roots=(root,)), base=tmp_path / "状态")
    set_library_ui(root, browse_mode=BROWSE_MODE_FLAT)
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


# ---- 4. 两处的输入框是同一个外观 --------------------------------------------


def test_the_round_corners_and_the_highlight_live_on_the_shared_editor(
    qt_app: QApplication,
) -> None:
    """样式挂在 :class:`RenameEdit` 自己身上 —— 两处摆的是同一个控件。

    **高亮那一行最要紧**：它写死过一次（拿链接色当选区底色），深色下那一片比正文还沉，
    「主干已经选中了」反而看不出来。走调色板的 ``Highlight``，就永远与系统一致 ——
    网格那一处本来就是系统样式。
    """
    style = RenameEdit().styleSheet()

    assert f"border-radius: {RADIUS}px" in style, "圆角写在共享样式里，两处才一起圆"
    assert "palette(highlight)" in style, "选区底色走调色板，不要写死"
    assert "palette(highlighted-text)" in style, "选区里的字要走配对的那个颜色，否则读不出来"


def test_the_grid_editor_and_the_title_bar_one_look_the_same(
    qt_app: QApplication, window: MainWindow
) -> None:
    """网格里那个与大图页标题栏里那个，外观一模一样。

    这正是用户看到的那件事：缩略图上改名是大图页改名的同一样东西，不该一个方框一个圆角。
    """
    window._grid.select_row(0)
    qt_app.processEvents()
    assert window._grid.begin_rename(window._grid.currentIndex()) is True
    grid_style = window._grid._rename_edit.styleSheet()
    QTest.keyClick(window._grid._rename_edit, Qt.Key.Key_Escape)

    _open_detail(qt_app, window)
    window.rename_current_entry()

    assert isinstance(_editor(window), RenameEdit)
    assert _editor(window).styleSheet() == grid_style


# ---- 5. 那一段上还发生的另外两件事：拖窗口与全屏（ADR 0037）--------------------


def _top_point(window: MainWindow) -> QPoint:
    """窗口最顶上那一带的一个点：正中、离顶边几像素。

    **正中**是刻意的：它与左上角那三个按钮、右侧的胶囊与标题文字都离得开，
    按到的那一下只可能是「这一段本身」。
    """
    return QPoint(window.width() // 2, 4)


def _double_click(qt_app: QApplication, window: MainWindow, point: QPoint) -> None:
    QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qt_app.processEvents()


def _move_holding_left(
    qt_app: QApplication, window: MainWindow, point: QPoint, *, delta: QPoint
) -> None:
    """按住左键把鼠标挪 ``delta``（一个真正的 ``MouseMove``，带按键状态）。

    ``QTest.mouseMove`` 发出的移动**不带按键状态**，被测的那一路靠
    ``event.buttons() & LeftButton`` 认「是不是还按着」—— 用它会空转绿。
    """
    target = point + delta
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(target),
        QPointF(window.mapToGlobal(target)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window, event)
    qt_app.processEvents()


def test_a_press_on_the_title_bar_starts_a_drag_only_after_the_mouse_moves_far_enough(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拖动与双击靠**移动距离**分家（理由见 :meth:`MainWindow.mouseMoveEvent`）。

    **按下那一刻还不能发起拖动**：那一下可能只是双击的第一下。手抖（移动不到
    :data:`DRAG_THRESHOLD`）也不算拖动，否则双击会被吃掉一半。

    **这条线必须比拖放那条（``startDragDistance``，10 点）小得多**：拖动要立刻跟手，
    拿 10 点当线的话，窗口要先等光标走满 10 点才动 —— 读起来就是「拖之前得先动一会儿」。
    """
    assert QApplication.startDragDistance() > DRAG_THRESHOLD, (
        "窗口拖动的分界线又变回拖放那条了，窗口会先等光标走满一大截才跟手"
    )

    started: list[int] = []
    monkeypatch.setattr(MainWindow, "_start_title_bar_drag", lambda self: started.append(1))

    point = _top_point(window)
    assert window._is_title_bar(QPointF(point)), "前提不成立：这一点压根不在标题栏上"

    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qt_app.processEvents()
    assert window._title_bar_drag_armed is True
    assert started == [], "刚按下就发起拖动，双击就没有机会了"

    _move_holding_left(qt_app, window, point, delta=QPoint(DRAG_THRESHOLD - 1, 0))
    assert started == [], "手抖不该被当成拖动"

    _move_holding_left(qt_app, window, point, delta=QPoint(40, 0))
    assert started == [1], "按住并动得够远之后没有发起窗口拖动"

    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qt_app.processEvents()
    assert window._title_bar_drag_armed is False


def test_a_click_on_the_title_bar_that_is_not_held_does_nothing(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点一下（不按住）什么也不该发生 —— 拖窗口得按住并动起来。

    顺带钉住「松手就作废」：这一闸没作废的话，一次普通单击会在之后莫名的某一次移动里
    发起窗口拖动，而用户早就松开手了。
    """
    started: list[int] = []
    monkeypatch.setattr(MainWindow, "_start_title_bar_drag", lambda self: started.append(1))

    point = _top_point(window)

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qt_app.processEvents()

    assert window._title_bar_drag_armed is False
    assert started == []
    assert window.isMaximized() is False

    # 松手之后即使鼠标再动，也不该发起拖动（那一下已经不作数了）。
    _move_holding_left(qt_app, window, point, delta=QPoint(40, 0))
    assert started == []


def test_double_clicking_the_title_bar_fills_the_window_and_back(
    qt_app: QApplication, window: MainWindow
) -> None:
    """双击标题栏**铺满**，再双击一次还原（ADR 0037）。

    **「铺满」不是 macOS 的真全屏，这是两件事**：铺满是窗口占满菜单栏与 Dock 之间的
    那一块，**菜单栏与 Dock 都还在**，也不会另开一个 Space（真全屏是另一个 Space，
    菜单栏与 Dock 都收起来 —— 做成后者就是答非所问）。

    **两行断言缺一不可**：只钉 ``isMaximized()`` 的话，把动作换成
    ``showFullScreen`` 这条照样绿。

    **来来回回都要能**：铺满只是窗口变大，标题栏那一段照旧在，于是第二次双击仍然
    落在标题栏上（判据是 ``isFullScreen``，不是 ``isMaximized``，见
    :meth:`MainWindow._is_title_bar`）。少了这一半就是「进得去、出不来」。
    """
    assert window.isMaximized() is False
    assert window.isFullScreen() is False

    _double_click(qt_app, window, _top_point(window))

    assert window.isMaximized() is True, "双击标题栏没有铺满"
    assert window.isFullScreen() is False, "铺满不该是 macOS 的真全屏（菜单栏与 Dock 得留着）"

    _double_click(qt_app, window, _top_point(window))

    assert window.isMaximized() is False, "再双击一次没有还原"


def test_filling_the_window_leaves_the_title_bar_where_it_is(
    qt_app: QApplication, window: MainWindow
) -> None:
    """铺满时标题栏、胶囊、外部按钮与那 12 点空档**全都留在原处**。

    **它们只在真全屏里才收起来**（那条路径见下一块的用例）—— 铺满只是窗口变大，
    把它们顺手收掉得到的是「照片铺满了，但顶上什么都没有」，而不是「窗口铺满了」。
    """
    assert window._title_bar.isVisible() is True
    assert window._search_field.isVisible() is True
    assert window._external_button.isVisible() is True

    _double_click(qt_app, window, _top_point(window))

    assert window._title_bar.isVisible() is True, "铺满之后标题栏不见了"
    assert window._search_field.isVisible() is True, "铺满之后搜索胶囊不见了"
    assert window._external_button.isVisible() is True, "铺满之后外部按钮不见了"
    assert window._stack.contentsMargins().top() == EXTRA_PADDING


def test_double_clicking_the_content_does_not_fill_the_window(
    qt_app: QApplication, window: MainWindow
) -> None:
    """内容区里的双击是网格自己的那一件事，不该顺手把窗口铺满。"""
    _double_click(qt_app, window, QPoint(window.width() // 2, window.height() // 2))

    assert window.isMaximized() is False


def test_the_fill_key_says_which_way_it_will_go(qt_app: QApplication, window: MainWindow) -> None:
    """⌃⌘F 是系统给铺满的那个键，菜单上那句话得跟着状态走。

    **⌃ 在 Qt 里写作 ``Meta``**（⌘ 才写作 ``Ctrl``）：写成 ``Ctrl+Ctrl+F`` 的话，
    Qt 会把它并成 ⌘F —— 于是铺满与「找」撞在同一个键上。

    **判据是 ``isMaximized`` 而不是 ``isFullScreen``**：菜单上这一条说的就是铺满，
    与绿按钮悬停菜单里的「进入全屏幕」不是同一件事。
    """
    assert window._fill_action.shortcut() == QKeySequence("Ctrl+Meta+F")
    assert window._fill_action.text() == "铺满窗口"

    window._fill_action.trigger()
    qt_app.processEvents()

    assert window.isMaximized() is True
    assert window._fill_action.text() == "还原窗口"

    window._fill_action.trigger()
    qt_app.processEvents()

    assert window.isMaximized() is False
    assert window._fill_action.text() == "铺满窗口"


def test_a_real_full_screen_stepped_aside_is_still_handled(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**真全屏**（绿按钮悬停菜单里的「进入全屏幕」）那一支仍然要成立。

    我们的动作不再进真全屏，但**用户能自己进**（系统设置里把绿按钮设成
    「进入全屏幕」）—— 那时标题栏、胶囊与外部按钮必须收起来，否则盖在照片上面
    （ADR 0025 决策 2）；内容区那 12 点空档也要还回去，不还回去照片区顶上就压着
    一条什么都没有的白边。

    **「回头那三行只由 :meth:`MainWindow.changeEvent` 撑着**：退出真全屏本身带着一次
    几何变化（``resizeEvent`` 会把标题栏与胶囊摆回来），但外部按钮是全屏那一支
    **藏起来**的，重摆只会「摆一个已经看不见的」。摘掉 ``changeEvent`` 时这一条变红。
    """
    window.showFullScreen()
    qt_app.processEvents()

    assert window._title_bar.isVisible() is False
    assert window._search_field.isVisible() is False
    assert window._external_button.isVisible() is False
    assert window._stack.contentsMargins().top() == 0

    window.showNormal()
    qt_app.processEvents()

    assert window._title_bar.isVisible() is True
    assert window._search_field.isVisible() is True
    assert window._external_button.isVisible() is True, (
        "退出真全屏之后这个按钮自己回来了吗（它是全屏那一支藏起来的）"
    )
    assert window._stack.contentsMargins().top() == EXTRA_PADDING


def test_in_a_real_full_screen_the_top_of_the_window_is_no_longer_a_title_bar(
    qt_app: QApplication, window: MainWindow
) -> None:
    """真全屏里那一段已经整个收起来了，它不是「标题栏」。

    留着这一条是为了不给将来的人留一条走不通的路：真全屏里退出得走绿按钮或 ⌃⌘F，
    而 ``_is_title_bar`` 若把那一块也算成标题栏，双击就会在退出那一侧做出一个
    「进得去出不来」的状态。
    """
    window.showFullScreen()
    qt_app.processEvents()

    assert window._is_title_bar(QPointF(_top_point(window))) is False


def test_the_drag_and_the_double_click_step_aside_while_a_name_is_being_edited(
    qt_app: QApplication, window: MainWindow
) -> None:
    """改到一半那一段上摆着输入框：拖走它等于把正在改的名字甩到屏幕外，
    铺满则会让它当场收起来。

    与 ADR 0025 决策 6 同一类让位（那时键盘归输入框，现在鼠标也归它）。
    """
    window._title_bar.set_text(PHOTO)
    window._title_bar.begin_edit()
    qt_app.processEvents()

    _double_click(qt_app, window, _top_point(window))

    assert window.isMaximized() is False
    assert window._title_bar.is_editing() is True
