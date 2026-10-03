"""搜索在窗口里的整条链（ADR 0026）。

覆盖用户真正会经历的那条路：在标题栏右上角那个胶囊里敲一个词 → 回车 → 条件落到
面包屑那一行的最右侧 → 网格只剩命中的照片 → 再敲一条（收窄）→ 点 ✕ 撤掉一条 →
`Esc` 逐条撤销 → 清空之后回到原来那一层。

**元数据读取与地点反查都用假的**（理由与 ``test_group_flow.py`` 一样）：测试不依赖本机
装了 exiftool，也不该因为机器上碰巧装了就把行为测偏。

有一条是这条链最容易出错的���面，单独测：**第一次搜索时索引还没建**。用户看到空结果
就再也不会搜索第二次了 —— 所以那一遍要自己跑完并且把这次检索补上。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

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
    save_session(
        SessionState(recent_roots=(root,), browse_mode=browse_mode),
        base=session_base,
    )
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
    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        idle = (
            window._scan_job is None
            and window._facet_job is None
            and window._place_job is None
            and window._search_job is None
        )
        idle = idle and (not index or window._index_job is None)
        if idle and window._staged is None:
            qt_app.processEvents()
            return
        time.sleep(0.01)
    qt_app.processEvents()


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
    assert field.visible_fields() == ["#省 / 州"]

    _click_candidate(field)

    assert field.text() == "#省 / 州 "
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
