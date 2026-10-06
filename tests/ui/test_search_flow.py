"""搜索在窗口里的整条链（ADR 0026）。

覆盖用户真正会经历的那条路：在标题栏右上角那个胶囊里敲一个词 → 回车 → 条件落到
面包屑那一行的最右侧 → 网格只剩命中的照片 → 再敲一条（收窄）→ 点 ✕ 撤掉一条 →
`Esc` 逐条撤销 → 清空之后回到原来那一层。

**元数据读取与地点反查都用假的**（理由与 ``test_group_flow.py`` 一样）：测试不依赖本机
装了 exiftool，也不该因为机器上碰巧装了就把行为测偏。

有一条是这条链最容易出错的局面，单独测：**第一次搜索时索引还没建**。用户看到空结果
就再也不会搜索第二次了 —— 所以那一遍要自己跑完并且把这次检索补上。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMenu,
    QToolButton,
    QWidget,
)
from waiting import wait_for_idle  # noqa: E402

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.menu_style import POPUP_LIST_INSET  # noqa: E402
from gallery.ui.panels.breadcrumb import ChipButton  # noqa: E402
from gallery.ui.search_field import (  # noqa: E402
    HEIGHT,
    MARGIN_RIGHT,
    WIDTH,
    SearchField,
    magnifier,
)
from gallery.ui.theme import rgba  # noqa: E402
from gallery.ui.title_bar import EXTRA_PADDING as TITLE_BAR_EXTRA  # noqa: E402

#: 测试库里每张照片的相机（按文件名对应）。没给的就是没写相机。
CAMERAS = {"海边-01.jpg": "Canon EOS R5", "山.jpg": "Sony A7"}

#: 每张照片的 GPS。相机原图通常只写坐标、不写 IPTC 地点。
GPS: dict[str, dict[str, str]] = {
    "海边-01.jpg": {
        "EXIF:GPSLatitude": "34.215",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "108.965",
        "EXIF:GPSLongitudeRef": "East",
    },
}


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
    """把批量读取换成假的：只给相机与 GPS。"""

    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            name = Path(path).name
            raw: dict[str, object] = dict(GPS.get(name, {}))
            camera = CAMERAS.get(name)
            if camera:
                raw["EXIF:Model"] = camera
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


def _open(
    qt_app: QApplication, root: Path, session_base: Path, *, browse_mode: str = BROWSE_MODE_FLAT
) -> MainWindow:
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(1000, 700)
    window.show()
    qt_app.processEvents()
    return window


def _library(tmp_path: Path, make_photo: Callable[..., Path]) -> Path:
    root = tmp_path / "照片"
    for directory, names in (
        (root, ("海边-01.jpg", "海边-02.jpg")),
        (root / "旅行" / "2024", ("山.jpg",)),
    ):
        for name in names:
            make_photo(name, directory=directory)
    prepare_library(root)
    return root


def _settle(qt_app: QApplication, window: MainWindow, *, index: bool = True) -> None:
    """转到「这一层排好了、后台那一遍也跑完了」为止。

    ``index=False`` 用于**故意不等索引那一遍**的用例：它们要看的正是
    「索引还没建好时用户提交了条件」那条路。
    """
    # 除了 :func:`waiting.BACKGROUND_JOBS` 那三个，检索这一遍（``_search_job``）
    # 与尚未提交的暂存选择（``_staged``）也要收工 —— 它们同样按「是 ``None``
    # 算收工」判，所以一并交给 :func:`waiting.wait_for_idle`。不成立**直接抛**。
    also = ("_search_job", "_staged", "_index_job") if index else ("_search_job", "_staged")
    wait_for_idle(qt_app, window, also=also, what="检索、排序与索引")


def _names(window: MainWindow) -> list[str]:
    return sorted(entry.name for entry in window._model.entries())


def _chips(window: MainWindow) -> list[str]:
    return [condition.chip_text for condition in window._conditions]


def _search(qt_app: QApplication, window: MainWindow, text: str) -> None:
    """在搜索框里敲一串然后回车 —— 等价于用户做的事（⌘F → 输入 → 回车）。"""
    window.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText(text)
    window._search_field.submit()
    _settle(qt_app, window)


# ---- 胶囊本身 ---------------------------------------------------------------------


# ---- 胶囊长什么样 -------------------------------------------------------------------


def test_the_capsule_paints_its_own_edge(qt_app: QApplication) -> None:
    """胶囊得**看得见**：边框要真的画出来。

    样式表挂在普通 ``QWidget`` 上时，底色与边框**一条都不会画** —— Qt 只给 ``QFrame``
    那一族自动画样式背景（实测：胶囊整个透明，在白色窗口上等于不存在）。

    **看得见靠的是那一圈边框，不是底色**：底色与窗口外面同一个（ADR 0035 决策 1 ——
    原来那一档灰恰好是 macOS 系统窗口的颜色，用户以为是 Qt 原生的）。所以这里断言的是
    「边框与窗外不一样」，而不是「里面那一块与窗外不一样」。
    """
    window = QWidget()
    window.resize(400, 60)
    window.setStyleSheet("background: white;")
    field = SearchField(window)
    field.setGeometry(20, 10, WIDTH, HEIGHT)
    window.show()
    qt_app.processEvents()

    image = window.grab().toImage()
    border = image.pixelColor(20, 20)
    inside = image.pixelColor(120, 20)
    outside = image.pixelColor(380, 20)
    assert border != inside, "胶囊的边框没画出来"
    assert border != outside, "边框与窗口底色一样，胶囊等于看不见"
    window.close()


def test_the_capsule_has_round_ends(qt_app: QApplication) -> None:
    """两个角是**圆的**：最上一行两端那几个像素不该有东西，否则是个方框。"""
    window = QWidget()
    window.resize(400, 60)
    window.setStyleSheet("background: white;")
    field = SearchField(window)
    field.setGeometry(20, 10, WIDTH, HEIGHT)
    window.show()
    qt_app.processEvents()

    image = window.grab().toImage()
    painted = [x for x in range(20, 20 + WIDTH) if image.pixelColor(x, 10).name() != "#ffffff"]

    assert painted, "胶囊顶边整条都没画"
    assert min(painted) > 20, "左端是方的"
    assert max(painted) < 20 + WIDTH - 1, "右端是方的"
    window.close()


def _content_top(window: MainWindow) -> int:
    """内容区**真正**从窗口第几行开始。

    中央控件自己的矩形顶边是安全区让出来的那一段，而标题栏比它高出的那一点是靠
    ``_stack`` 的上边距腾出来的（ADR 0026 决策 8）—— 只看中央控件的顶边会以为
    标题栏仍然是那么高。
    """
    return window.centralWidget().geometry().top() + window._stack.contentsMargins().top()


def test_the_capsule_stays_inside_the_title_bar_strip(qt_app: QApplication, tmp_path: Path) -> None:
    """胶囊不许压到内容上。

    标题栏那一条比安全区高（``title_bar.EXTRA_PADDING``），高出来的那一段**必须
    同时把内容区也往下推**—— 不推就是拿胶囊的底色去盖照片区的顶边。这条曾经真的
    发生过，是被 ``test_innermost_layer_has_no_border`` 抓到的（它量到大图区上边缘
    出现了几百个非底色像素）。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    field = window._search_field
    content = _content_top(window)
    assert field.geometry().bottom() <= content, (
        f"胶囊一直伸到第 {field.geometry().bottom()} 行，内容从第 {content} 行开始"
    )
    window.close()


def test_the_title_bar_is_taller_than_the_safe_area(qt_app: QApplication, tmp_path: Path) -> None:
    """标题栏要**比安全区高**，胶囊的上边框才离窗口顶边有距离。

    28 点的安全区里塞一个 22 点的胶囊，上下各剩 3 点 —— 看上去像浮在边沿上。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    safe_area = window.centralWidget().geometry().top()
    strip = window._title_bar.geometry().height()
    assert strip == safe_area + TITLE_BAR_EXTRA, (
        f"标题栏 {strip} 点 = 安全区 {safe_area} + {TITLE_BAR_EXTRA}"
    )
    # 胶囊的上边框离窗口顶边：整除会少一点，所以按「至少一半的余量」判。
    assert window._search_field.geometry().top() >= strip // 2 - window._search_field.height() // 2
    window.close()


def test_the_capsule_keeps_a_wide_gap_to_the_window_edge(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**右上角那一块**离窗口右边要留够 —— 12 点太贴边，看着像被窗口挤出去。

    那一块现在有两样东西（ADR 0040 决策 5）：胶囊与它右边那枚切换按钮，而
    ``MARGIN_RIGHT`` 量的正是**整块**的右边距 —— 胶囊自己被推到左边去了，所以量它自己会量到一个
    比留白大得多的数（按钮宽 + 缝 + 右边距），而那不是这条要盯的东西。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.resize(1000, 600)
    window.show()
    qt_app.processEvents()

    block_right = max(
        window._search_field.geometry().right(),
        window._scope_toggle.geometry().right(),
    )
    assert MARGIN_RIGHT >= 16, f"右边距只有 {MARGIN_RIGHT} 点"
    assert window.width() - 1 - block_right == MARGIN_RIGHT
    window.close()


def test_the_toggle_sits_between_the_capsule_and_the_window_edge(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """切换按钮在**胶囊与窗口右缘之间**，与胶囊同高、同基线。

    **高与基线都要一样**：各算各的话两者会歪开一两个点（胶囊为了字的中线被往下挪了
    ``TEXT_ALIGN_DROP``），而这正是胶囊当初与「外部应用」按钮之间踩过的那个坑。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.resize(1000, 600)
    window.show()
    qt_app.processEvents()

    field = window._search_field.geometry()
    toggle = window._scope_toggle.geometry()

    assert toggle.left() >= field.right() + 1, "按钮压在胶囊上，或跑到它左边去了"
    assert toggle.top() == field.top(), f"按钮在第 {toggle.top()} 行，胶囊在第 {field.top()} 行"
    assert toggle.height() == field.height()
    assert toggle.width() == toggle.height(), "22×22 正方形里只放一个图标"
    window.close()


def test_the_toggle_is_hidden_when_the_title_bar_is_not_ours(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """那一行不归我们时（别的平台）按钮也不出现 —— 它就摆在那一行上。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    assert window._scope_toggle.isVisible() is window._owns_title_bar
    window.close()


def test_the_rightmost_condition_lines_up_with_the_capsule(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """面包屑右端那几枚条件胶囊的**右缘**要与搜索胶囊的右缘在同一条竖线上。

    两者是同一列东西，只是分两行摆着（ADR 0026 决策 4：条件按录入顺序从右端排开）。
    右缘各说各话时那条竖线是折的，看着像两样互不相干的控件。

    **钉的是「对齐」而不是某个数**：真要盯住的是最右那一枚的右缘与胶囊的右缘相等，
    换句话说两边都得由 ``search_field.MARGIN_RIGHT`` 那一个数推出来 —— 两处各写一份
    留白时这条会立刻变红。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相机 Canon")

    chips = window._breadcrumb.findChildren(ChipButton)
    assert len(chips) == 2, f"两枚条件胶囊没摆出来：{len(chips)}"
    crumb = window._breadcrumb
    origin = crumb.mapTo(window, crumb.rect().topLeft()).x()
    rightmost = max(origin + chip.geometry().right() for chip in chips)
    capsule_right = window._search_field.geometry().right()

    assert rightmost == capsule_right, (
        f"最右那枚胶囊的右缘在 {rightmost}，搜索胶囊的在 {capsule_right}"
    )
    window.close()


def test_hovering_a_candidate_highlights_it(qt_app: QApplication, tmp_path: Path) -> None:
    """鼠标移到哪一行，那一行就高亮。

    **高亮落在「当前项」上**：挂了样式表之后 Qt 不再按 ``State_MouseOver`` 自己去挑高亮
    色（与 ``menu_style.style_sheet`` 里那条菜单的注释是同一件事），所以「当前项」那一份
    高亮得由我们自己点亮 —— 而点亮它的只能是悬停本身，否则鼠标移上去毫无反应。

    接在**视口**上：滚动区域的视口是 Qt 自己的控件，落在它身上的事件不会再回到视图的
    ``mouseMoveEvent``（实测那次移动完全没反应）。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    field = window._search_field
    field.focus_search()
    qt_app.processEvents()
    field._edit.setText("#")
    qt_app.processEvents()
    popup = field._popup
    assert popup.currentItem() is popup.topLevelItem(0)

    _move_mouse_over(popup, row=3)

    assert popup.currentItem() is popup.topLevelItem(3), "鼠标移过去了，那一行却不是当前的"
    window.close()


def _move_mouse_over(popup: object, *, row: int) -> None:
    """把一次 ``MouseMove`` 发到候选列表的视口上（等价于鼠标移到第 ``row`` 行）。"""
    center = popup.visualItemRect(popup.topLevelItem(row)).center()  # type: ignore[attr-defined]
    QApplication.sendEvent(
        popup.viewport(),  # type: ignore[attr-defined]
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(center),
            QPointF(center),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_the_candidate_list_takes_its_colors_from_the_menu_side(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """候选列表与右键菜单**同一套颜色**：四个角色都要一样。

    两边各自派生一遍的话，早晚会差一档 —— 而「搜索框里那个下拉框比菜单深一点」
    正是那种没人说得清、却一眼看得出的差别（ADR 0024：菜单怎么画是同一层的事）。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    menu = QMenu(window)
    menu.addAction("一项")
    window._dressed_menu(menu)  # 与右键菜单完全相同的那一套：调色板 + 样式表
    qt_app.processEvents()
    window._search_field.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText("#")
    qt_app.processEvents()

    menu_palette = menu.palette()
    list_palette = window._search_field._popup.palette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.HighlightedText,
    ):
        assert list_palette.color(role) == menu_palette.color(role), f"{role.name} 两边不一样"
    # 高亮色真的进了候选列表的样式表（不是「调色板一样但样式表没用它」）。
    assert (
        rgba(menu_palette.color(QPalette.ColorRole.Highlight))
        in window._search_field._popup.styleSheet()
    )
    window.close()


@pytest.mark.parametrize("ratio", [1.0, 2.0])
def test_the_magnifier_fits_inside_its_canvas(qt_app: QApplication, ratio: float) -> None:
    """放大镜要完整，还要**在画布之内**。

    两条曾经踩过的坑各钉一条：

    - 笔是画在路径**两侧**的，画到画布边上会被裁掉（手柄的尖少一截）；
    - ``QPainter`` 按设备像素比缩放它拿到的坐标，拿设备像素画上去在 Retina 上就是
      放大一倍 —— 13 点的画布里只剩手柄的尖（不透明像素只剩右下角一个）。

    ``ratio=2`` 那一条正是 Retina 屏上的样子。
    """
    pixmap = magnifier(theme.current_theme(), ratio)
    image = pixmap.toImage()
    lit = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 8
    ]

    assert len(lit) > 20, "放大镜几乎没画出来（按设备像素画进画布就是这样）"
    xs = [x for x, _ in lit]
    ys = [y for _, y in lit]
    assert min(xs) >= 1 and min(ys) >= 1, "图形顶到了画布的第一行 / 第一列"
    assert max(xs) <= image.width() - 2, "图形被右边缘裁掉了"
    assert max(ys) <= image.height() - 2, "图形被下边缘裁掉了"


def test_the_field_list_appears_after_a_hash(qt_app: QApplication, tmp_path: Path) -> None:
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    window._search_field._edit.setText("#")

    assert window._search_field.popup_visible()
    assert window._search_field.visible_fields()[0] == "#文件名"
    assert "#相册" in window._search_field.visible_fields()
    window.close()


def test_typing_a_field_name_narrows_the_list(qt_app: QApplication, tmp_path: Path) -> None:
    """边打边筛：敲「相机」那两个字就该看见相机被选中，而不是整个列表。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    window._search_field._edit.setText("#相机")

    assert window._search_field.visible_fields() == ["#相机"]
    window.close()


def _first_ink(
    image: object, box: tuple[int, int, int, int], scale: float, background: float
) -> int:
    """``box`` = (左, 上, 右, 下) 内第一个「不是底色」的像素的 x（**逻辑**坐标）。

    **像素级**的量法，而且是唯一靠得住的一种：控件的 ``mapTo`` / ``contentsRect``
    量得到「行矩形在哪」，量不到「Qt 实际把那个字画在哪」—— 中间隔着行内边距、
    委托自己的边距与字形左侧的边距（实测合起来十点上下，量控件一律量不到）。
    """
    left, top, right, bottom = box
    for x in range(int(left), int(right)):
        for y in range(int(top), int(bottom)):
            pixel = image.pixelColor(round(x * scale), round(y * scale)).lightnessF()  # type: ignore[attr-defined]
            if abs(pixel - background) > 0.06:
                return x
    return -1


def _hash_columns(field: SearchField, host: QWidget) -> tuple[int, int]:
    """输入框里那个 ``#`` 与候选列表里那个 ``#`` 各自的横坐标（**宿主**逻辑坐标）。

    量的是**画出来的像素**，不是控件的矩形：Qt 画 ``::item`` 的文字之前自己还会让开
    一段（委托的边距），QSS 的 ``padding`` 是在那之后才叠上去的 —— 只看控件位置，
    永远看不到这段（这一条当初就栽在这儿：控件量出来是齐的，界面上仍然错开八点）。

    列表量的是**没被选中**的那一行：选中行整行铺着高亮色，它比文字更靠左，量它
    量到的是高亮的圆角。深浅色都成立，所以底色参考点取同一行右侧的空白处。
    """
    image = host.grab().toImage()
    scale = image.devicePixelRatio() or 1.0
    popup = field._popup
    box = popup.geometry()
    # ``visualItemRect`` 给的是**视口**坐标，换到窗口坐标要加视口的左上角而不是列表的 ——
    # 两者差着一整圈边框与内边距（:data:`POPUP_LIST_INSET`）。加错了量到的是上一行
    # 高亮的下沿（实测偏七点，量出来「差 20」，真实差 2）。
    view = popup.viewport().mapTo(host, QPointF(0, 0))
    row = popup.visualItemRect(popup.topLevelItem(1)).translated(view.toPoint())
    background = image.pixelColor(  # type: ignore[attr-defined]
        round((box.right() - 6) * scale), round((row.top() + 4) * scale)
    ).lightnessF()
    listed = _first_ink(
        image,
        (view.x() + 1, row.top() + 4, box.right() - 4, row.bottom() - 3),
        scale,
        background,
    )
    typed = _first_ink(
        image,
        (field.x() + 22, field.y() + 6, field.x() + 80, field.y() + 18),
        scale,
        background,
    )
    return typed, listed


def _open_candidates(qt_app: QApplication, tmp_path: Path, text: str) -> MainWindow:
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    window._search_field.focus_search()
    window._search_field._edit.setText(text)
    qt_app.processEvents()
    return window


def test_the_candidate_hash_lines_up_with_the_typed_one(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """列表里那个 ``#`` 要与输入框里正打着的那个落在**同一条竖线**上。

    胶囊左边那一段是放大镜，文本从它右边起算；而列表里那个 ``#`` 前面还要再让开
    边框、内边距与行内边距。**这一条是像素对拍**（见 :func:`_hash_columns` 为什么要
    量像素）—— 控件位置量出来是齐的，界面上仍然可以差着八点，那八点就出在这中间。

    :data:`~gallery.ui.search_field._ITEM_TEXT_BASELINE` 那个量出来的常数由这条盯着：
    哪天真变了，先变红的是它。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    assert window._search_field.popup_visible()

    typed, listed = _hash_columns(window._search_field, window)

    assert listed > 0 and typed > 0, "两个「#」有一个没量到（列表只有一行时换一行）"
    assert abs(listed - typed) <= 1, (
        f"两个「#」错开 {listed - typed} 点（输入框 {typed}，列表 {listed}）"
    )
    window.close()


def test_the_candidate_list_does_not_stick_out_past_the_capsule(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """列表的右沿**不许越过胶囊的右沿**。

    宽度曾经是 :data:`WIDTH` 而左沿又照着胶囊往右挪了十八点，于是右边整整探出去
    十八点 —— 一份挂在输入框底下、比输入框还宽的列表看着是漏的。

    现在左沿让开多少、宽度就收多少，于是右沿**正正**压在胶囊右沿上（不是「不超过」，
    是「一样宽到同一处」）。只断言「不超过」的话，把列表缩成一条窄缝也能过。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    field = window._search_field
    popup = field._popup

    assert popup.geometry().right() == field.geometry().right(), (
        f"列表右沿 {popup.geometry().right()}，胶囊右沿 {field.geometry().right()}"
        f"（左沿 {popup.geometry().left()}，胶囊左沿 {field.x()}，宽 {popup.width()}）"
    )
    window.close()


def test_the_candidate_list_follows_the_capsule_layout(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """胶囊左边多出一截，列表的左沿要**跟着挪那么多**。

    这条盯的是「让开的那一段是**量出来的**，不是写死的常数」（见
    ``SearchField._popup_left``）：写死的话这里不会红，而换过放大镜之后两个 ``#``
    就对不齐了 —— 那一档 8 点内边距不变，挪的是框，框挪多少全看输入框那一侧有多宽。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    field = window._search_field
    before = field._popup.geometry().left()

    field._icon.setFixedWidth(field._icon.width() + 12)
    field.layout().activate()
    field._edit.setText("#相机")
    qt_app.processEvents()

    after = field._popup.geometry().left()
    assert after - before == 12, (
        f"放大镜宽了 12 点，列表左沿只挪了 {after - before} 点 —— 让开的那一段写死了？"
    )
    window.close()


def test_the_candidate_list_shows_only_the_field_names(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """候选只有一列：``#相机``，右边的灰字提示已经去掉。

    那一列其实**从来没被画出来过**：``QTreeWidgetItem`` 带着两列的值构造、再 ``add``，
    树并不会长出第 2 列，于是值一直搁在 item 自己身上，界面只是每行右边一片空白 ——
    留着它等于留一片空白。核心里那份 ``hint`` 仍然在：错误横幅还要用它给例子。

    **断言的是 item 自己有几列，不是树有几列**：树上永远只有 1 列（正因为如此那一列
    才没画出来），断言它等于什么都没盯着 —— 把 ``field.hint`` 加回来，这条仍然是绿的。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    popup = window._search_field._popup

    widths = {popup.topLevelItem(row).columnCount() for row in range(popup.topLevelItemCount())}
    assert widths == {1}, f"候选每行仍带着多于一列的数据：列数有 {sorted(widths)}，提示那列又回来了"
    assert popup.topLevelItem(0).text(0).startswith("#")
    window.close()


def test_the_candidate_list_is_only_as_tall_as_its_rows(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """列表底下不许留一截空白。

    高度曾经是「行数 × 26」，而那一档字号下真正的行高是 23 —— 每行多算三点，十七条
    就在最下面攒出四十来点空白（实测 450 高、内容只有 391）。那一块不是留白，是算错的。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    popup = window._search_field._popup
    rows = popup.topLevelItemCount()
    row_height = popup.sizeHintForRow(0)
    expected = rows * row_height + POPUP_LIST_INSET * 2

    assert popup.height() == expected, (
        f"列表高 {popup.height()}，{rows} 行内容该是 {expected} —— "
        f"底下多出（或少了）{popup.height() - expected} 点"
    )
    window.close()


def test_the_candidate_list_still_fits_a_short_window(qt_app: QApplication, tmp_path: Path) -> None:
    """窗口矮到放不下时，列表**不许伸到窗口外面去** —— 这条不能被上面那条挤掉。

    高度是「按行高算」与「夹在窗口以内」里取小的那个，所以这一条才成立。
    """
    window = _open_candidates(qt_app, tmp_path, "#")
    window.resize(700, 200)
    window._search_field.focus_search()
    window._search_field._edit.setText("#")
    qt_app.processEvents()

    popup = window._search_field._popup
    assert popup.isVisible(), "窗口一变矮，列表就该收起来，而不是悬在窗口外面"
    assert popup.geometry().bottom() <= window.height(), (
        f"列表底下伸到了第 {popup.geometry().bottom()} 行，窗口只有 {window.height()} 行"
    )
    window.close()


def _click_candidate(field: object, row: int = 0) -> None:
    """在候选列表的第 ``row`` 行按下并松开（等价于用户点它一下）。"""
    popup = field._popup  # type: ignore[attr-defined]
    viewport = popup.viewport()
    center = popup.visualItemRect(popup.topLevelItem(row)).center()
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        QApplication.sendEvent(
            viewport,
            QMouseEvent(
                kind,
                QPointF(center),
                QPointF(viewport.mapToGlobal(center)),
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )


def test_clicking_a_candidate_completes_it(qt_app: QApplication, tmp_path: Path) -> None:
    """点候选与回车补全是同一件事，而且**补完焦点要回到输入框**。

    这一条最容易坏：候选列表不吃焦点，点它时输入框会失焦 —— 收得太快就吃掉了这一次点击
    （列表在 ``itemClicked`` 之前就没了），收得太慢列表又会赖着不走。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    field = window._search_field
    field.focus_search()
    qt_app.processEvents()
    field._edit.setText("#省")
    qt_app.processEvents()
    assert field.visible_fields() == ["#省/州"]

    _click_candidate(field)

    assert field.text() == "#省/州 "
    assert not field.popup_visible()
    assert field._edit.hasFocus() is True
    window.close()


def test_entering_a_value_closes_the_list(qt_app: QApplication, tmp_path: Path) -> None:
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    window._search_field._edit.setText("#相机 Canon")

    assert not window._search_field.popup_visible()
    window.close()


def test_the_box_is_cleared_after_a_confirmed_search(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """用户确认之后输入框就该是干净的 —— 下一条条件从零开始敲。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "海边")

    assert window._search_field.text() == ""
    assert _names(window) == ["海边-01.jpg", "海边-02.jpg"]
    window.close()


# ---- 条件落在面包屑上 ---------------------------------------------------------------


def test_the_condition_lands_on_the_breadcrumb(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "海边")

    assert _chips(window) == ["文件名：海边"]
    # 平铺模式下平时没有面包屑，**有条件时必须出现** —— 否则胶囊没地方摆。
    assert window._breadcrumb.isVisible()
    labels = [
        window._breadcrumb._layout.itemAt(index).widget().text()
        for index in range(window._breadcrumb._layout.count())
        if window._breadcrumb._layout.itemAt(index).widget() is not None
        and hasattr(window._breadcrumb._layout.itemAt(index).widget(), "text")
    ]
    assert any("海边" in text for text in labels), labels
    assert any("搜索结果（2）" in text for text in labels), labels
    window.close()


def test_a_metadata_condition_needs_the_field_prefix(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "#相机 Canon")

    assert _chips(window) == ["相机：Canon"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_conditions_narrow_each_other(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """多条条件是「全部满足」：每加一条就再收窄一层（ADR 0026 决策 1）。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相机 Sony")

    assert _chips(window) == ["文件名：海边", "相机：Sony"]
    assert _names(window) == []
    window.close()


def test_the_same_field_twice_narrows_too(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "#相机 Canon")
    _search(qt_app, window, "#城市 西安")

    assert _chips(window) == ["相机：Canon", "城市：西安"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


# ---- 撤销一条 -----------------------------------------------------------------------


def test_clicking_the_dismiss_mark_removes_that_condition(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相册 旅行")

    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == ["相册：旅行"]
    assert _names(window) == ["山.jpg"]
    window.close()


def _chip_buttons(window: MainWindow) -> list[ChipButton]:
    """面包屑上那些条件胶囊（**每次重画都是新对象**，所以别把引用存过一轮）。"""
    return window._breadcrumb.findChildren(ChipButton)


def _click_chip(window: MainWindow, row: int, *, at_mark: bool = False) -> None:
    """点第 ``row`` 枚胶囊：``at_mark=True`` 点右端那个 ✕，否则点文字。

    两处**必须真的落在不同的热区**上：分界是按字体度量算的（见
    ``ChipButton._hits_mark``），点歪了就分不出「改」与「撤掉」，而那正是这条交互的全部。
    """
    chip = _chip_buttons(window)[row]
    x = chip.width() - 4 if at_mark else 10
    QApplication.sendEvent(
        chip,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, chip.height() / 2),
            QPointF(x, chip.height() / 2),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_the_breadcrumb_says_the_search_spans_the_library(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """检索时面包屑**只报库名**，不报当前所在的文件夹。

    在 `旅行/2024` 里搜，命中的是整个库（ADR 0026 决策 1）—— 于是那一行若还留着
    「旅行 ▸ 2024」，读起来就是「在这个文件夹里搜到 4 张」，而实际大半在别处。
    那是在说一件不存在的事。撤光条件之后文件夹自己回来。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "旅行", "2024"]

    _search(qt_app, window, "山")
    _settle(qt_app, window)

    assert _trail(window) == ["照片", "搜索结果（1）", "文件名：山 ✕"]
    assert _names(window) == ["山.jpg"]
    window.close()

    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)
    _search(qt_app, window, "山")
    _settle(qt_app, window)
    window.go_up()  # 撤掉那一条条件
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "旅行", "2024"], "撤光条件就该回到原来那一层"
    window.close()


def test_the_library_name_stays_a_link_while_searching(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """检索时点库名就该退回相册浏览。

    当前所在是「搜索结果」那一段，不是库名 —— 于是库名与路径上的其它段一样该能点。
    把它画成不可点的当前层，用户就只剩「把条件一条条撤光」这一条路走得回去。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)
    _search(qt_app, window, "山")
    _settle(qt_app, window)

    links = [b for b in window._breadcrumb.findChildren(QToolButton) if b.text() == "照片"]
    assert len(links) == 1, "检索时库名也该是可点的链接（当前所在是「搜索结果」）"

    links[0].click()
    _settle(qt_app, window)

    assert window._conditions == [], "点库名就是退出检索"
    assert window._current_dir == root
    assert _trail(window) == ["照片"]
    assert sorted(_names(window)) == ["旅行", "海边-01.jpg", "海边-02.jpg"], (
        "回到的是库根那一层（相册卡片 + 散着的照片），不是检索结果"
    )
    window.close()


def test_the_breadcrumb_drops_the_group_while_searching(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """检索时那一组也不报 —— 与文件夹同一个理由。

    「照片 ▸ 2024 年 5 月 ▸ 搜索结果（3）」读起来是「这一组里搜到 3 张」，而检索横跨
    整库。实测过一个**空组**：里面搜出 3 张全库照片，面包屑却还写着那个组。
    撤光条件之后要回到原来那一组（分组状态留着，不是丢了）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "Canon"]

    _search(qt_app, window, "jpg")
    _settle(qt_app, window)

    assert _trail(window) == ["照片", "搜索结果（3）", "文件名：jpg ✕"]
    assert len(_names(window)) == 3, "这一组里只有一张，搜出来的却是全库三张"
    window.go_up()  # 撤掉那一条条件
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "Canon"], "撤光条件就该回到原来那一组"
    assert window._group_view is not None
    window.close()


def _trail(window: MainWindow) -> list[str]:
    """面包屑那一行的全部文字，**按摆放顺序**（``findChildren`` 就是布局里的先后）。

    分隔符不算。条件胶囊也是按钮，一并在这里 —— 判据只有一条：那一行上写着什么。
    """
    parts: list[str] = []
    for widget in window._breadcrumb.findChildren(QWidget):
        text = widget.text().strip() if hasattr(widget, "text") else ""
        if text and text != "▸":
            parts.append(text)
    return parts


def test_clicking_a_chip_puts_it_back_into_the_box(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """点胶囊上的**文字**是「改」：放回输入框接着敲。

    改一条条件的结果是**那一条**，不是「删掉旧的、冒出一条新的」—— 这一点由下面那条
    用例看着顺序。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "#相册 旅行")

    _click_chip(window, 0)

    assert window._search_field.text() == "#相册 旅行"
    assert _chips(window) == ["相册：旅行"], "刚点了一下，那一条就没了"
    assert window._editing_condition == 0
    window.close()


def test_submitting_while_editing_replaces_that_chip_in_place(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改完回车是**就地替换**：位置不变，条目数不变。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相册 旅行")

    _click_chip(window, 1)
    _search(qt_app, window, "#相册 家庭")

    assert _chips(window) == ["文件名：海边", "相册：家庭"]
    assert window._editing_condition is None
    assert _names(window) == []
    window.close()


def test_clicking_the_mark_still_removes_that_condition(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """点右端那个 ✕ 才是撤掉 —— 与点文字是两个动作，两处都要能按预期走。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相册 旅行")

    _click_chip(window, 0, at_mark=True)
    _settle(qt_app, window)

    assert _chips(window) == ["相册：旅行"]
    assert _names(window) == ["山.jpg"]
    window.close()


def test_clearing_the_box_and_pressing_enter_removes_the_edited_condition(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改到一半把输入框清空再回车 = **撤掉这一条**。

    「点一下胶囊就没了」是不能有的意外（改与撤必须是两个动作）；而清空之后回车在输入框
    里本来就没有别的意思，于是把它定成撤掉，删除另有明路。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相册 旅行")

    _click_chip(window, 0)
    window._search_field._edit.clear()
    window._search_field.submit()
    _settle(qt_app, window)

    assert _chips(window) == ["相册：旅行"]
    window.close()


def test_escape_while_editing_keeps_the_condition(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改到一半按 Esc 是**放弃这一次改动**，那一条原样留着。

    不然「改到一半想溜」与「撤掉一条」是同一下，用户没法中途反悔。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")

    _click_chip(window, 0)
    window._search_field._edit.setText("#相册 旅行")
    window._search_field.dismissed.emit()
    qt_app.processEvents()

    assert _chips(window) == ["文件名：海边"]
    assert window._search_field.text() == ""
    assert window._editing_condition is None
    window.close()


def test_escape_removes_the_last_condition_one_by_one(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    _search(qt_app, window, "#相册 旅行")

    window.go_up()
    _settle(qt_app, window)
    assert _chips(window) == ["文件名：海边"]

    window.go_up()
    _settle(qt_app, window)
    assert _chips(window) == []
    # 撤光了才是「回到原来那一层」：三张照片全回来了。
    assert _names(window) == ["山.jpg", "海边-01.jpg", "海边-02.jpg"]
    window.close()


def test_clearing_everything_empties_the_breadcrumb_row(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _search(qt_app, window, "海边")

    window.clear_search_conditions()
    _settle(qt_app, window)

    assert _chips(window) == []
    assert _names(window) == ["山.jpg", "海边-01.jpg", "海边-02.jpg"]
    # 没有条件时菜单里那一条**整个消失**（不是置灰，ADR 0014 决策 4）。
    assert window._clear_search_action.isVisible() is False
    window.close()


def test_going_into_an_album_leaves_the_search(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """进目录就是退出检索：两者都是「一层」，面包屑上只有一个是当前所在。

    顺带钉住另一件事：**平铺模式下进目录是进不去的**（那一层永远是整库），
    所以这条用例得在相册模式下说 —— 换个模式就换了语义，不是同一个东西。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _search(qt_app, window, "海边")
    assert _names(window) == ["海边-01.jpg", "海边-02.jpg"]

    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)

    assert _chips(window) == []
    assert _names(window) == ["山.jpg"]
    window.close()


# ---- 索引还没建 ---------------------------------------------------------------------


def test_searching_before_the_index_is_built_still_finds_things(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """用户看到空结果就再也不会搜索第二次了。

    所以：条件先记下，索引那一遍自己跑，跑完把这次检索补上（ADR 0026 决策 5）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window, index=False)

    _search(qt_app, window, "海边")

    assert _chips(window) == ["文件名：海边"]
    assert _names(window) == ["海边-01.jpg", "海边-02.jpg"]
    window.close()


# ---- 别的东西让路 -------------------------------------------------------------------


def test_typing_in_the_box_lets_the_file_commands_go(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """键盘归输入框时，⌘⌫ 不该把正在看的那张照片删掉（与两个改名输入框同一件事）。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    window._grid.select_row(0)
    qt_app.processEvents()
    assert window._trash_action.isEnabled() is True

    window.focus_search()
    qt_app.processEvents()

    assert window._trash_action.isEnabled() is False
    window.close()


def test_a_word_where_a_number_belongs_is_refused_with_a_word(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """值写坏了要说人话 —— 用户以为条件生效了，而它没有。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    _search(qt_app, window, "#ISO abc")

    assert _chips(window) == []
    assert "ISO" in window._banner.text()
    window.close()


def test_searching_without_a_library_says_so(qt_app: QApplication, tmp_path: Path) -> None:
    """没有库时收下条件是悄悄的：面包屑压根不显示，换库时又清掉。

    与其让用户对着一个什么都没发生的输入框，不如说一句「还没有库可搜」。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    _search(qt_app, window, "海边")

    assert _chips(window) == []
    assert "先打开" in window._banner.text()
    window.close()
