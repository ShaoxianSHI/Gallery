"""标题条上那个「用外部应用打开」入口：摆位、下拉框里那几项、以及它跟着页面出现。

**为什么这些要单独测**：按钮只有日历页上出现，而日历页要一个真的库才进得去 ——
于是「它该不该出现」这条规矩很容易只靠手点验。摆位与下拉框那几条更不该为此造窗口。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    QPoint,
    QRect,
    QSize,  # noqa: E402
)
from PySide6.QtGui import QColor, QIcon, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,  # noqa: E402
    QMenu,
    QToolButton,
)
from waiting import wait_until  # noqa: E402

from gallery.core.external import apps, launch  # noqa: E402
from gallery.core.library.config import BROWSE_MODE_CALENDAR, prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.core.metadata.report import MetadataReport  # noqa: E402
from gallery.ui import desktop  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window.state.level_stack import AlbumFrame, GroupFrame  # noqa: E402
from gallery.ui.window.widgets import external_button  # noqa: E402
from gallery.ui.window.widgets.external_button import (  # noqa: E402
    BUTTON_GAP,
    BUTTON_WIDTH,
    ICON_EDGE,
    MENU_EDGE,
    MENU_GAP,
    ExternalAppsButton,
    ExternalEntry,
    button_rect,
    calendar_icon,
    folder_icon,
    icon_for,
    menu_origin,
)
from gallery.ui.window.widgets.play_button import PLAY_BUTTON_WIDTH  # noqa: E402
from gallery.ui.window.widgets.search_field import (
    MARGIN_RIGHT as SEARCH_FIELD_MARGIN_RIGHT,  # noqa: E402
)
from gallery.ui.window.widgets.search_field import TOGGLE_EDGE, TOGGLE_GAP  # noqa: E402
from gallery.ui.window.widgets.search_field import WIDTH as SEARCH_FIELD_WIDTH  # noqa: E402

#: 搜索胶囊摆好之后的那一块。**按钮是从它往左长出来的**，所以这一块就是唯一的输入。
FIELD = QRect(300, 7, 208, 22)

#: 「装了」那一支用的假应用包。开发机上真装着它们、CI 上没有，于是**默认一律当作没装**
#: （见 :func:`no_external_apps`），要摆出某一项的用例自己用 :func:`_only` 点名。
QSPACE_APP = Path("/Applications/QSpace Pro.app")
IMAGEGLASS_APP = Path("/Applications/ImageGlass.app")
PREVIEW_APP = Path("/System/Applications/Preview.app")
MAPS_APP = Path("/System/Applications/Maps.app")


@pytest.fixture(autouse=True)
def no_external_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    """下拉框里有哪几项**不该取决于跑测试的这台机器装没装它们**。

    不假装的话，装了 QSpace / ImageGlass 的开发机上菜单里多两项、CI 上少两项 —— 同一份断言在
    两边必然一红一绿。
    """
    monkeypatch.setattr(apps, "find_app", lambda app: None)


def _only(monkeypatch: pytest.MonkeyPatch, **installed: Path) -> None:
    """把名册换成「只装了这几个」，键是那一项的 id（``qspace`` / ``preview`` / ``imageglass``）。

    整份名册一起换，「装了预览」这类用例才不会连带把真实机器上装着的那些也摆出来。
    """
    monkeypatch.setattr(apps, "find_app", lambda app: installed.get(app.id))


def _set_anchor(window: MainWindow, day: datetime.date) -> None:
    """把日历那一页的锚点摆到某一天。

    **``CalendarPage`` 没有 ``set_anchor``**：它是用户翻页翻到的那个位置，只能
    ``step`` / ``go_today`` 相对地走（``set_anchor`` 在各层那一份上）。而这里要测的
    只是「打开时读到的是不是这一天」，直接写那个私有字段最省事 —— 测试摸私有成员
    是本仓库的惯例（见 ``main_window`` 顶部那段说明）。
    """
    window._calendar_page._anchor = day


# ---- 摆位（纯算术）---------------------------------------------------


def test_button_sits_left_of_the_search_field_with_a_gap() -> None:
    rect = button_rect(FIELD)
    # 用 ``left() + width()`` 而不是 ``right()``：后者算的是**最后一个像素的坐标**，
    # 与下一段的起点一比就差一。
    assert rect.left() + rect.width() + BUTTON_GAP == FIELD.left()


def test_button_is_exactly_as_tall_as_the_field() -> None:
    """**从胶囊那一块往左长出来**，于是两者必然同高、同一基线。

    各自拿标题条的高度各算一遍就会歪：胶囊为了字的中线被往下挪了
    ``TEXT_ALIGN_DROP``，而按钮没挪 —— 肉眼是「歪了一点」。
    """
    rect = button_rect(FIELD)
    assert rect.height() == FIELD.height()
    assert rect.top() == FIELD.top()


def test_button_is_wide_enough_for_an_icon_and_an_arrow() -> None:
    """里面放的是**两样东西**（图标 + 下拉箭头），正方形放不下。"""
    assert button_rect(FIELD).width() > ICON_EDGE
    assert button_rect(FIELD).width() >= 36


# ---- 下拉框（不必造窗口）-----------------------------------------------


def _entry(entry_id: str, name: str, enabled: bool) -> ExternalEntry:
    return ExternalEntry(id=entry_id, name=name, icon=None, enabled=lambda: enabled)


def _menu(button: ExternalAppsButton) -> QMenu:
    """那个下拉菜单。**故意取私有成员**：菜单不再挂在按钮上（挂着 Qt 就会自己再画
    一个菜单三角与我们画的箭头叠在一起），没有一个公开的入口拿得到它。"""
    return button._menu  # noqa: SLF001 - 本仓库的惯例就是直接摸私有成员


def _menu_texts(button: ExternalAppsButton) -> list[str]:
    return [action.text() for action in _menu(button).actions()]


def test_menu_lists_the_full_name(qt_app: QApplication) -> None:
    button = ExternalAppsButton()
    button.set_entries([_entry("google_calendar", "Google 日历", True)])
    assert _menu_texts(button) == ["Google 日历"]


def test_menu_carries_an_icon(qt_app: QApplication) -> None:
    """下拉框里是**图标 + 全称**：只有图标的话用户得一个个点过去才知道会发生什么。"""
    button = ExternalAppsButton()
    button.set_entries([_entry("google_calendar", "Google 日历", True)])
    action = _menu(button).actions()[0]
    assert isinstance(action.icon(), QIcon)
    assert not action.icon().isNull()


def test_the_left_half_shows_only_the_icon(qt_app: QApplication) -> None:
    button = ExternalAppsButton()
    assert button._primary.toolButtonStyle().name == "ToolButtonIconOnly"


def test_the_left_half_carries_an_icon(qt_app: QApplication) -> None:
    """**左半那个图标要有。**

    忘了按钮自己时 Qt 会给空图标画一个占位方框，屏幕上就成了「一个方框 + 一个下拉三角」。
    """
    button = ExternalAppsButton()
    button.set_entries([_entry("google_calendar", "Google 日历", True)])
    assert button.default_icon().isNull() is False
    assert button._primary.icon().isNull() is False


def test_no_entries_means_no_icon(qt_app: QApplication) -> None:
    """一条都没有时按钮整个收起来，左半的图标也该收掉，不留一个空方框。"""
    button = ExternalAppsButton()
    button.set_entries([_entry("x", "某应用", False)])
    assert button.default_icon().isNull() is True


def test_it_is_two_buttons_side_by_side(qt_app: QApplication) -> None:
    """**它是两个按钮，不是一个。**

    左半直接打开、右半才下拉 —— 是两件不同的事，各接各的鼠标事件。做成一个按钮的
    两个部位（``QToolButton`` 的菜单指示器）时，图标与箭头挤在一格里，读起来像叠在
    一起（视觉检查里用户指出来的就是它）。
    """
    button = ExternalAppsButton()
    button.resize(BUTTON_WIDTH, 22)
    # **先 show()**：Qt 对一个还没显示过的控件不发 resize 事件，于是两半还是
    # 默认那个 100×30（同一个坑：`isVisible()` 对子控件也是 False）。
    button.show()
    left, right = button._primary.geometry(), button._menu_button.geometry()

    assert left.left() == 0
    assert left.right() + 1 == right.left(), "两半必须紧挨着，中间不留缝"
    assert left.height() == right.height() == button.height()
    assert right.width() == MENU_EDGE
    assert left.width() > ICON_EDGE, "左半至少要放得下图标"

    # 两半是各自独立的控件，不是同一个按钮的两个部位。
    assert button._primary is not button._menu_button
    assert button._primary.parent() is button._menu_button.parent() is button


def test_the_right_half_does_not_borrow_qt_menu_indicator(qt_app: QApplication) -> None:
    """**右半不把菜单挂到按钮上。**

    挂了 Qt 就会在那一格里再画一个它自己的菜单三角，与我们画的那个箭头叠在一起；而给
    ``::menu-indicator`` 写样式去压它，那个三角又会整个消失。干脆两条都不走。
    """
    button = ExternalAppsButton()
    assert button._menu_button.menu() is None
    assert button._menu_button.popupMode() == QToolButton.ToolButtonPopupMode.DelayedPopup


def test_clicking_the_left_half_opens_the_default_entry(qt_app: QApplication) -> None:
    """左半那一下**直接打开**，不必先弹菜单再点一次。"""
    entry = _entry("google_calendar", "Google 日历", True)
    button = ExternalAppsButton()
    button.set_entries([entry])
    seen: list[object] = []
    button.entry_activated.connect(seen.append)

    button._primary.click()

    assert seen == [entry]


def test_the_left_half_opens_the_first_available_one(qt_app: QApplication) -> None:
    """默认那一项是**第一个此刻可用的** —— 现在一页只有一项，以后加了第二项仍然成立。"""
    off = _entry("finder", "访达", False)
    on = _entry("google_calendar", "Google 日历", True)
    button = ExternalAppsButton()
    button.set_entries([off, on])
    assert button.default_entry() is on


def test_the_left_half_is_disabled_with_nothing_to_open(qt_app: QApplication) -> None:
    button = ExternalAppsButton()
    button.set_entries([_entry("x", "某应用", False)])
    assert button._primary.isEnabled() is False
    assert button.default_entry() is None


def test_unavailable_entries_are_left_out(qt_app: QApplication) -> None:
    """此刻没东西可开的那一项**整条不出现**，而不是摆一个点下去没反应的灰条目。"""
    button = ExternalAppsButton()
    button.set_entries(
        [
            _entry("google_calendar", "Google 日历", True),
            _entry("finder", "访达", False),
        ]
    )
    assert _menu_texts(button) == ["Google 日历"]


def test_refresh_reasks_availability(qt_app: QApplication) -> None:
    """翻页要重新问一遍 —— 判据是活的，不是建菜单那一刻定死的。"""
    state = {"on": False}
    entry = ExternalEntry(id="x", name="某应用", icon=None, enabled=lambda: state["on"])
    button = ExternalAppsButton()
    button.set_entries([entry])
    assert _menu_texts(button) == []
    state["on"] = True
    button.refresh()
    assert _menu_texts(button) == ["某应用"]


def test_choosing_an_entry_reports_which(qt_app: QApplication) -> None:
    """点下去只报「选了哪一项」，具体做什么在外面按 id 分派。"""
    entry = _entry("google_calendar", "Google 日历", True)
    button = ExternalAppsButton()
    button.set_entries([entry])
    seen: list[object] = []
    button.entry_activated.connect(seen.append)
    _menu(button).actions()[0].trigger()
    assert seen == [entry]


def test_calendar_icon_is_never_null(qt_app: QApplication) -> None:
    """装了 PWA 用它自己的图标，没装就画一个 —— 两种情况下都得有个图标。"""
    assert not calendar_icon().isNull()


# ---- 装进窗口 ---------------------------------------------------------


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(root / "一张.jpg")
    prepare_library(root)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    # **要真的 show 出来**：子控件的 ``isVisible()`` 说的是「自己和祖先都可见」，
    # 窗口没显示时它一律是 False —— 那不是「按钮没摆出来」。
    instance.show()
    assert wait_until(
        qt_app, lambda: instance._scan_job is None and instance._model.rowCount() > 0
    ), "扫描没有在时限内完成"
    yield instance
    instance.close()


def test_button_is_visible_on_the_grid_page(window: MainWindow) -> None:
    """网格页上换的是**访达**那一项（有当前文件夹可开），按钮照样在。"""
    assert window._stack.currentWidget() is window._grid_page
    assert window._external_button.isVisible() is window._owns_title_bar


def test_grid_page_offers_the_finder_not_the_calendar(window: MainWindow) -> None:
    """日历那一项**只在日历页出现** —— 网格页上压根没有「哪一天」可开。"""
    texts = _menu_texts(window._external_button)
    assert "访达" in texts
    assert "Google 日历" not in texts


def test_calendar_page_offers_only_the_calendar(window: MainWindow) -> None:
    """反过来也一样：日历页只有 Google 日历那一项。"""
    # 页面是栈的函数：要真的在日历页上，栈底得是日历（``_enter_calendar`` 只负责喂内容）。
    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    assert _menu_texts(window._external_button) == ["Google 日历"]


def test_no_library_means_the_button_stays_out(window: MainWindow) -> None:
    """还没打开库（欢迎页）时两样都没有 —— **空菜单的图标只会让人以为坏了**。"""
    window._library = None
    window._external_button.refresh()
    assert window._external_button.is_available() is False
    assert _menu_texts(window._external_button) == []


def test_the_current_folder_is_the_library_root_at_first(window: MainWindow) -> None:
    """全部照片视图下锚点是库根 —— 口径就是既有那一句 ``_current_dir or root``。"""
    # ``_current_dir`` 是栈的投影、不可写（ADR 0041），摆出「没进相册」只能用**空的栈**。
    assert window._level_stack.frames == (), "前提不成立：栈上已经有东西了"
    assert window._current_folder() == window._library.root


def test_the_current_folder_follows_the_album_you_went_into(window: MainWindow) -> None:
    album = window._library.root / "京都"
    album.mkdir()
    window._push_frame(AlbumFrame(album))
    assert window._current_folder() == album


def test_grouping_still_reaches_the_folder_behind_it(window: MainWindow) -> None:
    """**分组时用的也是它**：分组卡片自己不是磁盘上的东西，它代表的那批照片就摆在
    这个目录里（ADR 0020）—— 于是向上找到的文件夹正是它，不必再往别处问。

    栈上是「相册 + 分组」两帧，而 ``_current_folder`` 要给的是**分组底下那个目录** ——
    那是这一条存在的全部理由（它要跨过分组那一帧往下看）。
    """
    window._push_frame(AlbumFrame(window._library.root / "京都"))
    window._push_frame(GroupFrame(GroupRef("month", "2026-06", "2026 年 6 月", ())))
    assert window._current_folder() == window._library.root / "京都"


def test_the_finder_entry_opens_that_folder(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**网格页**走的是右键菜单对**相册**那一条 ``open_folder_in_finder`` —— 落到**里面**去。

    锁定的是「**不是** ``reveal_in_finder``」：``open -R`` 弹出来的访达停在当前这一层的
    **上一级**，而这个按钮的用处就是看一眼**这一层里面**摆着什么。大图页那一支是反过来的
    （见 ``test_the_finder_entry_reveals_the_photo_the_detail_page_is_showing``）。
    """
    opened: list[list[Path]] = []
    revealed: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "open_folder_in_finder",
        lambda paths: opened.append(list(paths)) or "已在访达中打开。",
    )
    monkeypatch.setattr(
        desktop,
        "reveal_in_finder",
        lambda paths: revealed.append(list(paths)) or "已在访达中显示。",
    )
    window._open_finder()
    assert opened == [[window._library.root]]
    assert revealed == []


def test_the_finder_entry_follows_the_folder_the_window_is_in(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进到相册里之后按它，开的是**当前这一层**那个文件夹，不是永远开着库根。"""
    opened: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "open_folder_in_finder",
        lambda paths: opened.append(list(paths)) or "已在访达中打开。",
    )
    album = window._library.root / "京都"
    window._push_frame(AlbumFrame(album))

    window._open_finder()

    assert opened == [[album]]


def test_the_qspace_entry_only_shows_up_when_it_is_installed(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**没装就不摆这一项**（与「访达」同一套判据：灰着点不动的条目只会让人以为坏了）。

    装了之后左半那一下打开的**仍然是访达** —— 新来的一项排在后面，不动默认那一个。
    """
    assert "QSpace" not in _menu_texts(window._external_button)

    _only(monkeypatch, qspace=QSPACE_APP)
    window._external_button.refresh()

    assert _menu_texts(window._external_button) == ["访达", "QSpace"]
    default = window._external_button.default_entry()
    assert default is not None and default.name == "访达"


def test_the_qspace_entry_opens_that_folder(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递出去的是**当前这一层那个文件夹**与 core 查到的那个应用包。"""
    _only(monkeypatch, qspace=QSPACE_APP)
    opened: list[tuple[list[Path], Path]] = []
    monkeypatch.setattr(
        desktop,
        "open_folder_in_qspace",
        lambda paths, app: opened.append((list(paths), app)) or "已在 QSpace 中打开。",
    )

    window._open_qspace()

    assert opened == [([window._library.root], QSPACE_APP)]


def test_the_qspace_entry_follows_the_folder_the_window_is_in(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进到相册里之后按它，开的同样是**当前这一层**，不是永远开着库根。"""
    _only(monkeypatch, qspace=QSPACE_APP)
    opened: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "open_folder_in_qspace",
        lambda paths, app: opened.append(list(paths)) or "已在 QSpace 中打开。",
    )
    album = window._library.root / "京都"
    window._push_frame(AlbumFrame(album))

    window._open_qspace()

    assert opened == [[album]]


def test_the_finder_entry_reveals_the_photo_the_detail_page_is_showing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """大图页上按「访达」：要的是**正在看的那一张**在所在目录里被选出来。

    与右键那条「在访达中显示」是同一个对象 —— 网格页回答「这一层里摆着什么」，大图页回答
    「我看的这一张是磁盘上哪一个」（ADR 0021 / 0014 决策 3）。递当前文件夹过去会让访达落到
    它**外面**那一层，用户看到的东西完全对不上。
    """
    revealed: list[list[Path]] = []
    opened: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "reveal_in_finder",
        lambda paths: revealed.append(list(paths)) or "已在访达中显示。",
    )
    monkeypatch.setattr(
        desktop,
        "open_folder_in_finder",
        lambda paths: opened.append(list(paths)) or "已在访达中打开。",
    )
    window._open_detail(window._model.index(0, 0))

    window._open_finder()

    assert revealed == [[window._library.root / "一张.jpg"]]
    assert opened == []


def test_the_qspace_entry_reveals_the_photo_the_detail_page_is_showing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """大图页上按「QSpace」递的是**那张照片自己的**地址 —— 它没有 ``-R`` 那一支可借。

    QSpace 认的是地址里那一样东西：给文件就打开所在的那一层并把它选出来，给文件夹才会落到
    里面去（见 ``ui.desktop.reveal_in_qspace``）。所以这里递的必须是照片本身。
    """
    _only(monkeypatch, qspace=QSPACE_APP)
    revealed: list[list[Path]] = []
    opened: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "reveal_in_qspace",
        lambda paths, app: revealed.append(list(paths)) or "已在 QSpace 中显示。",
    )
    monkeypatch.setattr(
        desktop,
        "open_folder_in_qspace",
        lambda paths, app: opened.append(list(paths)) or "已在 QSpace 中打开。",
    )
    window._open_detail(window._model.index(0, 0))

    window._open_qspace()

    assert revealed == [[window._library.root / "一张.jpg"]]
    assert opened == []


def test_the_qspace_entry_stays_off_the_calendar_page(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日历页上没有它 —— 那一页没有「当前这一层文件夹」可开（与访达同一条判据）。"""
    _only(monkeypatch, qspace=QSPACE_APP)

    window.set_browse_mode(BROWSE_MODE_CALENDAR)

    assert _menu_texts(window._external_button) == ["Google 日历"]


# ---- 大图页那一列图片应用（ADR 0067）-----------------------------------


def test_every_installed_app_is_listed_in_a_fixed_order(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装了哪几项就摆哪几项，**次序固定**：预览在最前，然后是访达、QSpace、ImageGlass。

    **左半那一下在大图页上因此是「预览」**（它开的是列表里第一个可用的）—— 大图页上最常用的
    就是「用图片软件看这一张」；网格页上图片应用不摆，左半仍是访达。
    """
    _only(monkeypatch, qspace=QSPACE_APP, preview=PREVIEW_APP, imageglass=IMAGEGLASS_APP)
    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()

    assert _menu_texts(window._external_button) == ["预览", "访达", "QSpace", "ImageGlass"]
    default = window._external_button.default_entry()
    assert default is not None and default.name == "预览"


def test_a_viewer_entry_only_shows_up_on_the_detail_page(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**网格页上不摆**：那里没有「正在看的那一张」，用某个软件打开**选区**是右键菜单那一组
    回答的问题（ADR 0067）。只装了预览时 ImageGlass 也不出现 —— 没装就不摆，与「访达」同一条。
    """
    _only(monkeypatch, preview=PREVIEW_APP)
    assert "预览" not in _menu_texts(window._external_button)
    grid_default = window._external_button.default_entry()
    assert grid_default is not None and grid_default.name == "访达"

    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()

    assert _menu_texts(window._external_button) == ["预览", "访达"]


def test_the_viewer_entry_opens_the_photo_the_detail_page_is_showing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递出去的是**正在看的那一张**、名册上那个应用包，以及写进文案的那个名字。

    递的**不是**它所在的文件夹 —— 用户要看的正是这一张；名字也**不是**给 ``open -a`` 的
    （它不认中文名），只是横幅里那一句要说的。
    """
    _only(monkeypatch, imageglass=IMAGEGLASS_APP)
    calls: list[tuple[list[Path], Path, str]] = []
    monkeypatch.setattr(
        desktop,
        "open_with_app",
        lambda paths, app, name: calls.append((list(paths), app, name)) or "已在ImageGlass中打开。",
    )
    window._open_detail(window._model.index(0, 0))

    window._open_in_viewer(apps.IMAGEGLASS)

    assert calls == [([window._library.root / "一张.jpg"], IMAGEGLASS_APP, "ImageGlass")]


def test_clicking_a_viewer_entry_reaches_the_same_action(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点菜单里那一项走的是**按 id 分派**那条路（与「访达」「QSpace」同一处）。

    不接这一下的话，下拉框里会摆出一项点下去什么都不会发生的条目。
    """
    _only(monkeypatch, preview=PREVIEW_APP)
    opened: list[list[Path]] = []
    monkeypatch.setattr(
        desktop,
        "open_with_app",
        lambda paths, app, name: opened.append(list(paths)) or "已在预览中打开。",
    )
    window._open_detail(window._model.index(0, 0))

    window._on_external_entry(_entry("preview", "预览", True))

    assert opened == [[window._library.root / "一张.jpg"]]


def test_an_unknown_entry_reaches_no_action(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """认不出来的 id 谁也不碰 —— 分派那一串的末尾**不能顺手把最后那一项当默认**。"""
    reached: list[str] = []
    monkeypatch.setattr(desktop, "open_with_app", lambda *a, **k: reached.append("viewer") or "x")
    monkeypatch.setattr(desktop, "reveal_in_finder", lambda *a: reached.append("finder") or "x")
    monkeypatch.setattr(
        desktop, "open_folder_in_finder", lambda *a: reached.append("finder") or "x"
    )

    window._on_external_entry(_entry("某个以后才有的应用", "以后", True))

    assert reached == []


# ---- 大图页那一列地图（ADR 0075）----------------------------------------

#: 天安门。**这是 WGS-84（EXIF 的口径）**，不是地图底图那份 GCJ-02。
EARTH = (39.908722, 116.397499)


def _cache_coordinates(window: MainWindow, latitude: float, longitude: float) -> None:
    """把坐标放进面板那份缓存 —— 大图页不展开面板时，地图那一项问的就是它。"""
    ref = window._model.photo_at(0)
    assert ref is not None
    window._cache_metadata(
        ref,
        MetadataReport(
            path=ref.path,
            groups=(),
            all_fields=(),
            source="exiftool",
            latitude=latitude,
            longitude=longitude,
        ),
    )


def test_the_map_entries_need_a_photo_with_coordinates(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网格页上没有「这一张的坐标」，没有坐标的照片也没有地点可开。"""
    _only(monkeypatch, maps=MAPS_APP, preview=PREVIEW_APP)
    assert "地图" not in _menu_texts(window._external_button)
    assert "Google 地图" not in _menu_texts(window._external_button)

    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()
    assert _menu_texts(window._external_button) == ["预览", "访达"]

    _cache_coordinates(window, *EARTH)
    window._external_button.refresh()
    assert _menu_texts(window._external_button) == ["预览", "地图", "Google 地图", "访达"]
    default = window._external_button.default_entry()
    assert default is not None and default.name == "预览", "地图不能把左半那一下从预览换走"


def test_the_coordinates_can_come_from_the_index(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不展开元数据面板时，坐标就在索引里（扫描那一遍存好的）—— 那条路也要认得出来。"""
    _only(monkeypatch)
    ref = window._model.photo_at(0)
    assert ref is not None
    monkeypatch.setattr(
        window._library,
        "cached_facets",
        lambda refs: {ref.path: PhotoFacets(latitude=EARTH[0], longitude=EARTH[1])},
    )
    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()

    assert "Google 地图" in _menu_texts(window._external_button)


def test_null_island_is_not_a_location(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """经纬度同时为 0 是「没定到 GPS」的占位写法，不能当成几内亚湾里的一个点。"""
    _only(monkeypatch, maps=MAPS_APP)
    _cache_coordinates(window, 0.0, 0.0)
    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()

    assert "地图" not in _menu_texts(window._external_button)
    assert "Google 地图" not in _menu_texts(window._external_button)


def test_google_maps_shows_even_without_the_native_app(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """浏览器永远在；系统「地图」不在就不摆那一项（与预览、QSpace 同一条判据）。"""
    _only(monkeypatch)
    _cache_coordinates(window, *EARTH)
    window._open_detail(window._model.index(0, 0))
    window._external_button.refresh()

    assert _menu_texts(window._external_button) == ["Google 地图", "访达"]


def test_clicking_the_maps_entry_passes_the_coordinates(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only(monkeypatch, maps=MAPS_APP)
    _cache_coordinates(window, *EARTH)
    window._open_detail(window._model.index(0, 0))
    calls: list[tuple[float, float]] = []
    monkeypatch.setattr(
        launch,
        "open_in_maps",
        lambda latitude, longitude: (
            calls.append((latitude, longitude)) or launch.OpenOutcome(where=launch.WHERE_APP)
        ),
    )

    window._on_external_entry(_entry("maps", "地图", True))

    assert calls == [EARTH]
    assert window._banner.text() == "已在地图中打开这张照片的拍摄位置。"


def test_clicking_the_google_maps_entry_says_it_opened_the_browser(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only(monkeypatch)
    _cache_coordinates(window, *EARTH)
    window._open_detail(window._model.index(0, 0))
    monkeypatch.setattr(
        launch,
        "open_in_google_maps",
        lambda latitude, longitude: launch.OpenOutcome(where=launch.WHERE_BROWSER),
    )

    window._on_external_entry(_entry("google_maps", "Google 地图", True))

    assert window._banner.text() == "已在浏览器中打开 Google 地图。"


def test_opening_maps_without_a_location_says_so(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按钮只在有坐标时摆，但界面与索引对不上时也要说一句，不能静默不动。"""
    monkeypatch.setattr(launch, "open_in_maps", lambda *a: pytest.fail("不该去开地图"))
    window._open_detail(window._model.index(0, 0))

    window._open_in_maps()

    assert "没有拍摄位置" in window._banner.text()


def test_button_appears_on_the_calendar_page(window: MainWindow) -> None:
    window._enter_calendar()
    window.show()
    assert window._external_button.isVisible() is window._owns_title_bar


def test_button_is_placed_left_of_the_search_field(window: MainWindow) -> None:
    window._enter_calendar()
    window.show()
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    button = window._external_button.geometry()
    field = window._search_field.geometry()
    assert button.left() + button.width() < field.left()
    assert field.left() - (button.left() + button.width()) == BUTTON_GAP


def test_title_text_gives_room_for_the_button(window: MainWindow) -> None:
    """按钮摆出来之后，标题文字要给它也让出来 —— 否则长相册名从它底下穿过去。

    右上角那一段 = 切换按钮 + 它与胶囊之间的缝 + 搜索胶囊 + 胶囊的右边距 + **放映按钮 + 它与
    外部按钮之间的缝** + **外部按钮 + 它与胶囊之间的缝**（ADR 0040 决策 5）。
    """
    window.show()
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    assert window._external_button.isVisible()
    assert window._play_button.isVisible()
    assert window._title_bar._right_reserved == (
        TOGGLE_EDGE
        + TOGGLE_GAP
        + SEARCH_FIELD_WIDTH
        + 2 * SEARCH_FIELD_MARGIN_RIGHT
        + BUTTON_WIDTH
        + BUTTON_GAP
        + PLAY_BUTTON_WIDTH
        + BUTTON_GAP
    )


def test_nothing_is_reserved_when_the_button_is_away(window: MainWindow) -> None:
    """外部按钮收起来时**它那份不让位**：空出来的宽度还给标题文字，长名字才摆得下。

    **切换按钮与放映按钮那份仍然占着**（它们不跟着外部应用走）—— 所以这里少的只是外部按钮
    那两样。
    """
    window.show()
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    window._external_button.setVisible(False)
    window._layout_title_bar()
    assert window._title_bar._right_reserved == (
        TOGGLE_EDGE
        + TOGGLE_GAP
        + SEARCH_FIELD_WIDTH
        + 2 * SEARCH_FIELD_MARGIN_RIGHT
        + PLAY_BUTTON_WIDTH
        + BUTTON_GAP
    )


def test_calendar_entry_carries_the_current_level_and_day(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打开的必须是**当前那一页**：哪一层、哪一天，一样都不差。"""
    seen: list[tuple[str, datetime.date]] = []

    def fake(level: str, day: datetime.date, **_kwargs: object) -> launch.OpenOutcome:
        seen.append((level, day))
        return launch.OpenOutcome(where=launch.WHERE_BROWSER)

    monkeypatch.setattr(launch, "open_google_calendar", fake)
    window._enter_calendar()
    window._calendar_page.set_level("week")
    _set_anchor(window, datetime.date(2026, 6, 29))
    window._open_google_calendar()
    wait_until(QApplication.instance(), lambda: bool(seen))
    assert seen == [("week", datetime.date(2026, 6, 29))]


def test_selected_day_wins_over_the_anchor(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**有选中就用选中的那一天** ——「年视图点 5 月 16 号 → 打开日历」看到的是那天。"""
    seen: list[tuple[str, datetime.date]] = []

    def fake(level: str, day: datetime.date, **_kwargs: object) -> launch.OpenOutcome:
        seen.append((level, day))
        return launch.OpenOutcome(where=launch.WHERE_APP)

    monkeypatch.setattr(launch, "open_google_calendar", fake)
    window._enter_calendar()
    _set_anchor(window, datetime.date(2026, 6, 1))
    window._calendar_page.set_selected(datetime.date(2026, 3, 12))
    window._open_google_calendar()
    wait_until(QApplication.instance(), lambda: bool(seen))
    assert seen == [("month", datetime.date(2026, 3, 12))]


def test_the_menu_hangs_off_the_right_edge_of_the_arrow_half() -> None:
    """弹出的位置：**右缘与那一格右缘对齐**，上沿离它 :data:`MENU_GAP` 点。

    **纯算术，不造窗口**（``menu_origin``）。右对齐是这里唯一会写错的地方 —— 左对齐
    的话菜单整个跑到搜索框那一侧，而用户看的是右半那个箭头。
    """
    anchor = QPoint(1000, 40)  # 右半那一格的屏幕坐标
    placed = menu_origin(anchor, MENU_EDGE, 133)

    assert placed.x() + 133 == anchor.x() + MENU_EDGE, "右缘没对齐"
    assert placed.y() == anchor.y() + MENU_GAP


def test_the_menu_is_narrower_than_the_button_it_hangs_off() -> None:
    """菜单比那一格宽时它仍然往左铺（右缘对齐的**方向**不能写反）。"""
    anchor = QPoint(1000, 40)
    placed = menu_origin(anchor, MENU_EDGE, 300)

    assert placed.x() < anchor.x()


def test_folder_icon_uses_the_real_one_when_the_system_has_it(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """系统里有访达图标就**读它那一份**，不是自己画的那个文件夹轮廓。

    **判据是画出来的颜色**：那份图标整张涂成 `#ff0000`，自画的轮廓是主题那几个灰阶。
    只断言「不是 None」的话，退回自画那一支照样绿 —— 那正是这条要拦的。
    （`QIcon` 按内容认格式，所以往一个 `.icns` 名字里写 PNG 是能读出来的。）
    """
    import gallery.core.external as external

    art = QImage(16, 16, QImage.Format.Format_ARGB32)
    art.fill(QColor(255, 0, 0))
    real = tmp_path / "Finder.icns"
    assert art.save(str(real))
    monkeypatch.setattr(external, "finder_icon", lambda: real)

    drawn = folder_icon().pixmap(QSize(ICON_EDGE, ICON_EDGE)).toImage()
    center = drawn.pixelColor(drawn.width() // 2, drawn.height() // 2)

    assert (center.red(), center.green(), center.blue()) == (255, 0, 0), (
        "画出来的不是那个图标文件 —— 退回自画那一支了"
    )


def test_folder_icon_falls_back_to_a_drawn_one(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取不到系统那一份（非 macOS）就退回自画的轮廓，**而不是一个空图标**。"""
    import gallery.core.external as external

    monkeypatch.setattr(external, "finder_icon", lambda: None)

    icon = folder_icon()
    assert not icon.isNull()


def test_an_app_icon_is_the_one_the_system_drew(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """系统画的那一张就**用它**，不是自己画的轮廓。

    **判据是画出来的颜色**：那份 PNG 整张涂成 ``#ff0000``，自画的轮廓是主题那几个灰阶 ——
    只断言「不是 None」的话，退回自画那一支照样绿。

    这一条同时守着那圈白边：要的是**系统画好的那张 PNG**（它自己套过遮罩），不是从应用包里
    那个 ``.icns`` 里拆出来的那一份（老式图标四角是不透明白色）。
    """
    art = QImage(16, 16, QImage.Format.Format_ARGB32)
    art.fill(QColor(255, 0, 0))
    from_system = tmp_path / "preview.png"
    assert art.save(str(from_system))
    png = from_system.read_bytes()
    monkeypatch.setattr(external_button, "icon_png", lambda app: png)

    rendered = icon_for(apps.PREVIEW).pixmap(QSize(ICON_EDGE, ICON_EDGE)).toImage()
    center = rendered.pixelColor(rendered.width() // 2, rendered.height() // 2)

    assert (center.red(), center.green(), center.blue()) == (255, 0, 0), (
        "画出来的不是系统那张 —— 退回自画那一支了"
    )


def test_an_app_icon_falls_back_to_a_drawn_one(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取不到系统那一张（没装、非 macOS、系统给不出图）就退回自画的轮廓，**而不是一个空图标**。"""
    monkeypatch.setattr(external_button, "icon_png", lambda app: None)

    icon = icon_for(apps.PREVIEW)

    assert not icon.isNull()


def test_both_halves_are_wide_enough(qt_app: QApplication) -> None:
    """两半都要**放得下里面的东西**再留出余量。

    两半都要留出余量：卡着图标宽度的话，图标与那个 V 都像贴在边上。
    """
    assert BUTTON_WIDTH - MENU_EDGE >= ICON_EDGE + 8, "左半给图标留的余量不够"
    assert MENU_EDGE >= 16, "右半放那个箭头太挤"


# ---- 与搜索框的候选列表是同一种东西 -------------------------------------------


def test_the_dropdown_is_the_same_colour_as_the_search_popup(
    qt_app: QApplication, window: MainWindow
) -> None:
    """下拉框与搜索框的候选列表**同一套颜色**：那四个角色都要一样。

    这三样是三个不同的控件，样式表都由 :mod:`gallery.ui.window.menu_style` 从**各自的**调色板生成 ——
    而 macOS 上 Qt 会按控件类型把高亮、正文色解析成不同的值（同一份调色板：``QMenu`` 的
    ``Highlight`` 是 ``#548fff``、``QTreeWidget`` 是 ``#084ad9``），于是同一套生成逻辑画出了
    两种蓝。固定那几档之后（ADR 0035 决策 2）它们只可能同色。

    **在测试里这一条是绿的、而真机上量得出差别**：测试默认跑在 ``offscreen`` 上（``fusion``），
    那里每一类控件解析出来的都一样。真正把住那条不变式的是
    ``tests/ui/test_theme.py::test_the_pushed_palette_pins_the_popup_roles`` ——
    它量的是「钉没固定」，与平台无关。
    """
    dropdown = window._external_button._menu.palette()
    candidate_list = window._search_field._popup.palette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.HighlightedText,
    ):
        assert candidate_list.color(role) == dropdown.color(role), f"{role.name} 两边不一样"
    window.close()
