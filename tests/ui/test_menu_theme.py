"""菜单面板的底色必须跟窗口是同一块（README 设计原则第 6 条）。

背景：统一底色是在 ``_build_ui`` 里下发的，而菜单是**之后**才建出来的 ——
菜单栏里那几个在 ``_build_actions`` 里建，右键菜单更晚，按一次才建。它们因此一直用
系统菜单色（浅色 ``#ececec`` / 深色 ``#2b2b2b``），与窗口的纯色底差一档：按一下菜单
的那一刻，界面就从「一整块底色」掉出去了。

这里钉住两件事：菜单栏里的菜单（含子菜单）与右键菜单都拿到窗口那份调色板，且系统
换浅色 / 深色时菜单栏里的菜单会跟着一起变。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QColor,
    QFontMetrics,
    QKeySequence,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import BROWSE_MODE_ALBUMS  # noqa: E402
from gallery.ui import menus, theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.theme import rgba  # noqa: E402

#: 「假装系统换成了深色」用的底色。真深色下 Qt 给的是别的值，这里要的是
#: 「换一个和当前不同的值」，具体是哪个数与这条测试无关。
FLIPPED_BACKGROUND = QColor(23, 23, 23)

#: 等待扫描完成的时限。
SCAN_TIMEOUT_SECONDS = 10.0


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(root / "一张.jpg")
    # 还要有一个子文件夹：没有它就取不到「相册」那一种落点（用户正是看着相册菜单说
    # 「在访达中显示」和 ⇧ 几乎同一条 x 的）。
    album = root / "京都"
    album.mkdir()
    Image.new("RGB", (64, 48), (10, 160, 90)).save(album / "另一张.jpg")
    prepare_library(root)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    _wait_for_scan(qt_app, instance)
    yield instance
    instance.close()


def _wait_for_scan(qt_app: QApplication, window: MainWindow) -> None:
    """转到扫描结束：网格里一个格子都没有时，右键菜单那几条无从断言。"""
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._scan_job is None and window._model.rowCount() > 0:
            return
        time.sleep(0.01)
    qt_app.processEvents()


def _bar_menu(window: MainWindow, title: str) -> QMenu:
    for action in window.menuBar().actions():
        if action.text() == title:
            assert action.menu() is not None
            return action.menu()
    raise AssertionError(f"菜单栏里没有「{title}」")


def _submenu(parent: QMenu, title: str) -> QMenu:
    for action in parent.actions():
        if action.text() == title:
            assert action.menu() is not None
            return action.menu()
    raise AssertionError(f"「{parent.title()}」里没有子菜单「{title}」")


def _background(menu: QMenu) -> str:
    return menu.palette().color(QPalette.ColorRole.Window).name()


def test_menu_bar_menus_share_window_background(window: MainWindow) -> None:
    """菜单栏里的菜单与它们的子菜单，都得是窗口那块底色。"""
    window_background = window.palette().color(QPalette.ColorRole.Window).name()

    menus = (
        _bar_menu(window, "文件"),
        _bar_menu(window, "视图"),
        _submenu(_bar_menu(window, "文件"), "最近打开"),
    )
    for menu in menus:
        assert _background(menu) == window_background


def test_context_menu_shares_window_background(qt_app: QApplication, window: MainWindow) -> None:
    """右键菜单是按下那一刻才建的，拿不到任何一次主题下发，得自己照抄一份。"""
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    assert _background(menu) == window.palette().color(QPalette.ColorRole.Window).name()


def test_context_menu_carries_our_look(qt_app: QApplication, window: MainWindow) -> None:
    """圆角与行距在样式表里，而**底色必须一起写进去**。

    这是实测出来的坑：给 ``QMenu`` 加了样式表之后，面板就不再由 macOS 样式画，
    底色会掉回系统菜单色（浅色 ``#ececec``）。样式表里若不写底色，上一条测试
    断言的「与窗口同色」就会当场作废 —— 两条必须一起成立。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    sheet = menu.styleSheet()
    assert f"border-radius: {menus.MENU_CORNER_RADIUS}px" in sheet
    assert f"padding: {menus.MENU_INSET}px" in sheet
    assert menus.MENU_ITEM_PADDING in sheet
    assert rgba(window.palette().color(QPalette.ColorRole.Window)) in sheet


def test_context_menu_has_hover_highlight(qt_app: QApplication, window: MainWindow) -> None:
    """菜单必须有悬停 / 当前项的高亮带。

    实测过的坑：只要给 ``QMenu`` 挂了样式表，系统样式那份高亮就**不再画**了 ——
    原生菜单按方向键会把当前行点亮成强调色，加了样式表之后按 ↓ 两次所有行仍是底色，
    用户根本看不出鼠标在哪一行。所以这两条规则是补回来的，不能删。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    sheet = menu.styleSheet()
    highlight = rgba(menu.palette().color(QPalette.ColorRole.Highlight))
    assert "QMenu::item:hover" in sheet
    assert "QMenu::item:selected" in sheet
    assert sheet.count(highlight) >= 2, "两条规则都得用调色板里的高亮色"
    # 高亮带是内缩的圆角带。**带状态的规则里不许写 margin**：margin 属于盒子模型，
    # 写在 :selected 里等于每悬停一次就换一次盒子，文字会跟着挪位置（用户实测）。内缩
    # 由菜单自身的内边距决定，与状态无关。
    assert f"border-radius: {menus.MENU_HIGHLIGHT_RADIUS}px" in sheet
    # 只查那两条带状态的规则，不查整份：分隔线那条带 margin 是故意要的（那里没有
    # 状态，见 ``ui.menus`` 里 MENU_SEPARATOR_HEIGHT 那段）。
    assert "margin" not in _state_rules(sheet)
    # 内缩的那一处在菜单上，**四边同值**：只留左右的话，高亮带会顶着面板上下两条边
    assert f"padding: {menus.MENU_INSET}px" in sheet


def _state_rules(sheet: str) -> str:
    """样式表里那两条**带状态**的规则（悬停 / 当前项）。

    拆出来单独断言，而不是对整份样式表搜 ``margin``：分隔线那条规则带 margin 是
    故意要的（见 ``ui.menus``），而「高亮带不许有 margin」这条禁令针对的是
    **会随悬停换盒子**的那两条。
    """
    return "\n".join(line for line in sheet.splitlines() if ":hover" in line or ":selected" in line)


def test_separator_color_follows_the_theme() -> None:
    """分隔线的颜色深浅两套：深色下要**提亮到看得见**，浅色下维持原来的浓度。

    深色下那条线原本由系统样式画，取的是**应用调色板**里偏暗的一档（实测 ``#0f0f0f``），
    比底色 ``#171717`` 还深，等于没画。浅色下它本来是 ``#aaaaaa``、看得见，所以那个值
    照抄（实测合成出来是 ``#ababab``，差一档，看不出来）—— 不顺手改浅色的观感。
    """
    dark = QColor("#171717")
    light = QColor("#ffffff")

    dark_line = _line_on(dark, menus.separator_color(dark, QColor("#ffffff")))
    light_line = _line_on(light, menus.separator_color(light, QColor("#000000")))

    # 深色：至少要提亮一档（32/255），再低就回到「那条线不存在」的状态
    assert dark_line.value() - dark.value() >= 32, f"深色下那条线还是太暗：{dark_line.name()}"
    # 浅色：与系统原来那条（#aaaaaa）基本一致
    assert abs(light_line.value() - 0xAA) <= 2, f"浅色下改过头了：{light_line.name()}"


def _line_on(background: QColor, rule_color: str) -> QColor:
    """把样式表里那个 ``rgba(...)`` 压到实底色上，算出真正画出来的那一档颜色。"""
    channels = rule_color[rule_color.index("(") + 1 : rule_color.index(")")].split(",")
    red, green, blue = (int(float(channel)) for channel in channels[:3])
    alpha = float(channels[3])
    return QColor(
        round(red * alpha + background.red() * (1 - alpha)),
        round(green * alpha + background.green() * (1 - alpha)),
        round(blue * alpha + background.blue() * (1 - alpha)),
    )


def test_dark_separator_is_a_line_you_can_see(qt_app: QApplication, window: MainWindow) -> None:
    """深色下右键菜单里的分隔线必须**看得见，而且是线**（用户报的就是它基本看不见）。

    **真的抓一张图来看**，而不是只读样式表：只读样式表的话，将来谁把 ``height``
    删掉测试还是绿的 —— 而那一栏 12 像素会整块填上，看着像一条灰色的带子（见
    ``ui.menus`` 里 MENU_SEPARATOR_HEIGHT 那段）。
    """
    original = QPalette(qt_app.palette())
    background = QColor("#171717")
    try:
        flipped = QPalette(original)
        flipped.setColor(QPalette.ColorRole.Base, background)
        flipped.setColor(QPalette.ColorRole.Window, background)
        flipped.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
        qt_app.setPalette(flipped)
        theme.apply_theme(qt_app)

        window._stack.setCurrentWidget(window._grid_page)
        row = window._model.row_of_path(window._library.root / "一张.jpg")
        assert row >= 0
        menu = window.context_menu_for(row)

        assert menu is not None
        menu.popup(QPoint(60, 60))
        try:
            color, thickness = _painted_separator(menu, background)
        finally:
            menu.close()
    finally:
        qt_app.setPalette(original)
        theme.apply_theme(qt_app)

    assert thickness == menus.MENU_SEPARATOR_HEIGHT, f"分隔线厚了：{thickness} 像素"
    assert color.value() - background.value() >= 32, f"深色下那条线还是太暗：{color.name()}"


def _painted_separator(menu: QMenu, background: QColor) -> tuple[QColor, int]:
    """菜单中间那一列上，分隔线真正画出来的那一段：颜色与厚度（像素）。

    厚度数的是「不是底色的行数」：只看颜色的话，上面那两种坏法（整块填上、根本没画）
    都会漏过去。取中间一列是为了躲开两端的圆角。
    """
    separator = next(action for action in menu.actions() if action.isSeparator())
    rect = menu.actionGeometry(separator)
    image = menu.grab().toImage()
    # 抓图按设备像素给（Retina 上是两倍），几何却是逻辑像素，两边要按倍率对起来。
    scale = image.devicePixelRatio()
    x = image.width() // 2
    top, bottom = round(rect.top() * scale), round(rect.bottom() * scale)
    painted = [
        y
        for y in range(top, min(bottom + 1, image.height()))
        if image.pixelColor(x, y).rgb() != background.rgb()
    ]
    assert painted, f"分隔线整块都是底色（{background.name()}），等于没画"
    # 上下都得留出空地，否则线会贴住两行文字
    assert rect.height() > len(painted) * scale
    return image.pixelColor(x, painted[0]), round(len(painted) / scale)


def test_split_shortcut_separates_modifiers_from_the_key() -> None:
    """修饰符与键要分得开：靠的是 QKeyCombination，不是去解析「⇧⌘C」这种字符串。

    **注意第一个修饰符是 ``ControlModifier``**：macOS 上 Qt 把 Ctrl 译成 Command
    （菜单里显示的一直是 ⌘R / ⌘⌫ / ⇧⌘C），而 ``MetaModifier`` 才是控制键 ⌃。认反了
    画出来就是 ⌃R。
    """
    modifiers, key = menus.split_shortcut(QKeySequence("Ctrl+Shift+C"))

    assert modifiers == frozenset(
        {Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier}
    )
    assert key == "C"
    assert menus.split_shortcut(QKeySequence("Ctrl+Backspace")) == (
        frozenset({Qt.KeyboardModifier.ControlModifier}),
        "⌫",
    )
    assert menus.split_shortcut(QKeySequence("Esc")) == (frozenset(), "⎋")
    assert menus.split_shortcut(QKeySequence()) == (frozenset(), "")


def test_each_shortcut_column_lines_up(qt_app: QApplication, window: MainWindow) -> None:
    """「三个列各自对齐」：同一列在所有行上是同一段 x，字母列一律贴右沿。

    列宽只按整个菜单量一次，所有行都按它摆（见 ``menus.ShortcutColumns``）。早先试着在
    条目文字里补空格来对齐，位移只有请求的 2/3 且不线性，永远差几像素。

    **对齐的是「那一组列」**（:meth:`menus.ShortcutColumns.slot_rects`），不是每个符号：
    符号落哪一格是逐行定的，见 ``test_a_lone_modifier_hugs_the_key``。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    metrics = QFontMetrics(menu.font())
    shortcuts = [
        action.shortcut()
        for action in menu.actions()
        if not action.isSeparator() and not action.shortcut().isEmpty()
    ]
    assert len(shortcuts) >= 3
    columns = menus.ShortcutColumns.measure(shortcuts, metrics)
    right, top, height = 400, 0, 26

    key_rights, command_columns = [], []
    for shortcut in shortcuts:
        modifiers, _ = menus.split_shortcut(shortcut)
        key_rect, modifier_rects = columns.cells(modifiers, right, top, height)
        key_rights.append(key_rect.right())
        if Qt.KeyboardModifier.ControlModifier in modifiers:
            command_columns.append(modifier_rects[Qt.KeyboardModifier.ControlModifier])

    assert len(set(key_rights)) == 1, "字母列右沿在每一行上都该一样"
    assert command_columns
    assert len({rect.left() for rect in command_columns}) == 1, "⌘ 该在同一列上"
    assert len({rect.left() for rect in columns.slot_rects(right, top, height)}) == 1 + len(
        columns.modifier_widths
    )


def test_a_lone_modifier_hugs_the_key(qt_app: QApplication, window: MainWindow) -> None:
    """**只有一个**修饰符的那一行，它占内侧那一列，不占自己那一列。

    照片菜单里「在默认应用中打开」的 ``⇧⏎`` 就是这么一行（用户原话：shift 应该在第二列）。
    若按「每个修饰符钉死一列」来画，它的 ⇧ 会落到 ⌘ 的外侧，中间隔着一格空白 —— 看着像
    凭空多出来一列，而同一行紧挨着的 ``⌘⏎`` 的 ⌘ 却贴在字母旁边。

    与之相对，``⇧⌘C`` 两个修饰符都要，各占一格、依次往外排（访达的读法）。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    assert isinstance(menu, menus.ShortcutColumnMenu)
    shift = Qt.KeyboardModifier.ShiftModifier
    command = Qt.KeyboardModifier.ControlModifier
    default_app = next(a for a in menu.actions() if a.text() == "在默认应用中打开")
    assert default_app.shortcut() == QKeySequence("Shift+Return"), "前提：这一行只有一个修饰符"

    metrics = QFontMetrics(menu.font())
    columns = menus.ShortcutColumns.measure_parts(menu.key_cells(), metrics)

    def at(action: QAction) -> dict[Qt.KeyboardModifier, QRect]:
        """这一行真正画出来的修饰符格（几何与 ``paintEvent`` 里算的是同一套）。"""
        rect = menu.actionGeometry(action)
        return columns.cells(menu.key_cell(action)[0], rect.right(), rect.top(), rect.height())[1]

    command_row = next(a for a in menu.actions() if a.text() == "拷贝路径")

    assert at(default_app)[shift].left() == at(command_row)[command].left(), "⇧⏎ 的 ⇧ 该与 ⌘ 同一列"
    assert at(command_row)[shift].left() < at(command_row)[command].left(), (
        "⇧⌘C 的 ⇧ 该退到外一格，两个修饰符各占一格"
    )


def test_only_the_modifiers_a_menu_uses_get_a_column(
    qt_app: QApplication, window: MainWindow
) -> None:
    """只给用到的修饰符留列。空白处那张菜单只有 ⌘A、⇧⌘N 与无修饰的 ⎋，
    硬留四列是白占像素。"""
    window._stack.setCurrentWidget(window._grid_page)

    menu = window.context_menu_for(-1)

    assert menu is not None
    metrics = QFontMetrics(menu.font())
    columns = menus.ShortcutColumns.measure(
        [action.shortcut() for action in menu.actions() if not action.shortcut().isEmpty()],
        metrics,
    )

    assert set(columns.modifier_widths) == {
        Qt.KeyboardModifier.ControlModifier,
        Qt.KeyboardModifier.ShiftModifier,
    }


def test_menus_reserve_room_for_the_shortcut_columns(
    qt_app: QApplication, window: MainWindow
) -> None:
    """菜单要为快捷键列块**预留宽度**：它不在条目文字里，Qt 不会替它腾地方。

    不预留就压在标签上（实测空白处菜单自然宽 124，列块左沿 62，而「缩略图大小」的文字
    一直伸到 90）。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    menu.popup(QPoint(60, 60))
    try:
        needed = _needed_width(menu)
        assert menu.width() >= needed
    finally:
        menu.close()


@pytest.mark.parametrize("kind", ["photo", "album"])
def test_open_hint_is_really_painted_in_the_key_column(
    qt_app: QApplication, window: MainWindow, kind: str
) -> None:
    """「打开」那一行的最右列里**真的有字** —— 那不是 ``QAction`` 的快捷键，是我们自己画的。

    抓一张图来数墨迹，而不是只查 ``key_cells()``：只查的话，将来谁把 ``paintEvent`` 里
    画提示的那一段删掉，测试还是绿的，而菜单上就只剩一个空位置 —— 用户看到的正是
    「快捷键图标没出来」。

    顺带钉住**画在对的位置**：墨迹必须落在这一行的键位列里，且不越出这一行的上下边
    （越出去就压到隔壁行上去了）。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = _row_for(window, kind)
    assert row is not None

    menu = window.context_menu_for(row)

    assert menu is not None
    assert isinstance(menu, menus.ShortcutColumnMenu)
    open_row = next(action for action in menu.actions() if action.text() == "打开")
    menu.popup(QPoint(60, 60))
    try:
        ink = _ink_in(menu, open_row)
    finally:
        menu.close()

    assert ink, f"「打开」那一行的最右列是空的（{menus.OPEN_KEY_HINT} 没画出来）"


def _ink_in(menu: QMenu, action: QAction) -> int:
    """「这一行的键位列」里画出来多少墨迹（像素）。

    位置按 :meth:`menus.ShortcutColumnMenu.key_rect` 算 —— 那正是 ``paintEvent`` 用的那套
    几何。抓图按设备像素给（Retina 上是两倍），几何却是逻辑像素，两边要按倍率对起来。
    """
    assert isinstance(menu, menus.ShortcutColumnMenu)
    background = menu.palette().color(QPalette.ColorRole.Window).rgb()
    metrics = QFontMetrics(menu.font())
    columns = menus.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
    rect = menu.actionGeometry(action)
    key_rect = columns.key_rect(rect.right(), rect.top(), rect.height())
    image = menu.grab().toImage()
    scale = image.devicePixelRatio()
    return sum(
        1
        for y in range(round(key_rect.top() * scale), round(key_rect.bottom() * scale) + 1)
        for x in range(round(key_rect.left() * scale), round(key_rect.right() * scale) + 1)
        if image.pixelColor(x, y).rgb() != background
    )


@pytest.mark.parametrize("kind", ["photo", "album", "blank"])
def test_shortcut_columns_start_after_the_longest_label(
    qt_app: QApplication, window: MainWindow, kind: str
) -> None:
    """三列要排在**最长那条文字的右侧**，且留出固定的间距。

    这一段以前只有 0.5~2 像素：相册菜单里「在访达中显示」和下面 ⇧ 所在那列几乎同一条
    x，看着像串成一片。留白取 :data:`menus.SHORTCUT_LABEL_GAP`，与原生菜单一致。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = _row_for(window, kind)
    assert row is not None

    menu = window.context_menu_for(row)

    assert menu is not None
    assert isinstance(menu, menus.ShortcutColumnMenu)
    metrics = QFontMetrics(menu.font())
    rows = [a for a in menu.actions() if not a.isSeparator()]
    # 按**真正要画出来的内容**量，而不是只量快捷键：「打开」那枚提示也在这一列里，
    # 漏掉它，这一列就会按最窄的那个键（C / ⎋）去算，然后压到那枚提示上。
    columns = menus.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
    rect = menu.actionGeometry(rows[0])
    # 整块列最左一条：按 slot_rects 算而不是按某一行 —— 逐行填列之后，不同行用到的
    # 修饰符格数不同（见 ``ShortcutColumns.cells``）。
    block_left = min(
        rect_.left() for rect_ in columns.slot_rects(rect.right(), rect.top(), rect.height())
    )
    label_start = menus.MENU_INSET + menus.MENU_CHECK_GUTTER
    label_end = label_start + max(metrics.horizontalAdvance(a.text()) for a in rows)

    assert block_left >= label_end + menus.SHORTCUT_LABEL_GAP


def _needed_width(menu: QMenu) -> int:
    """菜单至少要有多宽才装得下「最长标签 + 间距 + 最右列」。

    最右列按 :meth:`menus.ShortcutColumnMenu.key_cells` 数，**不能只数快捷键** ——
    「打开」那枚只画不生效的提示也在那一列里（见 ``ui.menus`` 里 ``show_key``），漏掉它
    这条断言就会放过「菜单不够宽、把提示压到标签上」这种坏法。
    """
    assert isinstance(menu, menus.ShortcutColumnMenu)
    metrics = QFontMetrics(menu.font())
    rows = [action for action in menu.actions() if not action.isSeparator()]
    labels = max(metrics.horizontalAdvance(action.text()) for action in rows)
    columns = menus.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
    return int(
        menus.MENU_INSET
        + menus.MENU_CHECK_GUTTER
        + labels
        + menus.SHORTCUT_LABEL_GAP
        + columns.width
        + menus.SHORTCUT_RIGHT_INSET
        + menus.MENU_INSET
    )


def _row_for(window: MainWindow, kind: str) -> int | None:
    """三种落点各挑一行：照片 / 相册 / 空白处。"""
    if kind == "blank":
        return -1
    window._grid.select_for_context(-1)
    if kind == "photo":
        row = window._model.row_of_path(window._library.root / "一张.jpg")
    else:
        window.set_browse_mode(BROWSE_MODE_ALBUMS)
        deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if window._model.row_of_path(window._library.root / "京都") >= 0:
                break
            time.sleep(0.01)
        QApplication.processEvents()
        row = window._model.row_of_path(window._library.root / "京都")
    return row if row >= 0 else None


def test_qt_no_longer_draws_the_shortcut_column_itself(
    qt_app: QApplication, window: MainWindow
) -> None:
    """我们自己画快捷键，就得先关掉 Qt 画的那一版，否则两者重叠。

    这个标志是**长期生效**的（动作与菜单栏共用），但只影响弹出菜单：菜单栏那边原生的照旧，
    ``shortcut`` 本身也没动，按键照常触发。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    with_shortcut = [a for a in menu.actions() if not a.shortcut().isEmpty()]
    assert with_shortcut
    for action in with_shortcut:
        assert not action.isShortcutVisibleInContextMenu()
        assert not action.shortcut().isEmpty(), "关掉的只是显示，按键还得留着"


def test_submenu_arrow_moves_into_the_key_column(qt_app: QApplication, window: MainWindow) -> None:
    """子菜单的右箭头与快捷键的**字母列**对齐（原生就是这样）。

    QSS 挪不动 Qt 那个箭头 —— 实测 ``padding-right`` 从 12 加到 28，它始终在 x=148~151.5，
    落在字母列（129~141）右边，看着是另一个不相干的东西。所以带子菜单的那张菜单要**关掉**
    Qt 画的那一个（``::right-arrow`` 尺寸给 0 之后完全不画），改由
    ``ShortcutColumnMenu`` 画进字母列。

    照片菜单自 ADR 0019 起**也有**子菜单行了（「复制到相册」「移动到相册」），于是它与
    空白处那张一样，箭头归我们自己画 —— 全软件的箭头落在同一列上。
    """
    window._stack.setCurrentWidget(window._grid_page)

    blank = window.context_menu_for(-1)

    assert blank is not None
    assert blank.has_submenu_arrows()
    assert blank.owns_submenu_arrows()
    assert "QMenu::right-arrow { width: 0; height: 0; }" in blank.styleSheet()

    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0
    photo = window.context_menu_for(row)
    assert photo is not None
    assert photo.has_submenu_arrows(), "照片菜单有「复制到相册 / 移动到相册」两个子菜单行"
    assert photo.owns_submenu_arrows()
    assert "QMenu::right-arrow { width: 0; height: 0; }" in photo.styleSheet()
    # 那两行没有快捷键，``key_cell`` 返回 None，正是 ``_paint_arrow`` 接手的那些行。
    assert {a.text() for a in photo.actions() if a.menu() is not None} == {
        "复制到相册",
        "移动到相册",
    }


def test_menus_without_checkable_items_keep_the_text_column(
    qt_app: QApplication, window: MainWindow
) -> None:
    """没有可勾选项的菜单，文字也要与照片菜单对齐。

    Qt 只在菜单里**存在可勾选项**时才预留那一列对勾宽度（实测 19 像素），整列文字
    跟着右移：照片菜单文字在 28，相册 / 分组 / 空白处只有 9。菜单里根本没有对勾可画，
    留着那列空白没有道理，但文字忽左忽右更难看 —— 所以统一补上，全软件的文字列对齐。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    photo = window.context_menu_for(row)
    blank = window.context_menu_for(-1)

    assert photo is not None and blank is not None
    gutter = f"padding-left: {menus.MENU_CHECK_GUTTER}px"
    assert gutter not in photo.styleSheet(), "照片菜单有可勾选项，那一列本来就预留了"
    assert gutter in blank.styleSheet(), "空白处菜单没有可勾选项，必须自己补上那一列"


def test_submenus_get_the_same_look(qt_app: QApplication, window: MainWindow) -> None:
    """空白处那份菜单带三个子菜单：子菜单不继承样式表，也不是子控件，得显式发。"""
    window._stack.setCurrentWidget(window._grid_page)

    menu = window.context_menu_for(-1)

    assert menu is not None
    owned = [
        action.menu()
        for action in menu.actions()
        if action.menu() is not None and action.menu().menuAction() is action
    ]
    assert [sub.title() for sub in owned] == ["分组", "排序", "缩略图大小"]
    # 三份子菜单彼此同一份样式；它们里面全是单选项（有勾），所以都不补那一列
    gutter = f"padding-left: {menus.MENU_CHECK_GUTTER}px"
    assert len({submenu.styleSheet() for submenu in owned}) == 1
    for submenu in owned:
        assert gutter not in submenu.styleSheet()
    # 分隔线的规则也得在（「排序」里那道就靠它，深色下才看得见）
    assert "QMenu::separator" in menu.styleSheet()


def test_menu_bar_menus_are_left_native(window: MainWindow) -> None:
    """菜单栏那边**不该**被右键菜单的样式波及。

    ``QAction`` 是全局复用的，``some_action.menu()`` 可能指向菜单栏里的子菜单；
    递归时若不认「这个子菜单的 ``menuAction()`` 是不是当前这条 action」，
    「视图 → 排序 / 分组 / 缩略图大小」会被顺带改掉 —— 它们并不属于这次右键。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0
    assert window.context_menu_for(row) is not None

    for title in ("文件", "编辑", "视图"):
        assert _bar_menu(window, title).styleSheet() == ""
    view = _bar_menu(window, "视图")
    for title in ("浏览方式", "分组", "排序", "缩略图大小"):
        assert _submenu(view, title).styleSheet() == ""


def test_menu_bar_menus_follow_theme_change(qt_app: QApplication, window: MainWindow) -> None:
    """系统换浅色 / 深色之后重下一次底色，菜单要跟着一起变。

    **只有这个测试会去动应用调色板** —— 它要的就是「系统换了一套颜色」这个前提，
    而那正是产品代码永远不做的一步（见 ``ui.theme`` 模块文档里那两个坑）。
    """
    original = QPalette(qt_app.palette())
    before = window.palette().color(QPalette.ColorRole.Window).name()
    try:
        flipped = QPalette(original)
        flipped.setColor(QPalette.ColorRole.Base, FLIPPED_BACKGROUND)
        flipped.setColor(QPalette.ColorRole.Window, FLIPPED_BACKGROUND)
        flipped.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        qt_app.setPalette(flipped)

        theme.apply_theme(qt_app)

        after = window.palette().color(QPalette.ColorRole.Window).name()
        assert after != before, "测试前提不成立：底色根本没换成功，这条断言测不到东西"
        assert _background(_bar_menu(window, "文件")) == after
    finally:
        qt_app.setPalette(original)
        theme.apply_theme(qt_app)
