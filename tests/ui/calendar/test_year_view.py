"""年视图一帧里那些「算一次就够」的东西，以及**每张小月只摆自己那个月的照片**。

**12 张小月的日期矩阵在一帧之内是不变的**：42 个 ``datetime.date`` × 12。
原先每张小月算两遍（数张数一遍 ``days_of``、画格子一遍 ``month_matrix``），
于是每帧重建 24 × 42 = 1008 个 ``date`` —— 而这只是一次 ``paintEvent``。

另一半是那 12 张小月都是**含上下月补位**的 6 × 7：10 月 1 号既在「9 月」那张的末尾，
也在「10 月」那张的开头。照片因此摆了两遍、张数数了两遍，而 9 月那个数字里混着
十月的照片。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.calendar import month_matrix  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.ui.calendar import year_view as year_view_module  # noqa: E402
from gallery.ui.calendar.page import CalendarPage  # noqa: E402

#: 九月自己的一天。
SEPTEMBER_12 = datetime.date(2021, 9, 12)
#: **补位格**：10 月 1 号既在「9 月」那张小月的末尾，也在「10 月」那张的开头。
OCTOBER_1 = datetime.date(2021, 10, 1)


@pytest.fixture
def page(qt_app: QApplication) -> CalendarPage:  # noqa: ARG001 - 造控件的就是它
    widget = CalendarPage()
    widget.set_today(datetime.date(2021, 9, 12))
    widget.go_today()
    widget.set_level("year")
    refs: list[PhotoRef] = []
    values = {}
    for index in range(60):
        ref = PhotoRef(path=Path(f"/tmp/year{index}.jpg"), size=1, mtime_ns=0)
        day = datetime.date(2021, 1, 1) + datetime.timedelta(days=index)
        refs.append(ref)
        values[ref.path] = PhotoFacets(
            captured_at=float(day.toordinal() * 86400),
            year=day.year,
            month=day.month,
            day=day.day,
        )
    widget.set_photos(refs, values)
    widget.resize(1200, 800)
    return widget


def test_one_frame_builds_each_month_matrix_once(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12 张小月 → **12 次**，不是 24 次（每张小月要算两遍的那个版本）。"""
    view = page.current_view()
    calls: list[datetime.date] = []
    original = year_view_module.month_matrix

    def counted(anchor: datetime.date) -> list[datetime.date]:
        calls.append(anchor)
        return original(anchor)

    monkeypatch.setattr(year_view_module, "month_matrix", counted)
    view.render(QPixmap(view.size()))

    assert len(calls) == 12, f"一帧里算了 {len(calls)} 次日期矩阵"


def test_a_frame_still_draws_every_day_of_the_year(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """矩阵只算一遍之后，**该画的一格都不能少**（12 × 42 = 504 个日期数字）。

    这是「算一次就够」最容易踩的坑：把两次遍历合并成一次时，很容易顺手把
    「数张数」那一遍里的东西一起丢了。
    """
    view = page.current_view()
    drawn: list[str] = []
    original = year_view_module.draw_text

    def recorded(painter, rect, text, font, color, *args, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append(text)
        return original(painter, rect, text, font, color, *args, **kwargs)

    monkeypatch.setattr(year_view_module, "draw_text", recorded)
    view.render(QPixmap(view.size()))

    assert sum(1 for text in drawn if text.isdigit()) == 504, len(drawn)
    # 12 张小月各一个「N 月」标题；表头是「星期一」之类取最后一个字（84 个）。
    assert sum(1 for text in drawn if text.endswith("月")) == 12, len(drawn)


# ---- 补位格：每张小月只摆自己那个月的照片 -----------------------------------


@pytest.fixture
def two_months(qt_app: QApplication) -> CalendarPage:  # noqa: ARG001 - 造控件的就是它
    """九月一张、十月一张 —— 10 月 1 号那张正好落在九月那张小月的末尾。"""
    widget = CalendarPage()
    widget.set_today(SEPTEMBER_12)
    widget.go_today()
    widget.set_level("year")
    refs = [
        PhotoRef(path=Path("/tmp/september.jpg"), size=1, mtime_ns=0),
        PhotoRef(path=Path("/tmp/october.jpg"), size=1, mtime_ns=0),
    ]
    widget.set_photos(
        refs,
        {
            refs[0].path: _facets(SEPTEMBER_12),
            refs[1].path: _facets(OCTOBER_1),
        },
    )
    widget.resize(1200, 800)
    return widget


def _facets(day: datetime.date) -> PhotoFacets:
    return PhotoFacets(
        captured_at=float(day.toordinal() * 86400),
        year=day.year,
        month=day.month,
        day=day.day,
    )


def _asked_days(view: object, monkeypatch: pytest.MonkeyPatch) -> list[datetime.date]:
    """这一帧里「取过照片」的那些天（小月按 1–12 月的顺序画，所以顺序也有意义）。"""
    asked: list[datetime.date] = []
    original = view.photos_of  # type: ignore[attr-defined]

    def record(day: datetime.date) -> Sequence[PhotoRef]:
        asked.append(day)
        return original(day)

    monkeypatch.setattr(view, "photos_of", record)
    view.render(QPixmap(view.size()))  # type: ignore[attr-defined]
    return asked


def test_the_filler_day_really_is_in_two_mini_months() -> None:
    """前提：10 月 1 号**确实**既在 9 月那张小月的末尾，也在 10 月那张的开头。

    钉这一条是因为下面几条都在说「它只该出现一次」—— 哪天那张表真的不再补位了，
    它们会空转成绿的，而「一张照片摆两遍」这个毛病却还在原地。
    """
    september = month_matrix(datetime.date(2021, 9, 1))
    october = month_matrix(datetime.date(2021, 10, 1))
    assert OCTOBER_1 in september and OCTOBER_1 in october


def test_a_photo_is_painted_in_one_mini_month_only(
    two_months: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10 月 1 号那张照片**只画一次** —— 在 10 月自己那张小月里。"""
    view = two_months.current_view()
    asked = _asked_days(view, monkeypatch)
    assert asked.count(OCTOBER_1) == 1, f"同一张照片画了 {asked.count(OCTOBER_1)} 次"
    assert SEPTEMBER_12 in asked, "前提不成立：九月自己那张没画"


def test_a_mini_month_counts_only_its_own_photos(
    two_months: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「9 月」那个数字写的是九月的张数 —— 10 月 1 号那张不算进去。"""
    view = two_months.current_view()
    drawn: list[tuple[float, float, str]] = []
    original = year_view_module.draw_text

    def recorded(painter, rect, text, font, color, *args, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append((rect.left(), rect.top(), text))
        return original(painter, rect, text, font, color, *args, **kwargs)

    monkeypatch.setattr(year_view_module, "draw_text", recorded)
    view.render(QPixmap(view.size()))

    # 第九张小月（9 月）标题上写着两行字：月份与张数
    box = view._box(8)  # type: ignore[attr-defined]
    title = [
        text
        for left, top, text in drawn
        if abs(left - box.left()) < 0.5 and abs(top - box.top()) < 0.5
    ]
    assert title == ["9月", "1 张"]


def test_the_thumbnail_window_reports_each_photo_once(two_months: CalendarPage) -> None:
    """补位格不报：那张照片本来就该在它自己那张小月里报一次。"""
    refs = two_months.current_view().visible_refs()
    assert len(refs) == 2, refs
    assert len(set(refs)) == 2, refs


def test_clicking_a_filler_cell_lays_nothing_out(two_months: CalendarPage) -> None:
    """9 月那张小月末尾那几格点下去不摊东西 —— 它们是十月的日子。

    点的是**有照片的那一格**：点一格没有照片的补位格本来就什么都不会发生，
    那样这条用例会在「点错了地方」的情况下空转成绿的。
    """
    view = two_months.current_view()
    opened: list[tuple[list[PhotoRef], str]] = []
    view.cell_requested.connect(lambda photos, label: opened.append((photos, label)))

    _click_cell(view, view._box(8), month_matrix(datetime.date(2021, 9, 1)).index(OCTOBER_1))
    assert opened == [], "9 月那张小月末尾那几格摊出了十月的照片"

    # 同一张照片在 10 月自己那张小月里点得动 —— 钉住坐标没算错（上面那条不是空转）
    _click_cell(view, view._box(9), month_matrix(datetime.date(2021, 10, 1)).index(OCTOBER_1))
    assert [label for _, label in opened] == ["10月1日"]


def _click_cell(view: object, box: object, index: int) -> None:  # type: ignore[no-untyped-def]
    point: QPointF = view._day_cells(box)[index].center()  # type: ignore[attr-defined]
    view.mousePressEvent(  # type: ignore[attr-defined, attr-defined]
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


# ---- 选中：点一天高亮那一天 ---------------------------------------------------


def _sample(image, cell) -> set[str]:  # type: ignore[no-untyped-def]
    """一格四周与正中那几个点的颜色 —— 比「是不是底色」稳（描边是抗锯齿的）。"""
    x, y, w, h = cell.x(), cell.y(), cell.width(), cell.height()
    points = [
        (x + w / 2, y + 0.5),
        (x + 0.5, y + h / 2),
        (x + w - 0.5, y + h / 2),
        (x + w / 2, y + h - 0.5),
        (x + w / 2, y + h / 2),
    ]
    return {image.pixelColor(int(px), int(py)).name() for px, py in points}


def test_a_picked_day_is_painted_as_selected(
    page: CalendarPage,
    qt_app: QApplication,
) -> None:
    """年视图里**点一天高亮那一天**。

    从前只有「今天」那一圈：点过的地方什么都不剩，于是「点一下 → 切到月视图 → 再切回来」
    这一路上，用户选中过哪一天在年视图里根本看不出来。钉的是**画出来的那一屏**：空格子
    （没有照片）与有照片的格子都要看得出高亮，而**旁边那一天一个字都不许多**。
    """
    from PySide6.QtGui import QImage

    view = page.current_view()
    page.show()
    qt_app.processEvents()
    image = QImage(view.size(), QImage.Format.Format_RGBA8888)

    empty = datetime.date(2021, 6, 15)  # fixture 只铺 1 月 1 日到 3 月 1 日
    with_photo = datetime.date(2021, 1, 5)
    neighbour = datetime.date(2021, 6, 16)
    assert not view.photos_of(empty), "前提不成立：空的那一天上有照片"
    assert view.photos_of(with_photo), "前提不成立：那一天上没有照片"

    view.render(image)
    before = {day: _sample(image, _cell_of(view, day)) for day in (empty, with_photo)}
    neighbour_before = _sample(image, _cell_of(view, neighbour))

    view.set_selected(empty)
    view.render(image)
    assert _sample(image, _cell_of(view, empty)) != before[empty], "空的那一天没有高亮"
    assert _sample(image, _cell_of(view, neighbour)) == neighbour_before, "邻居被涂了"

    view.set_selected(with_photo)
    view.render(image)
    assert _sample(image, _cell_of(view, with_photo)) != before[with_photo], (
        "有照片的那一天没有高亮"
    )


def _cell_of(view, day: datetime.date):  # type: ignore[no-untyped-def]
    """年视图里某一天那一格（12 张小月的矩阵与格子一一对应）。"""
    for index in range(12):
        box = view._box(index)
        for cell_day, cell in zip(
            month_matrix(datetime.date(view.anchor().year, index + 1, 1)),
            view._day_cells(box),
            strict=True,
        ):
            if cell_day == day:
                return cell
    raise AssertionError(f"年视图上没有 {day} 这一格")
