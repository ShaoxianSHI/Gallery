"""元数据面板的可读性回归测试。

这里的两条测试都是补真实缺陷：

1. 普通 ``QWidget`` 不设 ``WA_StyledBackground`` 就不会画样式表里的背景，面板于是会是
   **系统白底配浅色文字**。所以这里不检查控件树，而是**量渲染出来的像素**：
   底色必须是窗口统一的那一个，且文字对比度要够。
2. 换一份内容时旧控件会被 ``deleteLater`` 排队删除，在真正销毁前仍画在原位置，
   于是上一份内容糊在新内容上面。所以这里检查旧文案确实不见了。

面板不再自带一套深色调色板，底色与文字都跟主题走（见 ``ui.theme``），所以
「正文色」也不能再假定是画面里最亮的那个 —— 浅色主题下恰恰相反。

Qt 的离屏平台让这些断言不需要真实显示器。
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from gallery.core.events.models import Event  # noqa: E402
from gallery.core.metadata.report import build_report  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.viewer.metadata import (  # noqa: E402
    MetadataPanel,
    event_fields,
    event_title,
)

#: 可读性的最低对比度（WCAG AA 对正文的要求）。
MIN_CONTRAST = 4.5


def luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def contrast_ratio(first: QColor, second: QColor) -> float:
    brighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (brighter + 5) / (darker + 5)


def sample_colors(widget: QWidget) -> tuple[QColor, QColor]:
    """返回 ``(底色, 与底色对比最强的颜色)``：前者是铺得最多的，后者当作正文色。"""
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    counts: Counter[int] = Counter()
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            counts[image.pixelColor(x, y).rgb()] += 1
    ranked = [QColor(rgb) for rgb, _ in counts.most_common(400)]
    background = ranked[0]
    return background, max(ranked, key=lambda color: contrast_ratio(background, color))


@pytest.fixture
def report(tmp_path: Path) -> object:
    return build_report(
        {"EXIF:Make": "Canon", "EXIF:Model": "Canon EOS R6", "EXIF:ISO": "400"},
        path=tmp_path / "IMG_0001.JPG",
        size=1024,
        mtime_ns=0,
        source="exiftool",
    )


def test_panel_matches_the_window_background_with_readable_text(
    qt_app: QApplication, report: object
) -> None:
    panel = MetadataPanel()
    panel.resize(300, 420)
    panel.set_report(report)  # type: ignore[arg-type]
    panel.show()
    qt_app.processEvents()

    background, text = sample_colors(panel)

    assert background == theme.current_theme().background, (
        f"面板底色是 {background.name()}，窗口是 {theme.current_theme().background.name()}"
        " —— 面板是图片区旁边的一块信息板，不该自己开一块颜色"
    )
    assert contrast_ratio(background, text) >= MIN_CONTRAST, (
        f"底色 {background.name()} 与文字 {text.name()} 的对比度不足"
    )
    panel.deleteLater()


def test_the_scroll_area_follows_a_theme_change(qt_app: QApplication, report: object) -> None:
    """换外观时**滚动区那一整块也要跟着变**，不能只在四边露出新底色。

    深色下建出来、随后切到浅色：面板中间（滚动区与它里面那一层）曾经停在旧的深色上 ——
    重设样式表会把这一支重新 polish 一遍，内容区的调色板在那一步被弹回上一轮的底色。
    """
    original = QPalette(qt_app.palette())
    dark = QPalette(original)
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
    ):
        dark.setColor(role, QColor("#171717"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
        dark.setColor(role, QColor("#ffffff"))

    panel: MetadataPanel | None = None
    try:
        qt_app.setPalette(dark)
        panel = MetadataPanel()
        panel.resize(300, 420)
        panel.set_report(report)  # type: ignore[arg-type]
        panel.show()
        qt_app.processEvents()
        assert sample_colors(panel)[0] == theme.current_theme().background, (
            "深色下建出来的那一份就先不对了，后面那条切换也就没了意义"
        )

        qt_app.setPalette(original)
        qt_app.paletteChanged.emit(original)
        qt_app.processEvents()

        background, text = sample_colors(panel)
        assert background == theme.current_theme().background, (
            f"换到浅色后滚动区还是 {background.name()}，窗口是"
            f" {theme.current_theme().background.name()}"
            " —— 面板四边已经变白，中间却压着一整块旧色"
        )
        assert contrast_ratio(background, text) >= MIN_CONTRAST
    finally:
        qt_app.setPalette(original)
        qt_app.paletteChanged.emit(original)
        qt_app.processEvents()
        if panel is not None:
            panel.deleteLater()


def test_switching_report_leaves_no_stale_labels(qt_app: QApplication, report: object) -> None:
    panel = MetadataPanel()
    panel.clear()
    qt_app.processEvents()
    assert any("尚未打开照片" in label.text() for label in panel.findChildren(QLabel))

    panel.set_report(report)  # type: ignore[arg-type]
    qt_app.processEvents()

    remaining = [label.text() for label in panel.findChildren(QLabel)]
    assert not any("尚未打开照片" in text for text in remaining)
    assert any("相机" in text for text in remaining)
    panel.deleteLater()


def test_switching_report_hides_widgets_before_they_leave_the_panel(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """换内容时，被移走的控件必须**先藏起来**。

    ``_fill`` 换内容时会把旧控件 ``setParent(None)`` 摘出控件树再排队删除。这件事本身是
    需要的，但顺序错了就是缺陷：**Qt 会把一个本来可见的控件在摘成顶层窗口时顺手显示出
    来**，于是面板的位置上弹出一个个白色小窗（macOS 上还带标题栏），里面是上一份元数据
    —— 用户看到的正是「打开照片时照片的位置上闪一下的小白窗」。打开第一张要走「尚未打开 →
    正在读取 → 出结果」三次重绘，所以会闪两次；⌘I 关着时不走这条路径，也就看不到。

    离屏环境复现不出那个窗口本身（Qt 在离屏下不会真的把它显示出来），所以这里断言的是
    **成因**：**控件离开面板的那一刻必须已经不可见**。这条与平台无关，坏代码必红。
    """
    panel = MetadataPanel()
    panel.resize(300, 420)
    panel.show()
    panel.set_report(
        build_report(
            {"EXIF:Make": "Canon"},
            path=tmp_path / "IMG_0001.JPG",
            size=1024,
            mtime_ns=0,
            source="exiftool",
        )
    )
    qt_app.processEvents()

    # 给面板里的每个控件套一层 setParent 探针：记下它离开控件树的那一刻是否还看得见。
    seen_visible: list[bool] = []
    kept: list[object] = []
    for widget in panel._body.findChildren(QWidget):
        original = widget.setParent

        def spy(
            parent: object = None,
            *args: object,
            _widget: QWidget = widget,
            _original: object = original,
            **kwargs: object,
        ) -> object:
            if parent is None:
                seen_visible.append(_widget.isVisible())
            return _original(parent, *args, **kwargs)  # type: ignore[operator]

        widget.setParent = spy  # type: ignore[method-assign]
        kept.append(spy)

    panel.set_report(
        build_report(
            {"EXIF:Model": "Canon EOS R6"},
            path=tmp_path / "IMG_0002.JPG",
            size=2048,
            mtime_ns=0,
            source="exiftool",
        )
    )

    assert seen_visible, "探针一个控件也没抓到 —— 这条测试就成了一句空话"
    assert not any(seen_visible), (
        f"有 {sum(seen_visible)} 个控件是**可见着**离开面板的：Qt 会把它们一个个变成独立"
        "的小窗口弹出来（就是那个闪一下的白色小窗）"
    )
    panel.deleteLater()


# ---- 焦点在面板里时，← / → 仍然换照片 -----------------------------------------


def _shown_panel(qt_app: QApplication) -> MetadataPanel:
    """面板得**可见**快捷键才生效 —— 藏着的面板不该抢按键，所以这里真把它摆出来。"""
    host = QWidget()
    QVBoxLayout(host).addWidget(MetadataPanel())
    host.show()
    qt_app.processEvents()
    panel = host.layout().itemAt(0).widget()
    assert isinstance(panel, MetadataPanel)
    panel.deleteLater()
    host.deleteLater()
    return panel


def _steps(panel: MetadataPanel, qt_app: QApplication) -> list[int]:
    seen: list[int] = []
    panel.photo_step_requested.connect(seen.append)
    panel._copy_button.setFocus()
    qt_app.processEvents()
    QTest.keyClick(panel._copy_button, Qt.Key.Key_Right)
    QTest.keyClick(panel._copy_button, Qt.Key.Key_Left)
    return seen


def test_the_arrow_keys_switch_photos_while_the_panel_has_focus(
    qt_app: QApplication,
) -> None:
    """**焦点落在面板里，← / → 仍然换照片。**

    网格自己的方向键走 ``PhotoGrid.keyPressEvent``，那条路只在网格有焦点时通；点一下
    「复制全部」、点一下「显示全部字段」，焦点就跑到面板这一支里来了，此后 ← / → 按下去
    什么也没发生 —— 用户明明还看着同一张照片的元数据，却换不了它。
    """
    panel = _shown_panel(qt_app)

    assert _steps(panel, qt_app) == [1, -1]


def test_it_works_from_a_plain_click_on_the_panel(qt_app: QApplication) -> None:
    panel = _shown_panel(qt_app)
    seen: list[int] = []
    panel.photo_step_requested.connect(seen.append)

    panel.setFocus()
    qt_app.processEvents()
    QTest.keyClick(panel, Qt.Key.Key_Right)

    assert seen == [1]


def test_the_up_and_down_keys_are_left_to_the_scroll_area(qt_app: QApplication) -> None:
    """**上下键留给滚动区自己滚动** —— 面板里的内容经常比面板高。"""
    panel = _shown_panel(qt_app)
    seen: list[int] = []
    panel.photo_step_requested.connect(seen.append)
    panel.setFocus()
    qt_app.processEvents()

    QTest.keyClick(panel, Qt.Key.Key_Up)
    QTest.keyClick(panel, Qt.Key.Key_Down)

    assert seen == []


def test_a_modified_arrow_key_is_left_alone(qt_app: QApplication) -> None:
    """⌥← 之类多半是输入法或辅助功能的用法，不该被抢走。"""
    panel = _shown_panel(qt_app)
    seen: list[int] = []
    panel.photo_step_requested.connect(seen.append)
    panel.setFocus()
    qt_app.processEvents()

    QTest.keyClick(panel, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)

    assert seen == []


def test_a_text_box_inside_the_panel_keeps_its_arrow_keys(qt_app: QApplication) -> None:
    """焦点在文本框里就不插手 —— 方向键是那个框的，不是换照片的。"""
    panel = _shown_panel(qt_app)
    seen: list[int] = []
    panel.photo_step_requested.connect(seen.append)
    editor = QLineEdit()
    panel.layout().addWidget(editor)
    editor.setFocus()
    qt_app.processEvents()

    QTest.keyClick(editor, Qt.Key.Key_Right)

    assert seen == []


# ---- 照片所属事件的元数据 -----------------------------------------------------


def _trip_event(**overrides: str | int | float | None) -> Event:
    values: dict[str, str | int | float | None] = {
        "title": "京都五日",
        "started_on": "2026-05-16",
        "destination": "京都、奈良",
        "companions": "小王",
        "cost": 3280.0,
        "cover": "/照片/京都-01.jpg",
    }
    values.update(overrides)
    return Event(id=1, kind="trip", created_at=0, modified_at=0, values=values)


def _body_labels(panel: MetadataPanel) -> list[str]:
    """面板正文里的文字，**按摆放顺序**（事件是不是真的在照片元数据下面靠它判）。"""
    texts: list[str] = []
    for index in range(panel._body_layout.count()):
        widget = panel._body_layout.itemAt(index).widget()
        if widget is not None:
            texts.extend(label.text() for label in widget.findChildren(QLabel))
    return texts


def test_event_fields_skip_the_name_and_the_cover() -> None:
    """名称已经在组标题里，封面只是一串文件名，两者都不再占一行；空着的字段也跳过。"""
    fields = event_fields(_trip_event())

    assert [field.label for field in fields] == ["开始日期", "目的地", "同行者", "花费"]
    assert (fields[-1].label, fields[-1].value) == ("花费", "3280")


def test_event_title_carries_the_kind_and_the_name() -> None:
    assert event_title(_trip_event()) == "旅行 · 京都五日"


def test_an_event_of_an_unknown_kind_shows_just_its_name() -> None:
    """库里存着的那一类定义没了（以后删掉一类事件就是这么回事）：退回名称，字段一个不摆。"""
    orphan = Event(id=2, kind="nope", created_at=0, modified_at=0, values={"title": "旧记录"})

    assert event_title(orphan) == "旧记录"
    assert event_fields(orphan) == ()


def test_the_panel_shows_events_below_the_photo_metadata(
    qt_app: QApplication, report: object
) -> None:
    panel = MetadataPanel()
    panel.resize(300, 600)
    panel.set_report(report)  # type: ignore[arg-type]
    panel.set_events((_trip_event(),))
    panel.show()
    qt_app.processEvents()

    texts = _body_labels(panel)
    assert "旅行 · 京都五日" in texts
    assert "京都、奈良" in texts
    assert texts.index("文件") < texts.index("旅行 · 京都五日"), "事件应当摆在照片自己的元数据下面"
    panel.deleteLater()


def test_events_survive_a_report_swap(qt_app: QApplication, tmp_path: Path) -> None:
    """地点反查会再交一份报告进来：换报告不该把下面的事件一起丢掉。"""
    panel = MetadataPanel()
    panel.set_events((_trip_event(),))
    panel.set_report(
        build_report({}, path=tmp_path / "IMG_0001.JPG", size=1, mtime_ns=0, source="exiftool")
    )
    assert "旅行 · 京都五日" in _body_labels(panel)

    panel.set_report(
        build_report(
            {"EXIF:Make": "Canon"},
            path=tmp_path / "IMG_0001.JPG",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )
    )

    assert "旅行 · 京都五日" in _body_labels(panel), "换一份报告把事件弄丢了"
    panel.deleteLater()


def test_clear_drops_the_events(qt_app: QApplication, report: object) -> None:
    panel = MetadataPanel()
    panel.set_report(report)  # type: ignore[arg-type]
    panel.set_events((_trip_event(),))
    assert "旅行 · 京都五日" in _body_labels(panel)

    panel.clear()

    assert "旅行 · 京都五日" not in _body_labels(panel)
    panel.deleteLater()


def test_copy_all_includes_the_events(qt_app: QApplication, report: object) -> None:
    panel = MetadataPanel()
    panel.set_report(report)  # type: ignore[arg-type]
    panel.set_events((_trip_event(),))

    panel._copy_all()

    copied = QGuiApplication.clipboard().text()
    assert "【旅行 · 京都五日】" in copied
    assert "目的地：京都、奈良" in copied
    panel.deleteLater()
