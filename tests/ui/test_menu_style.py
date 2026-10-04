"""菜单面板的底色必须跟窗口是同一块（README 设计原则第 6 条）。

背景：统一底色是在 ``_build_ui`` 里下发的，而菜单是**之后**才建出来的 ——
菜单栏里那几个在 ``_build_actions`` 里建，右键菜单更晚，按一次才建。它们因此一直用
系统菜单色（浅色 ``#ececec`` / 深色 ``#2b2b2b``），与窗口的纯色底差一档：按一下菜单
的那一刻，界面就从「一整块底色」掉出去了。

这里钉住两件事：菜单栏里的菜单（含子菜单）与右键菜单都拿到窗口那份调色板，且系统
换浅色 / 深色时菜单栏里的菜单会跟着一起变。

**菜单的「长什么样」全在这一份**（``gallery.ui.menu_style``）：样式表、快捷键列、
子菜单箭头、以及「菜单里该有哪些条目」那半在 ``gallery.ui.menus``。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QItemSelectionModel, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QColor,
    QFontMetrics,
    QKeySequence,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidget  # noqa: E402
from waiting import wait_until

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import BROWSE_MODE_ALBUMS  # noqa: E402
from gallery.ui import menu_style, theme  # noqa: E402
from gallery.ui.external_button import calendar_icon  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.theme import rgba  # noqa: E402

#: 「假装系统换成了深色」用的底色。真深色下 Qt 给的是别的值，这里要的是
#: 「换一个和当前不同的值」，具体是哪个数与这条测试无关。
FLIPPED_BACKGROUND = QColor(23, 23, 23)

#: 等待扫描完成的时限。


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
    assert wait_until(
        qt_app, lambda: instance._scan_job is None and instance._model.rowCount() > 0
    ), "扫描没有在时限内完成"
    yield instance
    instance.close()


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
    assert f"border-radius: {menu_style.MENU_CORNER_RADIUS}px" in sheet
    assert f"padding: {menu_style.MENU_INSET}px" in sheet
    assert menu_style.MENU_ITEM_PADDING in sheet
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
    assert f"border-radius: {menu_style.MENU_HIGHLIGHT_RADIUS}px" in sheet
    # 两条带状态的规则都**要换上高亮那一档字色**：只给底色的话，浅色下是黑字压蓝底
    # （下面 :func:`test_the_selected_row_wears_the_highlighted_text` 量的是画出来的那一档）。
    highlighted_text = rgba(menu.palette().color(QPalette.ColorRole.HighlightedText))
    assert _state_rules(sheet).count(highlighted_text) >= 2, "悬停与当前项都要给字色"
    # 只查那两条带状态的规则，不查整份：分隔线那条带 margin 是故意要的（那里没有
    # 状态，见 ``ui.menus`` 里 MENU_SEPARATOR_HEIGHT 那段）。
    assert "margin" not in _state_rules(sheet)
    # 内缩的那一处在菜单上，**四边同值**：只留左右的话，高亮带会顶着面板上下两条边
    assert f"padding: {menu_style.MENU_INSET}px" in sheet


def test_the_selected_row_wears_the_highlighted_text(
    qt_app: QApplication, window: MainWindow
) -> None:
    """浅色下选中那一行的**条目文字**得跟着高亮一起变浅（用户报的就是它）。

    坑在 Qt 上：样式表一挂上去，``QMenu::item`` 就归 ``QStyleSheetStyle`` 画，而它**不再按
    ``State_Selected`` 去挑 ``HighlightedText``** —— 选中行与未选中行一律是正文色。深色下
    两者本来就都是浅字，看不出来；浅色下就成了黑字压蓝底，而我们自己画的快捷键列早就按
    ``_text_role`` 换成了 ``HighlightedText``，于是那一行左半边白、右半边黑。

    **真的抓一张图去量**：只查样式表里有没有 ``color``，将来谁把它删掉、或写成一个 Qt 根本
    不认的值，测试都还是绿的 —— 而坏法恰好是「看着没变、画出来是黑的」。
    """
    original = QPalette(qt_app.palette())
    try:
        flipped = _light_palette(original)
        qt_app.setPalette(flipped)
        theme.apply_theme(qt_app)

        window._stack.setCurrentWidget(window._grid_page)
        row = window._model.row_of_path(window._library.root / "一张.jpg")
        assert row >= 0
        # 右键之前网格会先把那一格选中（``_show_context_menu`` 走的正是这条），
        # 「拷贝路径」这一类命令才可用 —— 前置条件摆出来，不拿默认值去赌。
        assert window._grid.select_for_context(row) == row
        assert window._grid.selected_rows() == [row]
        menu = window.context_menu_for(row)
        assert menu is not None
        menu.popup(QPoint(60, 60))
        try:
            selected = _enabled_action(menu, "拷贝路径")
            menu.setActiveAction(selected)
            qt_app.processEvents()
            assert menu.activeAction() is selected, "前提：那一行真的被点亮了"
            selected_background, selected_ink = _label_ink(menu, selected)
            plain_background, plain_ink = _label_ink(menu, _enabled_action(menu, "重命名…"))
        finally:
            menu.close()
    finally:
        qt_app.setPalette(original)
        theme.apply_theme(qt_app)

    palette = flipped
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    text = palette.color(QPalette.ColorRole.WindowText)
    assert selected_background.rgb() == highlight.rgb(), (
        f"前提：那一行铺的是高亮色，实测 {selected_background.name()}"
    )
    assert plain_background != selected_background, "参照行没有被点亮，这一对就没得比"
    # 字色离哪一档更近，就说明这一行实际用的是哪一档 —— 不用写死阈值。
    assert _closer(selected_ink, highlighted_text, text), (
        f"选中行的字还是 {selected_ink.name()}（压在高亮底上），该是 "
        f"{highlighted_text.name()} —— 用户报的就是黑字压蓝底"
    )
    assert _closer(plain_ink, text, highlighted_text), (
        f"没被点中的那一行也变成了 {plain_ink.name()}，那是反过来的毛病"
    )


def _light_palette(original: QPalette) -> QPalette:
    """一份**浅色**的调色板：白底、深字，配上系统那套高亮色。

    存在的理由是这条测试要测的就是浅色下的坏法，而跑测试的机器此刻是深是浅不能假定。
    """
    flipped = QPalette(original)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        flipped.setColor(group, QPalette.ColorRole.Base, QColor("#ffffff"))
        flipped.setColor(group, QPalette.ColorRole.Window, QColor("#ffffff"))
        flipped.setColor(group, QPalette.ColorRole.Text, QColor("#000000"))
        flipped.setColor(group, QPalette.ColorRole.WindowText, QColor("#000000"))
        flipped.setColor(group, QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return flipped


def _closer(ink: QColor, one: QColor, other: QColor) -> bool:
    """``ink`` 是更接近 ``one`` 那一档的颜色吗（抗锯齿的边缘像素不算）。"""
    return abs(ink.lightness() - one.lightness()) < abs(ink.lightness() - other.lightness())


def _enabled_action(menu: QMenu, text: str) -> QAction:
    """那张菜单里文字为 ``text``、且此刻可点的那一行（置灰行测不到高亮）。"""
    action = next((a for a in menu.actions() if a.text() == text and a.isEnabled()), None)
    assert action is not None, f"菜单里没有可点的「{text}」这一行"
    return action


def _label_ink(menu: QMenu, action: QAction) -> tuple[QColor, QColor]:
    """这一行**标签**上铺的底色与画出来的字色。

    取样范围是标签本身（菜单内缩 + 那一列对勾宽，右边刚好到文字末尾），因此右沿那列
    我们自己画的快捷键不掺进来 —— 这一条要盯的是 Qt 画的那一半。

    底色取「这一块里最多的那个颜色」，字色取「离它最远的那一档」：抗锯齿的边缘像素于是
    自动被排除，剩下的就是笔画真正的颜色。远近按 ``lightness()``（HSL 的明度）量，不用
    ``value()`` —— 高亮那一档蓝的 ``value()`` 高达 198，白字与它只差 57，按那条线量就
    白字也成不了「字」。
    """
    image = menu.grab().toImage()
    scale = image.devicePixelRatio()
    rect = menu.actionGeometry(action)
    metrics = QFontMetrics(menu.font())
    left = round((menu_style.MENU_INSET + menu_style.MENU_CHECK_GUTTER) * scale)
    right = left + round(metrics.horizontalAdvance(action.text()) * scale)
    top, bottom = round(rect.top() * scale), round(rect.bottom() * scale)
    colors: dict[str, int] = {}
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            name = image.pixelColor(x, y).name()
            colors[name] = colors.get(name, 0) + 1
    assert colors, f"「{action.text()}」那一行一个像素都没抓到"
    background = QColor(max(colors, key=lambda name: colors[name]))
    marks = [
        QColor(name)
        for name in colors
        if abs(QColor(name).lightness() - background.lightness()) > 60
    ]
    assert marks, f"「{action.text()}」那一行没画出字来（只有底色 {background.name()}）"
    return background, max(marks, key=lambda color: abs(color.lightness() - background.lightness()))


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

    dark_line = _line_on(dark, menu_style.separator_color(dark, QColor("#ffffff")))
    light_line = _line_on(light, menu_style.separator_color(light, QColor("#000000")))

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

    assert thickness == menu_style.MENU_SEPARATOR_HEIGHT, f"分隔线厚了：{thickness} 像素"
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
    modifiers, key = menu_style.split_shortcut(QKeySequence("Ctrl+Shift+C"))

    assert modifiers == frozenset(
        {Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier}
    )
    assert key == "C"
    assert menu_style.split_shortcut(QKeySequence("Ctrl+Backspace")) == (
        frozenset({Qt.KeyboardModifier.ControlModifier}),
        "⌫",
    )
    assert menu_style.split_shortcut(QKeySequence("Esc")) == (frozenset(), "⎋")
    assert menu_style.split_shortcut(QKeySequence()) == (frozenset(), "")


def test_each_shortcut_column_lines_up(qt_app: QApplication, window: MainWindow) -> None:
    """「三个列各自对齐」：同一列在所有行上是同一段 x，字母列一律贴右沿。

    列宽只按整个菜单量一次，所有行都按它摆（见 ``menu_style.ShortcutColumns``）。早先试着在
    条目文字里补空格来对齐，位移只有请求的 2/3 且不线性，永远差几像素。

    **对齐的是「那一组列」**（:meth:`menu_style.ShortcutColumns.slot_rects`），不是每个符号：
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
    columns = menu_style.ShortcutColumns.measure(shortcuts, metrics)
    right, top, height = 400, 0, 26

    key_rights, command_columns = [], []
    for shortcut in shortcuts:
        modifiers, _ = menu_style.split_shortcut(shortcut)
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
    assert isinstance(menu, menu_style.ShortcutColumnMenu)
    shift = Qt.KeyboardModifier.ShiftModifier
    command = Qt.KeyboardModifier.ControlModifier
    default_app = next(a for a in menu.actions() if a.text() == "在默认应用中打开")
    assert default_app.shortcut() == QKeySequence("Shift+Return"), "前提：这一行只有一个修饰符"

    metrics = QFontMetrics(menu.font())
    columns = menu_style.ShortcutColumns.measure_parts(menu.key_cells(), metrics)

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
    columns = menu_style.ShortcutColumns.measure(
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
    row = _row_for(qt_app, window, kind)
    assert row is not None

    menu = window.context_menu_for(row)

    assert menu is not None
    assert isinstance(menu, menu_style.ShortcutColumnMenu)
    open_row = next(action for action in menu.actions() if action.text() == "打开")
    menu.popup(QPoint(60, 60))
    try:
        ink = _ink_in(menu, open_row)
    finally:
        menu.close()

    assert ink, f"「打开」那一行的最右列是空的（{menu_style.OPEN_KEY_HINT} 没画出来）"


def _ink_in(menu: QMenu, action: QAction) -> int:
    """「这一行的键位列」里画出来多少墨迹（像素）。

    位置按 :meth:`menu_style.ShortcutColumnMenu.key_rect` 算 —— 那正是 ``paintEvent`` 用的那套
    几何。抓图按设备像素给（Retina 上是两倍），几何却是逻辑像素，两边要按倍率对起来。
    """
    assert isinstance(menu, menu_style.ShortcutColumnMenu)
    background = menu.palette().color(QPalette.ColorRole.Window).rgb()
    metrics = QFontMetrics(menu.font())
    columns = menu_style.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
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
    x，看着像串成一片。留白取 :data:`menu_style.SHORTCUT_LABEL_GAP`，与原生菜单一致。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = _row_for(qt_app, window, kind)
    assert row is not None

    menu = window.context_menu_for(row)

    assert menu is not None
    assert isinstance(menu, menu_style.ShortcutColumnMenu)
    metrics = QFontMetrics(menu.font())
    rows = [a for a in menu.actions() if not a.isSeparator()]
    # 按**真正要画出来的内容**量，而不是只量快捷键：「打开」那枚提示也在这一列里，
    # 漏掉它，这一列就会按最窄的那个键（C / ⎋）去算，然后压到那枚提示上。
    columns = menu_style.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
    rect = menu.actionGeometry(rows[0])
    # 整块列最左一条：按 slot_rects 算而不是按某一行 —— 逐行填列之后，不同行用到的
    # 修饰符格数不同（见 ``ShortcutColumns.cells``）。
    block_left = min(
        rect_.left() for rect_ in columns.slot_rects(rect.right(), rect.top(), rect.height())
    )
    label_start = menu_style.MENU_INSET + menu_style.MENU_CHECK_GUTTER
    label_end = label_start + max(metrics.horizontalAdvance(a.text()) for a in rows)

    assert block_left >= label_end + menu_style.SHORTCUT_LABEL_GAP


def _needed_width(menu: QMenu) -> int:
    """菜单至少要有多宽才装得下「最长标签 + 间距 + 最右列」。

    最右列按 :meth:`menu_style.ShortcutColumnMenu.key_cells` 数，**不能只数快捷键** ——
    「打开」那枚只画不生效的提示也在那一列里（见 ``ui.menus`` 里 ``show_key``），漏掉它
    这条断言就会放过「菜单不够宽、把提示压到标签上」这种坏法。
    """
    assert isinstance(menu, menu_style.ShortcutColumnMenu)
    metrics = QFontMetrics(menu.font())
    rows = [action for action in menu.actions() if not action.isSeparator()]
    labels = max(metrics.horizontalAdvance(action.text()) for action in rows)
    columns = menu_style.ShortcutColumns.measure_parts(menu.key_cells(), metrics)
    return int(
        menu_style.MENU_INSET
        + menu_style.MENU_CHECK_GUTTER
        + labels
        + menu_style.SHORTCUT_LABEL_GAP
        + columns.width
        + menu_style.SHORTCUT_RIGHT_INSET
        + menu_style.MENU_INSET
    )


def _row_for(qt_app: QApplication, window: MainWindow, kind: str) -> int | None:
    """三种落点各挑一行：照片 / 相册 / 空白处。"""
    if kind == "blank":
        return -1
    window._grid.select_for_context(-1)
    if kind == "photo":
        row = window._model.row_of_path(window._library.root / "一张.jpg")
    else:
        window.set_browse_mode(BROWSE_MODE_ALBUMS)
        target = window._library.root / "京都"
        assert wait_until(qt_app, lambda: window._model.row_of_path(target) >= 0), (
            "相册模式没有在时限内列出「京都」"
        )
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


def _menu_to_paint(qt_app: QApplication, window: MainWindow, kind: str) -> QMenu:
    """按落点拼出那张菜单（**已经**上过样式、接过快捷键，与真实弹出时同一份）。"""
    window._stack.setCurrentWidget(window._grid_page)
    if kind == "blank":
        menu = window.context_menu_for(-1)
    elif kind == "multi":
        window._grid.setCurrentIndex(window._model.index(0, 0))
        selection = window._grid.selectionModel()
        assert selection is not None
        selection.clearSelection()
        for row in (0, 1):
            selection.select(
                window._model.index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        assert window._grid.select_for_context(1) == 1
        assert len(window._grid.selected_rows()) > 1, "多选这张菜单得先选中两行以上"
        menu = window.context_menu_for(1)
    elif kind == "group":
        member = PhotoRef(path=window._library.root / "一张.jpg", size=1, mtime_ns=1)
        window._model.reorder(
            [GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(member,))]
        )
        qt_app.processEvents()
        assert window._grid.select_for_context(0) == 0
        menu = window.context_menu_for(0)
    elif kind == "detail":
        row = window._model.row_of_path(window._library.root / "一张.jpg")
        assert row >= 0
        window._open_detail(window._model.index(row, 0))
        assert wait_until(qt_app, lambda: window._detail.has_image), "大图没有在时限内显示出来"
        menu = window.detail_context_menu()
    else:
        row = window._model.row_of_path(window._library.root / "一张.jpg")
        assert row >= 0
        menu = window.context_menu_for(row)
    assert menu is not None, f"「{kind}」这一下压根没拼出菜单来"
    return menu


@pytest.mark.parametrize("kind", ["blank", "photo", "multi", "group", "detail"])
def test_every_row_that_owns_ink_really_paints_it(
    qt_app: QApplication, window: MainWindow, kind: str
) -> None:
    """**每一行该有的墨迹都得真的有** —— 子菜单的右箭头与快捷键是同一支笔画出来的。

    这条钉的是「画出来了」而不是「打算画」：上面那条只查样式表与开关，看不见笔是不是
    真的落到屏幕上。它漏过一次 —— ``_paint_arrow`` 里留着一次 ``painter.end()``，于是**第一
    个**子菜单行（分组 / 复制到相册）画得出箭头，它之后的每一行都画不出，症状正是用户报的
    「排序 / 缩略图大小 / 移动到相册没有右箭头」；排在后面的快捷键（⌘R / ⌘⌫ / ⌘I / ⎋）
    也一起消失。而 Qt 只在 stderr 留一句 ``QPainter::setPen: Painter not active``，界面上
    完全看不出来，测试也就一路绿灯。

    所以这里**数墨迹**（见 :func:`_ink_in`），而且是**逐行**数：坏法是「从第几行往后全没了」，
    只查第一行或只查标志位都抓不住。

    快捷键一并查：它们与箭头走同一个出口、同一支笔，箭头丢在哪一行它们就从哪一行开始丢。
    """
    menu = _menu_to_paint(qt_app, window, kind)
    assert isinstance(menu, menu_style.ShortcutColumnMenu)

    menu.popup(QPoint(60, 60))
    try:
        # 高亮带会把整行涂满颜色，于是「这一行本该有墨迹」就变成了永远为真 —— 那时这把
        # 尺子就废了。弹在菜单左上角（菜单自己内容的内缩处）不会点亮任何一行。
        assert menu.activeAction() is None, "有行被高亮了，数墨迹就数不出箭头了"
        # 没有子菜单、也没有快捷键/提示的那些行，那一列本来就该空着（「复制元数据」之类）。
        painted = [
            action
            for action in menu.actions()
            if not action.isSeparator()
            and (action.menu() is not None or menu.key_cell(action) is not None)
        ]
        missing = [action.text() for action in painted if _ink_in(menu, action) == 0]
    finally:
        menu.close()

    assert painted, f"「{kind}」这张菜单里一行该有墨迹的都没有，测的就不是它了"
    assert not missing, f"「{kind}」这几行的最右列是空的：{missing}"


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
    gutter = f"padding-left: {menu_style.MENU_CHECK_GUTTER}px"
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
    gutter = f"padding-left: {menu_style.MENU_CHECK_GUTTER}px"
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


# ---- 「朴素下拉菜单」与搜索框的候选列表必须是同一种东西 -------------------


def test_the_plain_menu_is_sized_like_the_search_popup(qt_app: QApplication) -> None:
    """字号、行内边距、圆角、字色**全部来自同一处常量**。

    这两个控件用户是挨着用的（搜索框就在那个按钮右边），字号或行内边距差一档，
    一眼就看得出「这是另一个软件弹的」。所以这里断言的不是「像」，是**同一个数**。
    """
    style = menu_style.popup_menu_style(QMenu())

    assert f"font-size: {menu_style.POPUP_FONT_PX}px" in style
    assert f"border-radius: {menu_style.MENU_HIGHLIGHT_RADIUS}px" in style
    assert f"border: {menu_style.POPUP_LIST_BORDER}px solid" in style
    # 行的内边距**左右同档**（就是候选列表那一档）：左内边距只管文字，图标那一侧
    # 归下面那条 ``::icon`` 规则 —— 两处各管一半。
    assert f"padding: 3px {menu_style.POPUP_LIST_ITEM_PAD}px;" in style
    # 图标那一段靠 ``::icon`` 的 ``margin-left``，**不是** ``QMenu::item`` 的左内边距
    # （左内边距只推文字，改它等于把右边那段又拉宽）。乘 2 见那条常量的说明。
    assert f"QMenu::icon {{ margin-left: {menu_style.POPUP_MENU_ICON_MARGIN}px; }}" in style


def test_the_plain_menu_spells_out_its_text_colour(qt_app: QApplication) -> None:
    """**item 的字色必须写死。**

    挂了样式表之后 Qt 不再按调色板给 item 上色 —— 实测深色下菜单的调色板里
    ``WindowText`` 明明白白是 ``#ffffff``，画出来却是 ``#000000``（近黑底上等于
    看不见）。曾经只在 ``:hover`` / ``:selected`` 里写了字色，于是**没被指到的那些行
    全是黑的**。候选列表那份一直写死了 ``color``，是同一个坑。
    """
    style = menu_style.popup_menu_style(QMenu())

    assert f"color: {rgba(QMenu().palette().color(QPalette.ColorRole.WindowText))}" in style


def test_the_search_popup_and_the_plain_menu_share_one_font_size(qt_app: QApplication) -> None:
    """字号只有一处来源：改 ``POPUP_FONT_PX`` 两边一起变。

    各写一个字面量的话，改了其中一处、另一处不会报错也不会变红 —— 只会**看起来
    不是一套东西**，而那正是这一节要防的。
    """
    from gallery.ui import search_field

    popup = search_field.popup_style(QTreeWidget())
    assert f"font-size: {menu_style.POPUP_FONT_PX}px" in popup
    assert f"font-size: {menu_style.POPUP_FONT_PX}px" in menu_style.popup_menu_style(QMenu())


def test_the_icon_sits_evenly_between_edge_and_text(qt_app: QApplication) -> None:
    """**图标到项矩形左沿的距离 ≈ 图标到文字的距离**（逐像素量渲染结果）。

    用户的原话：「icon 距离高亮框的边距要和 icon 和后面文字的距离差不多」。
    项矩形就是高亮带画的那一块，所以量它就等于量高亮带。

    这一条**只能靠渲染出来量**：一边由 ``QMenu::icon`` 的 ``margin-left`` 说了算、
    另一边由 Qt 自己排的图标列说了算，而 ``margin-left`` 到底把图标推多远**取决于
    Qt 怎么排那一列**（实测：拿点满整张画布的标记量是一个数，拿真图标量是另一个数）。
    Qt 换了排法，样式表里那个数字不会报错、也不会有别的测试变红，只有这条能发现。

    **用的就是应用自己那个图标**（``calendar_icon()``）：图标墨迹的宽窄直接决定
    右边那一段有多长，换个形状的标记量出来的就不是同一个东西 —— 这里试过，
    满格标记的平衡点差着 5 点。装了 PWA 用真图标、没装用自画的那一个，
    **两条路量出来一样**（四种组合实测都是左右各 8 点），所以这条不依赖本机装了什么。

    **量的是「没高亮」那一张**（黑字压白底）：高亮那张里文字是白的压蓝底，
    抗锯齿的边缘落在哪一列全看阈值，同一个菜单能差出 3 点来 —— 比这条的容差还大。
    """
    menu = QMenu()
    # 颜色钉死：`Window` / `WindowText` 在系统主题下是什么值不定，量的时候要有确定判据。
    palette = QPalette(menu.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor("#f6f6f6"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    menu.setPalette(palette)
    menu.setStyleSheet(menu_style.popup_menu_style(menu))

    action = QAction(calendar_icon(), "某项", menu)
    menu.addAction(action)
    menu.popup(QPoint(200, 200))
    for _ in range(4):
        qt_app.processEvents()

    image = menu.grab().toImage()
    item = menu.actionGeometry(action)  # 项矩形 = 高亮带画的那一块
    menu.close()

    def is_background(color: QColor) -> bool:
        return (
            abs(color.red() - 246) < 12
            and abs(color.green() - 246) < 12
            and abs(color.blue() - 246) < 12
        )

    def is_text(color: QColor) -> bool:
        return color.red() + color.green() + color.blue() < 300

    # 图标 = 既不是底色、也不是文字的那些列。文字与图标之间会空几列，据此分段。
    colored = [
        x
        for x in range(item.left(), item.right() + 1)
        if any(
            not is_background(image.pixelColor(x, y)) and not is_text(image.pixelColor(x, y))
            for y in range(item.top(), item.bottom() + 1)
        )
    ]
    icon: list[int] = []
    for x in colored:
        if not icon or x - icon[-1] <= 3:
            icon.append(x)
        else:
            break
    tail = [x for x in colored if x > max(icon)]
    assert icon and tail, "前提不成立：图标或文字没画出来，这条量不到东西"

    left_gap = min(icon) - item.left()
    right_gap = min(tail) - max(icon) - 1

    assert abs(left_gap - right_gap) <= 1, (
        f"图标两边的留白不匀：左边 {left_gap} 点、右边 {right_gap} 点"
    )
