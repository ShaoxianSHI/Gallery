"""在**当前这一层**里筛选（ADR 0040）在窗口里的整条链。

对位 :mod:`tests.ui.test_search_flow`（那是整库检索，ADR 0026）：同一个胶囊、同一套条件语法，
差别只在那些条件**管到哪儿**。所以这一份测试盯的是那些「只在筛选下才成立」的行为 ——
视图不换、卡片按子树收留、换视图条件跟着走、写操作让路、面包屑照旧报路径。

**元数据读取与地点反查都用假的**（理由与 ``test_search_flow.py`` 一样）：测试不依赖本机装了
exiftool，也不该因为机器上碰巧装了就把行为测偏。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    SEARCH_SCOPE_LEVEL,
    prepare_library,
)
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    SessionState,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.external_button import ICON_EDGE as EXTERNAL_ICON_EDGE  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels.breadcrumb import ChipButton  # noqa: E402
from gallery.ui.search_field import ICON_EDGE, PLACEHOLDER_FILTER, PLACEHOLDER_SEARCH  # noqa: E402

#: 测试库里每张照片的相机（按文件名对应）。没给的就是没写相机。
CAMERAS = {"海边-01.jpg": "Canon EOS R5", "山.jpg": "Sony A7"}

#: 每张照片的拍摄时间。**取「这个月」而不是写死的年份**：日历停在今天上，而
#: :meth:`CalendarPage.photo_count` 只数**当前那一段**里看得见的 —— 日期写死在别处，这一层就
#: 永远是空的，而症状（`photo_count() == 0`）与「日历没喂上照片」一模一样。
TODAY = datetime.date.today()

#: 每张照片拍在今天往前多少天（``#拍摄时间`` 那一族在日历测试里要用）。
DAYS_AGO: dict[str, int] = {"海边-01.jpg": 3, "海边-02.jpg": 3, "山.jpg": 40}


def _captured_string(name: str) -> str | None:
    """那张照片的 ``EXIF:DateTimeOriginal``（取**中午**：零点压在区间的边界上，
    ``>=`` 与 ``<`` 差一秒就会少算它一张 —— 那种失败与真正的缺陷长得一模一样）。"""
    days = DAYS_AGO.get(name)
    if days is None:
        return None
    moment = datetime.datetime.combine(TODAY - datetime.timedelta(days=days), datetime.time(12, 0))
    return moment.strftime("%Y:%m:%d %H:%M:%S")


@pytest.fixture(autouse=True)
def fake_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """把地点反查换成假的，免得联网、也不依赖本机下没下过城市数据。"""

    def place_for(
        self: object, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace:
        return ResolvedPlace(city="西安", region="陕西省", country="中国")

    monkeypatch.setattr(LazyPlaceResolver, "place_for", place_for)
    monkeypatch.setattr(LazyPlaceResolver, "ready", property(lambda _self: True))


@pytest.fixture(autouse=True)
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """把批量读取换成假的：只给相机与拍摄时间。"""

    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            name = Path(path).name
            raw: dict[str, object] = {}
            camera = CAMERAS.get(name)
            if camera:
                raw["EXIF:Model"] = camera
            when = _captured_string(name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


def _open(
    qt_app: QApplication,
    root: Path,
    session_base: Path,
    *,
    browse_mode: str | None = BROWSE_MODE_FLAT,
) -> MainWindow:
    """开一个窗口。

    ``browse_mode=None`` 表示**别碰那个库的偏好** —— 「重开这个库还记着上次的设置」那条用例
    要的就是这个：``set_library_ui`` 是**整张 ``[ui]`` 表覆盖写**，所以每次都写等于把上一次
    设的作用域擦掉，那条断言于是变成「配置从没被写进去过」，量不到任何东西。
    """
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    if browse_mode is not None:
        set_library_ui(root, browse_mode=browse_mode)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(1000, 700)
    window.show()
    qt_app.processEvents()
    return window


def _library(tmp_path: Path, make_photo: Callable[..., Path]) -> Path:
    """测试库：根下两张、一个空的 ``家庭``、``旅行/2024`` 下一张。

    ``家庭`` **故意是空的**：相册卡片那条判据要验的就是「一张都没有的子相册不该摆出来」，
    库里若只有非空的相册，那条断言与「卡片全都不摆」是同一件事，测不出差别。
    """
    root = tmp_path / "照片"
    for directory, names in (
        (root, ("海边-01.jpg", "海边-02.jpg")),
        (root / "旅行" / "2024", ("山.jpg",)),
    ):
        for name in names:
            make_photo(name, directory=directory)
    (root / "家庭").mkdir(parents=True, exist_ok=True)
    prepare_library(root)
    return root


def _settle(qt_app: QApplication, window: MainWindow) -> None:
    """转到「这一层排好了、后台那一遍也跑完了」为止。

    ``_filter_job`` 与 ``_search_job`` 一并等掉：切作用域时两者可能交替在途。
    """
    wait_for_idle(
        qt_app,
        window,
        also=("_search_job", "_filter_job", "_staged", "_index_job"),
        what="筛选、排序与索引",
    )


def _names(window: MainWindow) -> list[str]:
    return sorted(entry.name for entry in window._model.entries())


def _chips(window: MainWindow) -> list[str]:
    return [condition.chip_text for condition in window._conditions]


def _trail(window: MainWindow) -> list[str]:
    """面包屑那一行的全部文字，按摆放顺序（分隔符不算）。"""
    from PySide6.QtWidgets import QWidget

    parts: list[str] = []
    for widget in window._breadcrumb.findChildren(QWidget):
        text = widget.text().strip() if hasattr(widget, "text") else ""
        if text and text != "▸":
            parts.append(text)
    return parts


def _filter(
    qt_app: QApplication,
    window: MainWindow,
    text: str,
    *,
    scope: str = SEARCH_SCOPE_LEVEL,
) -> None:
    """切到筛选作用域，在胶囊里敲一串然后回车 —— 等价于用户做的事。

    **回车之后把光标从输入框里拿走**：界面在输入期间会让开会动文件的命令（那是三个输入框
    共用的一条规矩，ADR 0025）。于是「筛选时 ⌘V 是灰的」那条断言会在**任何**作用域下为真 ——
    测的其实是输入框的规矩，不是筛选的。焦点回到网格上才是用户做完一件事的样子。
    """
    window.set_search_scope(scope)
    qt_app.processEvents()
    window.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText(text)
    window._search_field.submit()
    qt_app.processEvents()
    window._grid.setFocus()
    qt_app.processEvents()
    _settle(qt_app, window)


# ---- 视图不换（这一族是筛选存在的理由）--------------------------------------------


def test_the_calendar_stays_a_calendar(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """**月视图里加一条条件，仍然停在月视图** —— 这是筛选与检索最要紧的区别。

    整库检索会接管这一层、换成网格页（ADR 0026 决策 1）；筛选不许。这一条如果反过来，用户在
    日历上按一次筛选就永远回不去日历了，而菜单里的勾还留着日历。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    assert window._stack.currentWidget() is window._calendar_page

    _filter(qt_app, window, "#相机 Canon")

    assert window._stack.currentWidget() is window._calendar_page, "筛选把日历页换成了网格页"
    window.close()


def test_the_calendar_only_shows_the_matching_photos(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历上只剩命中的那些照片 —— 由喂进分桶的那份引用决定，日历自己不改动任何绘制逻辑。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    assert window._calendar_page.photo_count() == 2, "前提不成立：三张里有两张在这个月"

    _filter(qt_app, window, "#相机 Canon")

    assert window._calendar_page.photo_count() == 1
    window.close()


def test_the_conditions_are_visible_on_the_calendar(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历那一页**看得见**自己加了哪几条条件 —— 用户原话：「看不到加了什么筛选条件」。

    **这一条是这个需求本身**：筛选时那一页仍然是日历（上面那条），而日历那一页占掉的就是网格页
    那条面包屑（ADR 0033 决策 5）—— 胶囊只摆在面包屑上，就等于「加了但不知道加了什么」。

    量的是**工具条上真的有那几枚**，而不是「模型里的条件列表非空」：后者在日历上照样成立，
    它量不到「用户看不见」。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    chips = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(chips) == 1, "日历工具条上没有条件胶囊（这是那个需求本身）"
    assert "相机" in chips[0].text(), chips[0].text()
    window.close()


def test_the_conditions_follow_the_user_onto_the_calendar(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """条件可以**在网格页上改**，改完切到日历页要立刻看见那一份。

    钉的是「判据是条件现在是什么样，而不是日历页此刻在不在线」—— 条件可能是在别处改的
    （例如从「全部照片」切过来之前），而日历那一页的工具条要跟着。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    chips = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(chips) == 1, "在网格页上加的条件，切到日历页不见了"
    window.close()


def test_removing_a_condition_from_the_calendar_works(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在日历工具条上点 ✕ 撤掉一条 —— 那几枚不是摆设。

    走的是**同一个** ``_on_condition_removed``（两个控件接到同一个处理函数上），所以
    「日历上撤掉了、网格页上还留着」这种不一致不可能发生。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)
    assert len(window._calendar_page._bar._chips.findChildren(ChipButton)) == 2

    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == ["城市：西安"], "日历上撤掉了，条件列表里还在"
    remaining = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(remaining) == 1, "日历工具条上还留着撤掉的那一枚"
    window.close()


def test_switching_the_calendar_level_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """`Y` / `M` / `W` 与筛选互不干扰：条件跟着走，切层不重算命中。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window._calendar_page.set_level("month")
    qt_app.processEvents()
    window._calendar_page.set_level("year")
    qt_app.processEvents()
    window._calendar_page.set_level("month")
    qt_app.processEvents()
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert window._calendar_page.photo_count() == 1, "切到年再切回月，筛选丢了"
    window.close()


def test_switching_the_browse_mode_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """换浏览方式**保留**条件（ADR 0040 决策 1）—— 整库检索在这一步会清掉它们。

    反过来的症状是「换一次视图筛选就没了」，而用户会以为是自己按了什么。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    assert _names(window) == ["海边-01.jpg"]

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "换浏览方式把条件清掉了"
    assert window._calendar_page.photo_count() == 1

    window.set_browse_mode(BROWSE_MODE_FLAT)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_entering_an_album_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """进相册保留条件 —— 它收窄的正是「当前这一层」，所以要跟着走进那一层。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "进相册把条件清掉了（那是整库检索的行为）"
    # 那一层里唯一一张是 Sony，Canon 在根下 —— 于是这里该是空的。
    assert _names(window) == []
    window.close()


def test_entering_a_group_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """进出分组同样保留条件 —— 分组也是「这一层」。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)
    _filter(qt_app, window, "jpg")

    window.go_up()  # 撤掉那一条条件
    _settle(qt_app, window)

    assert _chips(window) == []
    assert window._group_view is not None, "撤光条件就该回到原来那一组"
    window.close()


# ---- 相册卡片按子树收留（ADR 0040 决策 3）----------------------------------------


def test_only_the_albums_with_a_hit_stay(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """相册模式下：**不含命中的子相册卡片消失**，含命中的留下当导航。

    卡片不是照片，所以一条都不命中照片的子相册不该摆出来；而摆着一排点进去全是空的卡片，
    等于骗用户白点一次。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    before = {entry.name for entry in window._model.entries()}
    assert {"旅行", "家庭"} <= before

    _filter(qt_app, window, "#相机 Sony")

    # **这一层是库根**：命中的是 ``旅行/2024/山.jpg``，它在卡片那一层之下，所以照片本身
    # 不出现在这一层里 —— 这一层摆的是「卡片」。卡片留不留才是这一条要断的事。
    names = {entry.name for entry in window._model.entries()}
    assert "家庭" not in names, "这个子相册里一张都没有，卡片不该摆着"
    assert "旅行" in names, "命中的照片在它子树里，卡片要留着当导航"
    window.close()


def test_the_album_card_goes_away_when_its_hit_is_beneath_it(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """命中在**更深处**时，祖先那张卡片照样留着 —— 卡片的含义是它的整棵子树。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)

    window.enter_folder(root / "旅行")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Sony")

    # 在 ``旅行`` 这一层里，命中的是 ``旅行/2024/山.jpg`` —— 它在下一层里。
    names = {entry.name for entry in window._model.entries()}
    assert names == {"2024"}, f"只剩下一层的卡片不对：{names}"
    window.close()


def test_the_photos_of_this_level_survive_even_with_no_card(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """命中的照片就在**这一层**上时它照旧留着 —— 它不归属任何子相册卡片。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    names = {entry.name for entry in window._model.entries()}
    assert "海边-01.jpg" in names
    assert "海边-02.jpg" not in names, "它没写相机，不该命中"
    window.close()


# ---- 撤销与清空（与整库检索同一套心智）--------------------------------------------


def test_escape_still_removes_the_conditions_one_by_one(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """`Esc` 逐条撤条件，撤光之后这一层恢复完整 —— 一个动作都不多。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.go_up()
    _settle(qt_app, window)

    assert _chips(window) == []
    assert _names(window) == ["山.jpg", "海边-01.jpg", "海边-02.jpg"]
    window.close()


def test_clearing_every_condition_restores_the_level(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.clear_search_conditions()
    _settle(qt_app, window)

    assert _chips(window) == []
    assert len(_names(window)) == 3
    # 条件空了，菜单里那一条**整个消失**（与整库检索同一套，ADR 0014 决策 4）。
    assert window._clear_search_action.isVisible() is False
    window.close()


# ---- 面包屑（ADR 0040 决策 4）----------------------------------------------------


def test_the_breadcrumb_keeps_the_path_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选时面包屑**照旧报完整路径** —— 范围没变，藏掉路径是在说一件不存在的事。

    与 :func:`tests.ui.test_search_flow.test_the_breadcrumb_says_the_search_spans_the_library`
    互为反证：整库检索时报库名（范围真的变了），筛选时报路径（范围没变）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "旅行", "2024"]

    _filter(qt_app, window, "#相机 Sony")

    trail = _trail(window)
    assert trail[:3] == ["照片", "旅行", "2024"], f"路径段不见了：{trail}"
    assert any("筛选后（1）" in part for part in trail), trail
    window.close()


def test_the_breadcrumb_still_drops_the_group_while_searching(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """整库检索仍然只报库名 —— 两种作用域各写各的，一条都没被顺手改掉。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)

    _filter(qt_app, window, "jpg", scope="library")

    trail = _trail(window)
    assert trail[0] == "照片"
    assert "Canon" not in trail, f"整库检索不该报分组：{trail}"
    assert any("搜索结果" in part for part in trail), trail
    window.close()


def test_the_breadcrumb_keeps_the_group_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选时那一组**说的是真的** —— 它就是这一层的范围，该报。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Sony")

    trail = _trail(window)
    assert "Canon" in trail, f"筛选时该报当前那一组：{trail}"
    assert _names(window) == ["山.jpg"]
    window.close()


# ---- 切换按钮与作用域（ADR 0040 决策 5、6）----------------------------------------


def test_the_button_switches_the_scope_and_the_words(qt_app: QApplication, tmp_path: Path) -> None:
    """按钮一按：占位文字、勾、菜单文案一起换。**三处说同一句话**，否则用户会以为它们是两个设置。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_SEARCH
    assert window._scope_toggle.isChecked() is False

    window._scope_toggle.setChecked(True)
    qt_app.processEvents()

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
    assert window._scope_action.isChecked() is True
    assert window._search_action.text() == "筛选…"
    assert window._clear_search_action.text() == "清除筛选条件"
    window.close()


def test_the_icon_is_smaller_than_the_button(qt_app: QApplication, tmp_path: Path) -> None:
    """图标**不许画满整个按钮** —— 22 点画布上画 22 点的东西会顶到边框上。

    钉的是「图标比按钮小」这个关系，不是某个数：真要盯住的是图标画布与按钮边长之间留着
    余量，而两者都由 :data:`~gallery.ui.search_field.TOGGLE_ICON_EDGE` 与
    :data:`~gallery.ui.search_field.TOGGLE_EDGE` 推出来 —— 有人把图标又改回跟着按钮走时，
    这条会立刻变红。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    button_edge = window._scope_toggle.width()
    pixmap = window._scope_toggle.icon().pixmap(button_edge, button_edge)
    icon_edge = pixmap.width() / (pixmap.devicePixelRatio() or 1.0)

    assert icon_edge < button_edge, (
        f"图标 {icon_edge:g} 点画在 {button_edge:g} 点的按钮里 —— 顶到边框上了"
    )
    # 那一档不是随手定的：标题条上另外两个图标（放大镜、外部应用）都是 13。
    assert icon_edge == ICON_EDGE == EXTERNAL_ICON_EDGE
    window.close()


def test_selecting_the_button_fills_it_not_just_its_edge(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """选中时**整枚按钮**填满高亮色，不只是那一圈轮廓 —— 守护。

    **量像素，不量样式表字符串**：只断言 ``"background"`` 出现在样式表里量不到任何东西 ——
    颜色写对了而 Qt 没画（或者被别的规则盖掉）时它照样绿。这一条量的是「按钮中心那一块到底
    是什么颜色」。

    为什么非得填满：22 点的圆角小块上，1 像素的边只占边长的一成，而这一枚按钮**没有文字标签**
    可看 —— 唯一的「现在开着吗」信号就是那一片颜色。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    toggle = window._scope_toggle

    resting = _button_center_color(toggle)
    toggle.setChecked(True)
    qt_app.processEvents()
    selected = _button_center_color(toggle)

    assert resting is not None and selected is not None
    assert selected != resting, "选中态的中心还是原来那个底色 —— 整枚按钮没被填满"
    # 填的必须是高亮那一档，而它与菜单里选中项同源（``menu_style`` 的 ``:selected``）。
    highlight = QPalette(QApplication.instance().palette()).color(QPalette.ColorRole.Highlight)
    assert _close(selected, highlight, 0.06), (
        f"选中态填的是 {selected.name()}，要高亮的 {highlight.name()}（菜单选中项也是那一档）"
    )
    window.close()


def _button_center_color(button: object) -> QColor | None:
    """按钮**中心那一块**画出来的颜色。

    **中心而不是四角**：圆角按钮的四角是圆角外的透明区，量那里量到的是父控件的底色，于是
    「有没有填满」与「圆角画得对不对」两件事会搅在一起。中心是任何圆角半径下都必然被填到的那
    一块。

    用 ``render()`` 而不是屏幕像素：它会**强制**重画，所以量到的是此刻真的会被画出来的东西
    （对照 ``PhotoGrid`` 那条教训：屏幕上的陈旧像素在 ``grab()`` 里看不见）。

    ``render(None)`` 返回 ``None``（Qt 在没有目标矩形时不给画布），所以走
    :meth:`QWidget.grab` 之前先确认控件自己已经有一个尺寸 —— 离屏下没跑过布局时它是 640×480
    的默认值，中心那一块量的就不是这枚按钮了。
    """
    size = button.size()  # type: ignore[attr-defined]
    if size.width() < 2 or size.height() < 2:
        return None
    image = button.grab().toImage()  # type: ignore[attr-defined]
    if image.isNull() or image.width() < 2:
        return None
    return image.pixelColor(image.width() // 2, image.height() // 2)


def _close(left: QColor, right: QColor, tolerance: float) -> bool:
    """两个颜色差得不算远（``lightnessF`` 上的差，阈值写死一个 ``tolerance``）。"""
    return abs(left.lightnessF() - right.lightnessF()) <= tolerance and (
        abs(left.red() - right.red()) <= 2
        and abs(left.green() - right.green()) <= 2
        and abs(left.blue() - right.blue()) <= 2
    )


def test_the_menu_item_and_the_button_are_one_setting(qt_app: QApplication, tmp_path: Path) -> None:
    """从菜单切过去，按钮也要亮 —— 两条路走同一个入口，所以两处永远一致。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    window._scope_action.setChecked(True)
    qt_app.processEvents()

    assert window._search_scope == SEARCH_SCOPE_LEVEL
    assert window._scope_toggle.isChecked() is True
    assert window._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
    window.close()


def test_switching_the_scope_keeps_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """切作用域**不清条件**：同一个胶囊里的同一批条件，用户改的只是「它们管到哪儿」。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon", scope="library")
    assert _names(window) == ["海边-01.jpg"]

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "切作用域把条件清掉了"
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_the_scope_is_remembered_per_library(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """作用域是**库级**偏好（ADR 0040 决策 6）：重开这个库还该是筛选。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    qt_app.processEvents()
    window.close()

    again = _open(qt_app, root, tmp_path / "会话", browse_mode=None)
    _settle(qt_app, again)

    assert again._search_scope == SEARCH_SCOPE_LEVEL
    assert again._scope_toggle.isChecked() is True
    again.close()


def test_each_library_keeps_its_own_scope(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """A 库用筛选不该传染给 B 库 —— 与分组、排序那几项同一套（ADR 0038 决策 4）。"""
    first = _library(tmp_path / "A", make_photo)
    second = _library(tmp_path / "B", make_photo)
    window = _open(qt_app, first, tmp_path / "会话")
    _settle(qt_app, window)

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    qt_app.processEvents()
    window.open_library(second)
    _settle(qt_app, window)

    assert window._search_scope == "library", "B 库被 A 库的用法传染了"
    window.close()


# ---- 层标识（ADR 0040 决策 2）------------------------------------------------------


def test_a_filtered_level_is_not_a_search_level(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """「加一条条件」要算**换了层** —— 否则焦点恢复会往一个已被滤掉的行上跳。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    before = window._current_level_key()

    _filter(qt_app, window, "#相机 Canon")
    filtered = window._current_level_key()

    assert filtered != before
    _filter(qt_app, window, "#相机 Canon", scope="library")
    searched = window._current_level_key()

    assert searched != filtered, "筛选层与整库检索层撞成了同一个标识"
    window.close()


# ---- 筛选时让路的命令（ADR 0040 决策 7）--------------------------------------------


@pytest.fixture
def pasteable_clipboard(qt_app: QApplication, tmp_path: Path) -> Iterator[None]:
    """剪贴板里**真有文件**。

    ``_can_paste`` 会真去读一次剪贴板（读一次约 1 微秒），所以空剪贴板下 ⌘V 本来就一直是灰的
    —— 那会让「筛选把它关掉了」这条断言变成**空转**：前后都灰，红了也看不出是哪一句的功劳。
    """
    from gallery.ui import desktop

    desktop.copy_files_to_clipboard([tmp_path / "待粘.jpg"])
    qt_app.processEvents()
    try:
        yield
    finally:
        QGuiApplication.clipboard().clear()


def test_writing_commands_are_withheld_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo, pasteable_clipboard: None
) -> None:
    """筛选生效时 ⌘V 与 ⇧⌘N **禁掉**：这一层显示的不是全部内容，粘进去的可能一条都不出现。

    而且它们是会动用户文件的那一类（红线 1），不该在用户看不清全貌时发生。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    assert window._paste_action.isEnabled() is True, "前提不成立：⌘V 本来就按不动"
    assert window._new_album_action.isEnabled() is True

    _filter(qt_app, window, "#相机 Canon")

    assert window._paste_action.isEnabled() is False, "筛选时还让粘贴，用户粘进去的照片不会显示"
    assert window._new_album_action.isEnabled() is False
    window.close()


def test_writing_commands_come_back_when_the_filter_is_cleared(
    qt_app: QApplication, tmp_path: Path, make_photo, pasteable_clipboard: None
) -> None:
    """撤光条件后那两条**立刻回来** —— 不许留在关着的状态上（用户看不出为什么按不动）。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.clear_search_conditions()
    _settle(qt_app, window)

    assert window._paste_action.isEnabled() is True
    assert window._new_album_action.isEnabled() is True
    window.close()


# ---- 空层与索引没建（两个假阴性陷阱，ADR 0040 决策 3）-----------------------------


def test_filtering_before_the_index_is_built_still_finds_things(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """用户看到空结果就再也不会筛选第二次了 —— 与整库检索同一道闸（ADR 0026 决策 5）。

    文件表一行都没有时一条都命中不了，而用户看到的会是「当前这一层里没有符合这些条件的照片」
    —— 那不是答案，是索引还没建。那一遍跑完要自己把这次筛选补上。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    wait_for_idle(qt_app, window, also=("_search_job", "_filter_job", "_staged"), what="扫描")

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    window.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText("#相机 Canon")
    window._search_field.submit()
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_an_empty_filtered_level_says_which_layer_is_empty(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """空层提示说的是「**这一层**里没有」而不是「这些条件下没有」—— 后者在筛选下是骗人的。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    assert _names(window) == []
    assert "这一层" in window._banner.text(), window._banner.text()
    window.close()


# ---- 监听器（ADR 0040 决策 6）------------------------------------------------------


def test_a_new_photo_shows_up_only_if_it_matches(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选中来了新照片：命中的留下，不命中的不进这一层。

    走的是「重跑一遍」那条路（与整库检索同一处），所以这条量的是**结果对不对**，不是增量
    逻辑本身 —— 增量分支本来就不该存在（另写一份正是 ADR 0026 决策 7 点名的 bug 来源）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    assert _names(window) == ["海边-01.jpg"]

    make_photo("新来的.jpg", directory=root / "旅行" / "2024")
    window._on_watch_changed(_added_change(root / "旅行" / "2024" / "新来的.jpg"))
    _settle(qt_app, window)

    assert _names(window) == ["海边-01.jpg"], "没写相机的新照片不该出现在 Canon 筛选里"
    window.close()


def _added_change(path: Path) -> tuple:
    """一次「新建了文件」的监听变化（测试自己造，不起真的监听器）。"""
    from gallery.core.library.watcher import ChangeKind, LibraryChange

    change = LibraryChange(kind=ChangeKind.ADDED, path=path)
    return (change,)


def test_the_condition_chips_stay_clickable_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """面包屑右端那几枚胶囊**照旧能点**（改 / 撤掉），否则筛选就没有出口了。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == []
    assert len(_names(window)) == 3
    # **不再显示**（平铺模式下没有条件时面包屑整条收起来，ADR 0026 决策 9）。
    # 断的是「可见」而不是「子控件清空了」：``set_path`` 在隐藏时不会被调，面包屑里留着上一轮
    # 那几枚控件 —— 它整条不可见，留着无害，而拿 ``findChildren`` 去断会量到一件与用户无关的事。
    assert window._breadcrumb.isVisible() is False
    window.close()
