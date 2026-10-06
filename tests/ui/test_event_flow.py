"""「记录为」事件的界面测试（ADR 0043）：三级菜单、面板、写完之后横幅 / 分组 / ⌘Z 都跟上。

**不碰真 exiftool**（与元数据那一族同一套做法）：事件读写压根不经手它，而日期范围那一步要查
拍摄时间，所以那些照片是用 ``exif`` 参数真的带上了拍摄时间的。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PIL import ExifTags, Image  # noqa: E402
from PySide6.QtCore import QPoint, QRect, QTimer  # noqa: E402
from PySide6.QtGui import QAction, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMenu,
    QMessageBox,
)
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.events.kinds import TRIP, fields_of  # noqa: E402
from gallery.core.events.store import Event, EventDraft  # noqa: E402
from gallery.core.library.config import BROWSE_MODE_ALBUMS, prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui import menu_style  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.menu_style import ShortcutColumnMenu  # noqa: E402
from gallery.ui.panels.event_edit import ChoiceButtons, EventEditDialog  # noqa: E402
from gallery.ui.theme import rgba  # noqa: E402

#: DateTimeOriginal 的标签编号与它在**Exif 子 IFD** 里（塞进主 IFD 读不出来）。
DATETIME_ORIGINAL = 0x9003


def _photo(root: Path, name: str, *, taken_at: str | None = "2026:05:16 10:00:00") -> Path:
    """造一张真的带拍摄时间的照片。

    日期范围那一步要读的就是它，所以**必须真的写进文件** —— 编造一份 ``PhotoFacets`` 的话，
    那一步与索引之间的连线就没被测到。
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (16, 12), (200, 120, 40))
    try:
        tags = image.getexif()
        if taken_at is not None:
            tags.get_ifd(ExifTags.IFD.Exif)[DATETIME_ORIGINAL] = taken_at
        image.save(path, exif=tags)
    finally:
        image.close()
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "照片"
    library.mkdir(parents=True)
    _photo(library, "京都-01.jpg")
    _photo(library, "京都-02.jpg")
    _photo(library, "大阪-01.jpg", taken_at="2025:10:02 09:00:00")
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描与地点解析")
    yield instance
    instance.close()


def _paths(window: MainWindow, *names: str) -> tuple[Path, ...]:
    """这些照片的路径。

    **从 ``window._library.root`` 起算**，不是从造库的目录起算 —— macOS 上 ``/var`` 与
    ``/private/var`` 是同一条链（``facade._relative_posix`` 那段注释说的就是它），而模型里存的
    是解析过的路径，拿未解析的那个去 ``row_of_path`` 永远是 -1。
    """
    root = window._library.root  # type: ignore[union-attr]
    return tuple(root / name for name in names)


def _select(window: MainWindow, *names: str) -> int:
    """选中若干张（多选）并返回第一张的行号。**摆正选区**也要做 —— 菜单据此算判据。"""
    wanted = set(_paths(window, *names))
    rows = [
        window._model.row_of_path(path) for path in wanted if window._model.row_of_path(path) >= 0
    ]
    window._grid.clearSelection()
    for row in rows:
        window._grid.select_row(row)
    if rows:
        window._grid.select_for_context(rows[0])
    return rows[0] if rows else -1


def _context(window: MainWindow, row: int) -> QMenu:
    assert window._grid.select_for_context(row) == row
    menu = window.context_menu_for(row)
    assert menu is not None
    return menu


def _texts(menu: QMenu) -> list[str]:
    """菜单上的字。

    **菜单一定要先存进一个变量再传进来**（于是它的引用在这一整段里都活着）：``build_menu`` 造出来
    的那个 ``QMenu`` 由 Python 拥有，没人握着它就当场析构，而菜单里的 action 由它拥有 ——
    于是 ``[a.text() for a in window.context_menu_for(row).actions()]`` 里那些包装全是悬空的，
    报 ``Internal C++ object already deleted``。真实路径不受影响（``_show_context_menu`` 一直把
    菜单握在局部变量里再 ``exec``）。
    """
    return [action.text() for action in menu.actions()]


def _submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        sub = action.menu()
        if sub is not None and sub.title() == title:
            return sub
    raise AssertionError(f"菜单上没有「{title}」：{[a.text() for a in menu.actions()]}")


def _open_kind_menu(menu: QMenu) -> QMenu:
    """「记录为 → 旅行」那一层，并让它把内容填上（内容是展开时才填的）。"""
    record_as = _submenu(menu, "记录为")
    kinds = _submenu(record_as, "旅行")
    kinds.aboutToShow.emit()
    return kinds


def _create(kind_menu: QMenu, **fields: str) -> None:
    """走「新建旅行…」那一条，填几个框再保存。**真的按保存** —— 不然测的不是用户那一路。"""
    action = next(a for a in kind_menu.actions() if a.text().startswith("新建"))
    dialogs: list[EventEditDialog] = []

    def fill_and_save() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, EventEditDialog)
        dialogs.append(dialog)
        for field_id, value in fields.items():
            dialog._fields[field_id].setText(value)
        dialog._on_accept()

    QTimer.singleShot(0, fill_and_save)
    action.trigger()
    assert dialogs, "面板没有弹出来"


# ---- 菜单摆不摆 ---------------------------------------------------------------


def test_the_menu_offers_recording_as_an_event(qt_app: QApplication, window: MainWindow) -> None:
    """选中照片时，「记录为…」出现在菜单里（第四组「它是什么」的最前面）。"""
    row = _select(window, "京都-01.jpg")
    print("DBG row", row, "entries", [getattr(e, "name", None) for e in window._model.entries()])
    m = _context(window, row)
    print("DBG count", len(m.actions()))
    texts = [action.text() for action in m.actions()]

    assert "记录为" in texts


def test_it_is_absent_when_nothing_is_selected(qt_app: QApplication, window: MainWindow) -> None:
    """一张都没选时那一行不出现 —— 「记录为」的对象是**选中那些**照片。"""
    window._grid.clearSelection()

    menu = window.context_menu_for(-1)

    assert menu is None or "记录为" not in _texts(menu)


def test_it_is_absent_when_the_event_library_is_unavailable(
    qt_app: QApplication, window: MainWindow
) -> None:
    """只读卷上事件库建不出来，那一行**整个不出现** —— 而不是按下去才报错
    （ADR 0043 决策 1：用户并没有第三个有意义的选择）。"""
    row = _select(window, "京都-01.jpg")
    assert window._library is not None
    window._library._config_dir_ready = False
    window._library.close_index()

    menu = _context(window, row)

    assert "记录为" not in _texts(menu)


def test_all_three_levels_are_built_before_the_style_goes_out(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**三层都要在下发样式表之前就存在** —— ``apply_style`` 只在建菜单那一刻递归一次。

    晚建的那一层拿不到样式表，界面上就是一张系统默认的白底菜单（与相册清单那一支同一个坑）。
    """
    row = _select(window, "京都-01.jpg")
    menu = _context(window, row)
    record_as = _submenu(menu, "记录为")
    kinds = _submenu(record_as, "旅行")
    leave = _submenu(record_as, "从这次事件移出")

    # 三层**都**拿到了样式表。第三层若是在 ``apply_style`` 之后才建出来的（挂在某一次展开里），
    # 它会是一张系统默认的白底菜单 —— 而那个症状在离屏下几乎看不出来，所以这里断的是
    # 「样式到没到」。
    assert all(part.styleSheet() for part in (record_as, kinds, leave))


def test_the_record_as_menu_draws_the_same_solid_arrows(
    qt_app: QApplication, window: MainWindow
) -> None:
    """「记录为」那一层的箭头也是**自绘的实心三角** —— 「旅行」与「从这次事件移出」两行都要。

    这一层没有快捷键，原先走 Qt 那条 ``::right-arrow``（macOS 上画出来的形状与上一层不同，
    同一个软件里于是有两种箭头）。所以这里既钉样式表里 Qt 那条被关掉，
    也**数墨迹**：「关掉 Qt 的、自己又没画」时会得到一个空荡荡的右沿，样式表那一句却照样绿。
    """
    row = _select(window, "京都-01.jpg")
    record_as = _submenu(_context(window, row), "记录为")

    assert isinstance(record_as, ShortcutColumnMenu)
    assert record_as.owns_submenu_arrows()
    assert "QMenu::right-arrow { width: 0; height: 0; }" in record_as.styleSheet()

    record_as.popup(QPoint(60, 60))
    try:
        assert record_as.activeAction() is None, "有行被高亮了，数墨迹就数不出箭头"
        for label in ("旅行", "从这次事件移出"):
            action = next(a for a in record_as.actions() if a.text() == label)
            assert _arrow_ink(record_as, action) > 0, f"「{label}」那一行的箭头没画出来"
    finally:
        record_as.close()


def _arrow_ink(menu: QMenu, action: QAction) -> int:
    """这一行右沿那条箭头列里画出来多少墨迹（像素）。

    框按 ``SHORTCUT_RIGHT_INSET`` 对称取 12 点宽 —— 没画箭头时那一带只剩底色。
    抓图是设备像素，几何是逻辑像素，两边按倍率对起来。
    """
    background = menu.palette().color(QPalette.ColorRole.Window).rgb()
    rect = menu.actionGeometry(action)
    edge = rect.right() - menu_style.SHORTCUT_RIGHT_INSET
    box = QRect(edge - 6, rect.top(), 12, rect.height())
    image = menu.grab().toImage()
    scale = image.devicePixelRatio()
    return sum(
        1
        for y in range(round(box.top() * scale), round(box.bottom() * scale) + 1)
        for x in range(round(box.left() * scale), round(box.right() * scale) + 1)
        if image.pixelColor(x, y).rgb() != background
    )


# ---- 记一次 -------------------------------------------------------------------


def test_recording_two_photos_under_a_new_trip(qt_app: QApplication, window: MainWindow) -> None:
    """多选两张 → 新建旅行 → 填目的地与日期 → 保存：事件建好，两张都记进去了。"""
    library = window._library
    assert library is not None
    row = _select(window, "京都-01.jpg", "京都-02.jpg")
    kind_menu = _open_kind_menu(_context(window, row))

    _create(kind_menu, destination="京都, 奈良", started_on="2026-05-16")

    events = library.all_events()
    assert [event.display_name for event in events] == ["京都、奈良 · 2026-05-16"]
    recorded = set(library.events.photos_of_event(events[0].id))
    assert recorded == set(_paths(window, "京都-01.jpg", "京都-02.jpg"))


def test_the_day_range_picks_up_the_photos_outside_the_selection(
    qt_app: QApplication, window: MainWindow
) -> None:
    """**面板里那个勾才是容器型事件的正路**：只选中一张，勾上按日期范围，
    那天拍的三张（含没选中的）都记进去（ADR 0043 决策 4）。"""
    library = window._library
    assert library is not None
    row = _select(window, "京都-01.jpg")
    kind_menu = _open_kind_menu(_context(window, row))

    _create(kind_menu, destination="京都", started_on="2026-05-16", ended_on="2026-05-17")

    recorded = set(library.events.photos_of_event(library.all_events()[0].id))

    assert recorded == set(_paths(window, "京都-01.jpg", "京都-02.jpg"))


def test_unticking_the_range_keeps_it_to_the_selection(
    qt_app: QApplication, window: MainWindow
) -> None:
    """不勾就只记选中的那一张 —— 勾子的默认状态是可改的，不是「总是全库」。"""
    library = window._library
    assert library is not None
    row = _select(window, "京都-01.jpg")
    kind_menu = _open_kind_menu(_context(window, row))
    action = next(a for a in kind_menu.actions() if a.text().startswith("新建"))

    def fill_and_save() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, EventEditDialog)
        dialog._fields["destination"].setText("京都")
        dialog._fields["started_on"].setText("2026-05-16")
        dialog._include_range.setChecked(False)
        dialog._on_accept()

    QTimer.singleShot(0, fill_and_save)
    action.trigger()

    recorded = set(library.events.photos_of_event(library.all_events()[0].id))

    assert recorded == set(_paths(window, "京都-01.jpg"))


def test_recording_again_under_an_existing_trip(qt_app: QApplication, window: MainWindow) -> None:
    """同一个事件第二次记照片：清单在「新建」下面，而且**横幅说这次新记了几张**。"""
    library = window._library
    assert library is not None
    event = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    row = _select(window, "京都-01.jpg")
    record_as = _submenu(_context(window, row), "记录为")
    kinds = _submenu(record_as, "旅行")
    kinds.aboutToShow.emit()
    listed = [action.text() for action in kinds.actions() if not action.isSeparator()]

    assert listed == ["新建旅行…", "京都五日"]
    chosen = next(action for action in kinds.actions() if action.text() == "京都五日")
    chosen.trigger()

    assert library.events.photos_of_event(event.id) == tuple(_paths(window, "京都-01.jpg"))


def test_leaving_an_event(qt_app: QApplication, window: MainWindow) -> None:
    """「从这次事件移出 → 京都五日」把它摘掉。"""
    library = window._library
    assert library is not None
    event = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg", "京都-02.jpg"), event.id)
    row = _select(window, "京都-01.jpg")
    leave = _submenu(_submenu(_context(window, row), "记录为"), "从这次事件移出")
    leave.aboutToShow.emit()

    assert leave.isEnabled()
    chosen = next(action for action in leave.actions() if action.text() == "京都五日")
    chosen.trigger()

    assert set(library.events.photos_of_event(event.id)) == set(_paths(window, "京都-02.jpg"))


# ---- 分组 ---------------------------------------------------------------------


def test_grouping_by_event_shows_a_card_per_event(qt_app: QApplication, window: MainWindow) -> None:
    """按事件分组：旅行变成一张组卡片，「未归入事件」装剩下那些（ADR 0043 决策 7）。"""
    library = window._library
    assert library is not None
    trip = library.create_event(
        "trip", EventDraft(values={"title": "京都五日", "started_on": "2026-05-16"})
    ).event
    library.bind_photos(_paths(window, "京都-01.jpg"), trip.id)

    window.set_group_facet("event")
    wait_until(qt_app, lambda: window._model.group_count() > 0)
    labels = [group.label for group in _groups(window)]

    assert "京都五日" in labels
    assert "未归入事件" in labels
    # 组数可以大于照片数 —— 一张照片可以属于几个事件（与关键词同款的多归属）。
    assert window._model.group_count() >= 2


def _groups(window: MainWindow) -> list[GroupRef]:
    return [entry for entry in window._model.entries() if isinstance(entry, GroupRef)]


def test_recording_a_photo_regroups_right_away(qt_app: QApplication, window: MainWindow) -> None:
    """**记完之后组卡片当场就变了** —— 那一道重排闸（``_reorder_events``）漏了的话，
    用户看到的就是「记完什么都没发生」（ADR 0043 决策 7）。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    window.set_group_facet("event")
    # **先把这一层等定**：按事件分组要起一个后台任务去取那份映射，而它在飞的时候绑定一张照片，
    # 那个任务回来时会带着新数据 —— 于是「分组变了」是它干的，不是 :meth:`_after_events_changed`，
    # 这条守护就成了恒真的（注入违规照样绿）。
    wait_for_idle(qt_app, window, what="按事件分组")
    assert "京都五日" not in [group.label for group in _groups(window)]

    library.bind_photos(_paths(window, "京都-01.jpg"), trip.id)
    window._after_events_changed()
    wait_until(qt_app, lambda: "京都五日" in [group.label for group in _groups(window)])

    assert "京都五日" in [group.label for group in _groups(window)]


def test_grouping_by_event_does_not_read_exiftool(qt_app: QApplication, window: MainWindow) -> None:
    """按事件分组**不重读一遍 EXIF** —— 它既不在文件上也不在 EXIF 里（ADR 0043 决策 7）。

    放行它的判据是 ``facets.needs_metadata``：漏了那一条，用户点一次「按事件分组」就会先起
    一个后台任务把整库 EXIF 读一遍。
    """
    from gallery.core.metadata.facets import group_facet, needs_metadata

    facet = group_facet("event")

    assert facet is not None
    assert needs_metadata((facet,)) is False

    # **量的是「有没有真去读 EXIF」**，而不是横幅上有没有出现过那句话：横幅收走之后
    # ``_progress_notice_text`` 回到 ``None``，两种情况在它身上长得一样。
    library = window._library
    assert library is not None
    reads: list[int] = []
    real = library.photo_facets

    def counted(*args: object, **kwargs: object) -> object:
        reads.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    library.photo_facets = counted  # type: ignore[method-assign]
    try:
        window.set_group_facet("event")
        wait_for_idle(qt_app, window, what="按事件分组")
    finally:
        library.photo_facets = real  # type: ignore[method-assign]

    assert reads == [], "按事件分组重读了一遍 EXIF：它既不在文件上也不在 EXIF 里"


# ---- 撤销 ---------------------------------------------------------------------


def test_editing_an_event_can_be_undone(qt_app: QApplication, window: MainWindow) -> None:
    """改一个事件之后按 ⌘Z：字段回到改之前，而且**只值一次**（公共列与扩展表在同一次写里）。"""
    library = window._library
    assert library is not None
    event = library.create_event(
        "trip", EventDraft(values={"title": "京都五日", "destination": "京都"})
    ).event
    library.update_event(
        event.id, EventDraft(values={"title": "改过的名字", "destination": "大阪"})
    )
    window.undo_last_write()

    restored = library.event(event.id)
    assert restored is not None
    assert restored.title == "京都五日"
    assert restored.text("destination") == "京都"


def test_deleting_an_event_comes_back_with_its_photos(
    qt_app: QApplication, window: MainWindow
) -> None:
    """撤销删除：事件与它的绑定都回来。少了绑定，用户要等到某天按事件筛才发现
    （ADR 0043 决策 5）。"""
    library = window._library
    assert library is not None
    event = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg"), event.id)
    library.delete_event(event.id)

    window.undo_last_write()

    restored = library.event(event.id)
    assert restored is not None
    assert set(library.events.photos_of_event(event.id)) == set(_paths(window, "京都-01.jpg"))


# ---- 事件分组卡片上的那两行（ADR 0043 决策 8）---------------------------------


def test_the_event_group_card_offers_editing_and_leaving(
    qt_app: QApplication, window: MainWindow
) -> None:
    """事件分组卡片上多出「编辑事件…」与「从这次事件移出」—— 那一组就是某个事件，
    改个名不必退回照片逐张右键。别的分组卡片上它们不出现。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg"), trip.id)
    window.set_group_facet("event")
    wait_until(qt_app, lambda: window._model.group_count() > 0)
    card_row = next(
        row
        for row in range(window._model.rowCount())
        if (group := window._model.group_at(row)) is not None and group.label == "京都五日"
    )

    menu = _context(window, card_row)

    assert "编辑事件…" in _texts(menu)
    assert "从这次事件移出" in _texts(menu)
    assert "复制分组条件" in _texts(menu)


def test_an_ordinary_group_card_has_no_event_entries(
    qt_app: QApplication, window: MainWindow
) -> None:
    """按年份分组的卡片上那两行**不出现** —— 那不是事件，改它的名字是别的意思。"""
    window.set_group_facet("captured_year")
    wait_until(qt_app, lambda: window._model.group_count() > 0)
    card_row = next(row for row in range(window._model.rowCount()) if window._model.group_at(row))

    menu = _context(window, card_row)

    assert "编辑事件…" not in _texts(menu)
    assert "从这次事件移出" not in _texts(menu)


def test_leaving_the_event_from_its_card(qt_app: QApplication, window: MainWindow) -> None:
    """卡片上那一条把**卡片背后那批照片**摘掉，事件本身留着。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg", "京都-02.jpg"), trip.id)
    window.set_group_facet("event")
    wait_until(qt_app, lambda: window._model.group_count() > 0)
    card_row = next(
        row
        for row in range(window._model.rowCount())
        if (group := window._model.group_at(row)) is not None and group.label == "京都五日"
    )
    # **先弹一次右键**：视图会把选区摆到那一格（当前项也就跟着过去了），而那两条动作认的正是
    # 当前项背后的事件 —— 与用户在真实菜单里点它们时走的是同一条路。
    _context(window, card_row)
    window._leave_event_action.trigger()
    wait_until(qt_app, lambda: library.events.photos_of_event(trip.id) == ())

    assert library.event(trip.id) is not None
    assert library.events.photos_of_event(trip.id) == ()


def test_editing_an_event_from_its_card(qt_app: QApplication, window: MainWindow) -> None:
    """卡片上的「编辑事件…」摆出同一个面板，填完写回库里。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg"), trip.id)
    window.set_group_facet("event")
    wait_until(qt_app, lambda: window._model.group_count() > 0)
    card_row = next(
        row
        for row in range(window._model.rowCount())
        if (group := window._model.group_at(row)) is not None and group.label == "京都五日"
    )
    _context(window, card_row)

    def rename() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, EventEditDialog)
        dialog._fields["title"].setText("京都七日")
        dialog._on_accept()

    QTimer.singleShot(0, rename)
    window._edit_event_action.trigger()
    wait_until(qt_app, lambda: library.all_events()[0].title == "京都七日")

    assert library.all_events()[0].title == "京都七日"


# ---- 删除 ---------------------------------------------------------------------


def test_deleting_an_event_asks_first_and_can_be_undone(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除要确认框（废纸篓那一支不确认是因为它自己就是保险层，事件没有），撤得回来。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    library.bind_photos(_paths(window, "京都-01.jpg"), trip.id)
    asked: list[str] = []

    def ask(self, title, text, *args, **kwargs):  # noqa: ANN001 - QMessageBox.question
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", ask)
    window.delete_event(trip.id)

    assert asked and "京都五日" in asked[0]
    assert library.event(trip.id) is None
    window.undo_last_write()
    assert library.event(trip.id) is not None
    assert set(library.events.photos_of_event(trip.id)) == set(_paths(window, "京都-01.jpg"))


def test_answering_no_keeps_the_event(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点「取消」就什么都不做 —— 不留痕，也不必撤销。"""
    library = window._library
    assert library is not None
    trip = library.create_event("trip", EventDraft(values={"title": "京都五日"})).event
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Cancel
    )

    window.delete_event(trip.id)

    assert library.event(trip.id) is not None


# ---- 面板自己（不碰任何一张照片）-----------------------------------------------


def test_the_panel_is_built_from_the_field_definitions(qt_app: QApplication) -> None:
    """**控件由事件的定义生成**：旅行的六个独有字段加上四个公共字段，一样不少
    （ADR 0043 决策 3）。"""
    dialog = EventEditDialog(None, TRIP)

    assert sorted(dialog._fields) == sorted(field.id for field in fields_of(TRIP))


def test_a_rating_field_comes_up_as_a_row_of_five_buttons(qt_app: QApplication) -> None:
    """有值域的字段画成**一排按钮**（用户要的是「一排 5 个按钮，铺满这一排」）。

    点一下选中，再点一下取消 —— 评分是可不记的，所以「都不选」也是合法状态，
    不再需要下拉里那个空选项。
    """
    dialog = EventEditDialog(None, TRIP)
    dialog.show()
    qt_app.processEvents()
    choice = dialog._fields["rating"]

    assert isinstance(choice, ChoiceButtons)
    buttons = [choice._buttons[str(score)] for score in range(1, 6)]
    assert [button.text() for button in buttons] == ["1", "2", "3", "4", "5"]
    assert all(not button.isChecked() for button in buttons)
    # 铺满这一排：五个平分（整数宽度除不尽时最多差 1 点），最右那个顶到右缘。
    widths = [button.width() for button in buttons]
    assert max(widths) - min(widths) <= 1
    assert widths[0] > buttons[0].sizeHint().width()  # 被拉宽了，而不是各占自己那一小块
    assert buttons[-1].geometry().right() >= choice.width() - 2
    assert choice.width() == dialog._fields["title"].width()  # 与其余输入框同宽（铺满那一列）

    buttons[2].click()
    assert choice.value() == "3"
    buttons[0].click()
    assert choice.value() == "1"
    buttons[0].click()
    assert choice.value() == ""


def test_the_checked_rating_wears_the_popup_highlight(qt_app: QApplication) -> None:
    """评分按钮选中时用**与弹出菜单、候选列表同一档**的高亮：底色 ``Highlight``、字色
    ``HighlightedText``（由 ``theme`` 钉死，ADR 0035）。判据是「与下发的那一份相等」，不写死颜色。
    """
    dialog = EventEditDialog(None, TRIP)
    palette = dialog.palette()
    highlight = rgba(palette.color(QPalette.ColorRole.Highlight))
    highlighted_text = rgba(palette.color(QPalette.ColorRole.HighlightedText))

    checked = _style_rule(dialog.styleSheet(), "QPushButton#eventEditChoice:checked")

    assert f"background: {highlight}" in checked
    assert f"color: {highlighted_text}" in checked


def _style_rule(sheet: str, selector: str) -> str:
    """样式表里某一条选择器的规则体（``{`` 与 ``}`` 之间）。找不到就是空串 —— 断言因此会失败。"""
    start = sheet.find(selector)
    if start < 0:
        return ""
    body = sheet.find("{", start)
    end = sheet.find("}", body)
    return sheet[body:end] if 0 <= body < end else ""


def test_the_name_grows_by_itself_until_the_user_types_one(qt_app: QApplication) -> None:
    """名称栏自动长成「目的地 · 日期」，**用户动过它之后就不再自动改** —— 手写的名字是他的意思。"""
    dialog = EventEditDialog(None, TRIP)
    dialog._fields["destination"].setText("京都, 奈良")
    dialog._fields["started_on"].setText("2026-05-16")
    assert dialog._text("title") == "京都、奈良 · 2026-05-16"

    dialog._fields["title"].setText("我自己起的名字")
    dialog._fields["destination"].setText("大阪")

    assert dialog._text("title") == "我自己起的名字"


def test_clearing_the_generated_name_keeps_it_clear(qt_app: QApplication) -> None:
    """把自动生成的名称删掉之后**不许自己长回来** —— 否则用户永远输不进自己的名字。

    删空与「还没填」在字符上一样，所以判据只能是「它是不是上次自动生成的那一句」。
    """
    dialog = EventEditDialog(None, TRIP)
    assert dialog._text("title") == "新的旅行"

    dialog._fields["title"].clear()
    assert dialog._text("title") == ""

    dialog._fields["title"].setText("2026 京都")
    dialog._fields["destination"].setText("京都")
    assert dialog._text("title") == "2026 京都"


def test_editing_an_existing_event_prefills_it(qt_app: QApplication) -> None:
    """改一个已有事件时那一格不少，而「按日期范围加入」收起来（那里已经在讨论改内容了）。"""
    event = Event(
        id=1,
        kind="trip",
        created_at=0,
        modified_at=0,
        values={
            "title": "京都五日",
            "started_on": "2026-05-16",
            "destination": "京都、奈良",
            "cost": 3280.5,
            "rating": 5,
        },
    )

    dialog = EventEditDialog(None, TRIP, event=event)

    assert dialog._text("title") == "京都五日"
    assert dialog._text("destination") == "京都、奈良"
    assert dialog._text("cost") == "3280.5"
    rating = dialog._fields["rating"]
    assert isinstance(rating, ChoiceButtons)
    assert rating.value() == "5"
    assert dialog._include_range.isChecked() is False


def test_a_bad_date_keeps_the_dialog_open(qt_app: QApplication) -> None:
    """**校验不过就不关自己** —— 关掉了用户填的那一份就白填了（与元数据编辑器同一套）。"""
    dialog = EventEditDialog(None, TRIP)
    dialog._fields["started_on"].setText("去年夏天")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "开始日期看不懂" in dialog._error.text()
    assert dialog.edit().draft.text("destination") == ""


def test_an_end_before_the_start_is_reported(qt_app: QApplication) -> None:
    dialog = EventEditDialog(None, TRIP)
    dialog._fields["started_on"].setText("2026-05-16")
    dialog._fields["ended_on"].setText("2026-05-10")

    dialog._on_accept()

    assert "还早" in dialog._error.text()


def test_a_good_draft_accepts_and_carries_the_range_flag(qt_app: QApplication) -> None:
    dialog = EventEditDialog(None, TRIP)
    dialog._fields["destination"].setText("京都")
    dialog._fields["started_on"].setText("2026-05-16")
    dialog._fields["ended_on"].setText("2026-05-20")

    dialog._on_accept()
    edit = dialog.edit()

    assert dialog.result() != 0
    assert edit.include_range is True
    assert edit.first_day == "2026-05-16"
    assert edit.last_day == "2026-05-20"


def test_the_range_flag_needs_a_start_day(qt_app: QApplication) -> None:
    """勾着但没填开始日期 → 那一次整库查询没有意义，而 ``first_day`` 是空的（不会去查）。"""
    dialog = EventEditDialog(None, TRIP)

    assert dialog.edit().include_range is False


def test_one_end_day_is_used_when_the_end_is_left_empty(qt_app: QApplication) -> None:
    """当天来回是最常见的那种 —— 结束日期留空就与头一天同一天。"""
    dialog = EventEditDialog(None, TRIP)
    dialog._fields["started_on"].setText("2026-05-16")

    assert dialog.edit().last_day == "2026-05-16"
