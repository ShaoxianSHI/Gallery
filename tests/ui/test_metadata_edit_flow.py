"""「编辑元数据」的界面测试（ADR 0030 / 0031 / 0032）：菜单摆不摆得对、对话框自己
（预填、读成 ``PlaceEdit``、不合法时不关自己）、以及写完之后横幅/模型/⌘Z 都跟上。

**不碰真 exiftool**：窗口建好之后把库里的 writer 换成一份内存里的假表，于是这条链在没装
exiftool 的机器上也照样跑。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from outline_helpers import assert_no_darker_dots  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
)
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.errors import MetadataWriteError  # noqa: E402
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata.capture_time import CaptureTime, CaptureTimeEdit  # noqa: E402
from gallery.core.metadata.journal import read_records  # noqa: E402
from gallery.core.metadata.metadata_edit import MetadataEdit, MetadataState  # noqa: E402
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState, plan_place_edit  # noqa: E402
from gallery.core.metadata.report import build_report  # noqa: E402
from gallery.core.metadata.writeback import Assignment  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.dialogs.metadata_edit import FIELD_RADIUS, MetadataEditDialog  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.outline import opaque_over  # noqa: E402
from gallery.ui.viewer.metadata import CONTENT_PAD, MetadataPanel  # noqa: E402

PHOTO = "一张.jpg"


class FakeWriter:
    """一份内存里的「文件」。写得进去、读得回来，够界面这一层用了。"""

    def __init__(self) -> None:
        self.fields: dict[str, str] = {}
        self.available_flag = True

    def available(self) -> bool:
        return self.available_flag

    def read(self, path: Path) -> Mapping[str, str]:
        return dict(self.fields)

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        for item in assignments:
            if item.value:
                self.fields[item.tag] = item.value
            else:
                self.fields.pop(item.tag, None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "照片"
    (library / "京都").mkdir(parents=True)
    Image.new("RGB", (16, 12), (200, 120, 40)).save(library / "京都" / PHOTO)
    prepare_library(library)
    return library


@pytest.fixture
def writer() -> FakeWriter:
    return FakeWriter()


@pytest.fixture
def window(qt_app: QApplication, root: Path, writer: FakeWriter) -> Iterator[MainWindow]:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance._library._writer = writer  # 不起真的 exiftool（测试不得依赖本机装没装）
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描与地点解析")
    yield instance
    instance.close()


def _enter_album(qt_app: QApplication, window: MainWindow, root: Path) -> int:
    """进那个相册并等这一层排好，返回照片那一行的行号。

    列一层是**后台**跑的：转一圈事件循环不等于扫完了，所以必须等 ``row_of_path`` 真的能
    给出行号。
    """
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    window.enter_folder(root / "京都")
    path = root / "京都" / PHOTO

    def listed() -> bool:
        return window._model.row_of_path(path) >= 0

    wait_until(qt_app, listed)
    row = window._model.row_of_path(path)
    window._grid.select_row(row)
    return row


@pytest.fixture
def focused(qt_app: QApplication, window: MainWindow, root: Path) -> MainWindow:
    """停在大图页、正在看那一张 —— 「编辑元数据」作用的对象（ADR 0021）。"""
    row = _enter_album(qt_app, window, root)
    window._open_detail(window._model.index(row, 0))
    wait_until(qt_app, lambda: window._in_detail_page())
    return window


def _context_at(window: MainWindow, row: int) -> list[str]:
    """模拟一次真实的右键并读出菜单上的字。

    **两步都要有**：视图先把选区摆正（``PhotoGrid.contextMenuEvent``），窗口再拼菜单 ——
    少了前一步，「重命名」「移到废纸篓」会因「什么都没选中」而正确地置灰，
    测的就不是用户看到的那一份。
    """
    assert window._grid.select_for_context(row) == row
    menu = window.context_menu_for(row)
    assert menu is not None
    return [action.text() for action in menu.actions()]


def _photo_ref(window: MainWindow, root: Path) -> object:
    row = window._model.row_of_path(root / "京都" / PHOTO)
    return window._model.photo_at(row)


def test_the_photo_menu_has_it_in_the_write_group(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """它落在**「会动文件」**那一段，与重命名、移到废纸篓同族：改的是照片里的字节。"""
    row = _enter_album(qt_app, window, root)
    texts = _context_at(window, row)

    assert "编辑元数据…" in texts
    assert texts.index("编辑元数据…") > texts.index("重命名…")
    assert texts.index("编辑元数据…") < texts.index("移到废纸篓")


def test_a_group_card_has_no_metadata_editor(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """分组卡片不是磁盘上的东西 —— 「编辑它的元数据」说不清指的是哪一张（ADR 0020）。"""
    _enter_album(qt_app, window, root)
    member = _photo_ref(window, root)
    window._model.reorder(
        [GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(member,))]  # type: ignore[arg-type]
    )

    assert "编辑元数据…" not in _context_at(window, 0)


def test_it_disappears_when_exiftool_is_missing(
    qt_app: QApplication, window: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """没装 exiftool 时**不出现**，而不是摆一条点了只会报错的（ADR 0014 决策 4）。"""
    writer.available_flag = False
    # 探测结果**按设计只算一次**（`Library.exiftool_available`），而 writer 是窗口建好、
    # ``wait_for_idle`` 跑完之后才注入的 —— 那时答案已经缓存成「能用」，翻转后要作废重来。
    window._library._exiftool_available = None
    row = _enter_album(qt_app, window, root)
    qt_app.processEvents()

    assert "编辑元数据…" not in _context_at(window, row)


def _dialog(
    window: MainWindow,
    place: PlaceState | None = None,
    *,
    capture: CaptureTime | None = None,
    **kwargs: object,
) -> MetadataEditDialog:
    """摆一个对话框。**位置是默认的那一半**，时间用 ``capture=`` 单独传（ADR 0032）。"""
    state = MetadataState(capture=capture or CaptureTime(), place=place or PlaceState())
    return MetadataEditDialog(None, state, **kwargs)  # type: ignore[arg-type]


def test_the_dialog_prefills_what_the_photo_says(qt_app: QApplication) -> None:
    dialog = _dialog(
        None,  # type: ignore[arg-type]
        PlaceState(
            city="南京", region="江苏省", country="中国", latitude=32.0603, longitude=118.7969
        ),
        capture=CaptureTime("2024:05:16 20:52:02", "+08:00"),
    )

    assert dialog._country.text() == "中国"
    assert dialog._region.text() == "江苏省"
    assert dialog._city.text() == "南京"
    assert dialog._latitude.text().startswith("32.0603")
    assert dialog._longitude.text().startswith("118.7969")
    # **拍摄时间在最上面两栏**，写成连字符（EXIF 的冒号只存在于文件里）
    assert dialog._taken_at.text() == "2024-05-16 20:52:02"
    assert dialog._offset.text() == "+08:00"


def test_a_photo_without_a_capture_time_leaves_the_offset_box_empty(qt_app: QApplication) -> None:
    """**照片没记时区时，那一栏就是空的** —— 绝不按本机时区替用户补一个。

    猜错的后果是排序整体偏几个小时（ADR 0032 决策 2），比空着糟糕得多。
    """
    dialog = _dialog(None, PlaceState(city="南京"), capture=CaptureTime("2024:05:16 20:52:02"))

    assert dialog._taken_at.text() == "2024-05-16 20:52:02"
    assert dialog._offset.text() == ""


def test_an_unreadable_capture_time_keeps_the_dialog_open(qt_app: QApplication) -> None:
    """**不弹窗、不关自己**：关掉了用户填的别的内容就白填了。"""
    dialog = _dialog(None, PlaceState(city="南京"))
    dialog._taken_at.setText("去年夏天")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "无法识别拍摄时间" in dialog._error.text()


def test_a_day_that_does_not_exist_is_refused(qt_app: QApplication) -> None:
    """**二月三十一号不认** —— 一个不存在的时间比没有时间糟糕得多，而它是手滑就能敲出来的。"""
    dialog = _dialog(None, PlaceState(city="南京"))
    dialog._taken_at.setText("2024-02-31 10:00:00")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "无法识别拍摄时间" in dialog._error.text()


def test_an_unreadable_offset_keeps_the_dialog_open(qt_app: QApplication) -> None:
    dialog = _dialog(None, PlaceState(city="南京"))
    dialog._offset.setText("东八区")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "无法识别时区" in dialog._error.text()


def test_the_clear_button_leaves_the_capture_time_alone(qt_app: QApplication) -> None:
    """**「清空位置」只清位置** —— 删掉一张照片的拍摄时间是另一件更大的事（ADR 0032）。

    键名已经说了「位置」，而删拍摄时间的方式是把上面那两个框清空 —— 与位置同一套语义。
    """
    dialog = _dialog(
        None,
        PlaceState(city="苏州", region="江苏省", country="中国", latitude=31.3, longitude=120.6),
        capture=CaptureTime("2024:05:16 20:52:02", "+08:00"),
    )

    dialog._clear_button.click()

    assert dialog._city.text() == ""
    assert dialog._taken_at.text() == "2024-05-16 20:52:02"
    assert dialog._offset.text() == "+08:00"


def test_the_dialog_returns_the_final_state_not_a_patch(qt_app: QApplication) -> None:
    """``MetadataEdit`` 收的是**最终状态**：空框表示「清空那一层」，不是「别动它」。

    这正是对话框打开时要把三层地名与坐标全填上的原因。
    """
    dialog = _dialog(None, PlaceState(city="南京", latitude=32.06, longitude=118.79))  # type: ignore[arg-type]

    result = dialog.edit()

    assert result.place == PlaceEdit(city="南京", latitude=32.06, longitude=118.79)


def test_emptying_both_coordinate_boxes_means_remove_them(qt_app: QApplication) -> None:
    """定位漂了、地名没错 —— 清空两个坐标框要能被表达成「删坐标」。"""
    dialog = _dialog(None, PlaceState(city="南京", latitude=32.06, longitude=118.79))  # type: ignore[arg-type]
    dialog._latitude.clear()
    dialog._longitude.clear()

    assert dialog._coordinates_emptied()
    assert dialog.edit().place.clear_coordinates


def test_an_impossible_coordinate_keeps_the_dialog_open(qt_app: QApplication) -> None:
    """**不弹窗、不关自己**：那行红字就够，关掉的话用户填的别的内容全丢了。"""
    dialog = _dialog(None, PlaceState(city="南京"))  # type: ignore[arg-type]
    dialog._latitude.setText("91")
    dialog._longitude.setText("120")

    dialog._on_accept()

    assert dialog.result() == 0  # 还没 Accepted
    assert "纬度超出范围" in dialog._error.text()


def test_half_a_coordinate_is_refused_too(qt_app: QApplication) -> None:
    dialog = _dialog(None, PlaceState(city="南京"))  # type: ignore[arg-type]
    dialog._latitude.setText("32.06")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "需同时填写" in dialog._error.text()


def _write(
    window: MainWindow,
    ref: object,
    state: PlaceState | None = None,
    edit: PlaceEdit | None = None,
    *,
    capture: CaptureTime | None = None,
    capture_edit: CaptureTimeEdit | None = None,
) -> None:
    """替掉对话框，直接把一份 ``MetadataEdit`` 交给写回那条路。"""
    assert window._library is not None
    window._apply_metadata_edit(  # type: ignore[arg-type]
        ref,
        MetadataState(capture=capture or CaptureTime(), place=state or PlaceState()),
        MetadataEdit(capture=capture_edit or CaptureTimeEdit(), place=edit or PlaceEdit()),
    )


def test_writing_updates_the_file_the_row_and_the_banner(
    qt_app: QApplication, focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    ref = _photo_ref(focused, root)

    _write(
        focused,
        ref,
        PlaceState(),
        PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    assert writer.fields["XMP:City"] == "杭州"
    assert writer.fields["IPTC:Country-PrimaryLocationName"] == "中国"
    row = focused._model.row_of_path(root / "京都" / PHOTO)
    assert focused._model.photo_at(row) is not None
    assert focused._undo_action.text() == "撤销 编辑元数据"
    # 横幅只有一句：「接下来会发生什么」是对话框里那行小字的事，保存之前就说过了。
    assert focused._banner.text() == "已更新「一张.jpg」的位置；⌘Z 可撤销。"


def test_the_journal_has_the_field_level_record(
    focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    # 照对话框按完「地点 → 经纬度」之后那样：三层地名与两个坐标框都有值。
    # ``MetadataEdit`` 是最终状态，空的那一层意思是「清掉它」。
    _write(
        focused,
        _photo_ref(focused, root),
        PlaceState(),
        PlaceEdit(
            city="杭州", region="浙江省", country="中国", latitude=30.2741, longitude=120.1551
        ),
    )

    records = read_records(root)
    # **成对**：先记「打算改」，动完文件再记终态（写入与落盘之间那一下崩溃不能什么都不留下）。
    # 两记的是同一件事，撤销栈按「终态顶掉 pending」处理，用户按一次 ⌘Z 就够。
    assert [record.action for record in records] == ["metadata", "metadata"]
    assert [record.result for record in records] == ["pending", "ok"]
    assert records[0].changes == records[1].changes
    # 地名与坐标都在记录里，各带原值。「删掉海拔与定位时间」那三条**不在里面**：
    # 这张照片本来就没有它们，空操作不该进日志。
    assert [change.tag for change in records[0].changes] == [
        "EXIF:GPSLatitude",
        "EXIF:GPSLatitudeRef",
        "EXIF:GPSLongitude",
        "EXIF:GPSLongitudeRef",
        "XMP:City",
        "IPTC:City",
        "XMP:State",
        "IPTC:Province-State",
        "XMP:Country",
        "IPTC:Country-PrimaryLocationName",
    ]
    assert records[0].changes[0].before is None  # 原来没有坐标


def test_undo_puts_the_original_value_back(
    qt_app: QApplication, focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """⌘Z 撤得回去 —— 它撤销的是**字段**，于是界面必须先记下原来是什么。"""
    writer.fields["XMP:City"] = "南京"
    ref = _photo_ref(focused, root)
    _write(focused, ref, PlaceState(city="南京"), PlaceEdit(city="杭州"))
    assert writer.fields["XMP:City"] == "杭州"

    focused.undo_last_write()

    assert writer.fields["XMP:City"] == "南京"


def test_writing_the_capture_time_goes_through_the_whole_way(
    focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """**改拍摄时间走的是同一条路**：写盘、记账、横幅、⌘Z，一个都不少。

    ``DateTimeOriginal`` 与 ``OffsetTimeOriginal`` 与坐标一样必记账、必可撤销 ——
    少了这一条，「写回就等于撤销得回」这句话对它们是假的（ADR 0032）。
    """
    writer.fields["EXIF:DateTimeOriginal"] = "2024:05:16 20:52:02"
    _write(
        focused,
        _photo_ref(focused, root),
        capture=CaptureTime("2024:05:16 20:52:02"),
        capture_edit=CaptureTimeEdit("2003:07:19 09:12:00", "+09:00"),
    )

    assert writer.fields["EXIF:DateTimeOriginal"] == "2003:07:19 09:12:00"
    assert writer.fields["EXIF:OffsetTimeOriginal"] == "+09:00"
    # 横幅说的是**改了哪一部分**，不是笼统的「已更新元数据」。
    assert focused._banner.text() == "已更新「一张.jpg」的拍摄时间；⌘Z 可撤销。"
    changes = {(change.tag, change.before) for change in read_records(root)[-1].changes}
    assert ("EXIF:DateTimeOriginal", "2024:05:16 20:52:02") in changes


def test_undo_puts_the_capture_time_back(
    focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """**时间也撤得回** —— 字段级原值就是为它准备的。"""
    writer.fields["EXIF:DateTimeOriginal"] = "2024:05:16 20:52:02"
    _write(
        focused,
        _photo_ref(focused, root),
        capture=CaptureTime("2024:05:16 20:52:02"),
        capture_edit=CaptureTimeEdit("2003:07:19 09:12:00", "+09:00"),
    )

    focused.undo_last_write()

    assert writer.fields["EXIF:DateTimeOriginal"] == "2024:05:16 20:52:02"
    assert "EXIF:OffsetTimeOriginal" not in writer.fields  # 原来就没有，撤回去也就是没有


def test_time_and_place_go_out_in_one_write(
    focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """一次保存 = 一条日志 = 一下 ⌘Z（ADR 0032 决策 4）。

    分成两次写回的话，用户按了一次保存却要撤两下、横幅也要说两遍 —— 那不是一次操作。
    """
    writer.fields["EXIF:DateTimeOriginal"] = "2024:05:16 20:52:02"
    _write(
        focused,
        _photo_ref(focused, root),
        PlaceState(),
        PlaceEdit(city="杭州", region="浙江省", country="中国"),
        capture=CaptureTime("2024:05:16 20:52:02"),
        capture_edit=CaptureTimeEdit("2003:07:19 09:12:00"),
    )

    records = read_records(root)
    assert [record.result for record in records] == ["pending", "ok"]
    tags = {change.tag for change in records[-1].changes}
    assert {"EXIF:DateTimeOriginal", "XMP:City"}.issubset(tags)
    # 一句话里说清两样都改了
    assert focused._banner.text() == "已更新「一张.jpg」的拍摄时间与位置；⌘Z 可撤销。"

    focused.undo_last_write()

    assert writer.fields["EXIF:DateTimeOriginal"] == "2024:05:16 20:52:02"
    assert writer.fields.get("XMP:City") != "杭州"


def test_a_refused_write_says_why_and_changes_nothing(
    qt_app: QApplication, focused: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写不进去时**如实说**：横幅上是那句说人话的错，而不是「发生错误」。

    日志里留一条 ``failed`` 是故意的（与改名 / 移到废纸篓同一套）：用户回头翻历史时，
    「我刚才试过，没成」也该看得见 —— 它记着的是那次尝试，不是成功。
    """

    class Refuses:
        """一个只会说「不给写」的工具。"""

        def available(self) -> bool:
            return True

        def read(self, path: Path) -> Mapping[str, str]:
            return {}

        def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
            raise MetadataWriteError("没有权限写入「一张.jpg」：它或它所在的磁盘可能被设成了只读。")

    assert focused._library is not None
    monkeypatch.setattr(focused._library, "_writer", Refuses())
    before = len(read_records(root))

    # 照对话框那样把三层都填上 —— ``MetadataEdit`` 是最终状态，空的那一层意思是「清掉它」。
    _write(
        focused,
        _photo_ref(focused, root),
        PlaceState(),
        PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    records = read_records(root)
    # 「打算改」与「没改成」两条都在：原值留在日志里，人才有得救。
    assert len(records) == before + 2
    assert [record.result for record in records[-2:]] == ["pending", "failed"]
    assert records[-2].changes, "原值没了就再也写不回去"
    assert records[-1].result == "failed"
    assert "只读" in records[-1].message
    assert "没有权限写入" in focused._banner.text()


def test_the_panel_offers_the_editor(qt_app: QApplication, focused: MainWindow) -> None:
    """页脚那个按钮是同一个入口（点它与 ⇧⌘I 走的是同一条路）。

    **它摆在页脚，不摆在「位置」那一组里**：照片**本来就没有位置**的时候面板上根本没有
    「位置」那一组，入口也就跟着没了 —— 而那恰恰是最需要补一个位置的时候。
    """
    window = focused
    window.toggle_metadata(True)
    wait_until(qt_app, lambda: window._panel.isVisible())

    panel = window._panel
    assert panel.metadata_editable
    assert panel._edit_button.parentWidget() is panel._footer  # 摆在页脚那一行
    assert panel._edit_button.isEnabled()
    # 页脚在读到元数据之前是藏着的（那时照片还没选中，几句话说了也没用）。
    wait_until(qt_app, lambda: not panel._footer.isHidden())


class StubResolver:
    """城市库的替身：按名字给坐标，按坐标给地点。"""

    def __init__(self) -> None:
        self.by_name = {
            "苏州": ("苏州", "江苏省", "中国", 31.311, 120.621),
            "京都": ("京都", "京都府", "日本", 35.021, 135.754),
        }

    @property
    def ready(self) -> bool:
        return True

    def place_for(self, latitude: float, longitude: float, *, status: object = None) -> object:
        if abs(latitude - 31.311) < 0.5:
            return _Resolved("苏州", "江苏省", "中国", 2.4)
        return None

    def place_named(
        self,
        name: str,
        *,
        region: str | None = None,
        country: str | None = None,
        status: object = None,
    ) -> tuple[object, ...]:
        found = self.by_name.get(name.strip())
        if found is None:
            return ()
        city, province, nation, latitude, longitude = found
        return (_Place(city, province, nation, latitude, longitude),)


class _Resolved:
    def __init__(self, city: str, region: str | None, country: str, distance_km: float) -> None:
        self.city = city
        self.region = region
        self.country = country
        self.distance_km = distance_km


class NoRegionResolver(StubResolver):
    """反查到**直辖市** —— 城市有、国家有、**省是空的**。

    随包那份城市库里北京、上海、天津、重庆、香港、澳门这 151 条就是这样
    （``_City.region`` 允许为 ``None``），而它正是「界面显示的」与「写下去的」
    分道扬镳的那条路。
    """

    def place_for(self, latitude: float, longitude: float, *, status: object = None) -> object:
        if abs(latitude - 31.2304) < 0.5:
            return _Resolved("上海", None, "中国", 0.4)
        return None


class _Place:
    def __init__(
        self, city: str, region: str | None, country: str, latitude: float, longitude: float
    ) -> None:
        self.city = city
        self.region = region
        self.country = country
        self.latitude = latitude
        self.longitude = longitude


class _StaleAreaResolver(StubResolver):
    """只有「不按范围查」才找得到 —— 用来锁定上面那条回退重查。"""

    def place_named(
        self,
        name: str,
        *,
        region: str | None = None,
        country: str | None = None,
        status: object = None,
    ) -> tuple[object, ...]:
        if region or country:
            return ()
        return super().place_named(name)


@pytest.fixture
def resolver() -> StubResolver:
    return StubResolver()


def _row_positions(dialog: QDialog) -> dict[str, int]:
    """表单里每个标签的纵坐标 —— 用户看到的就是这个顺序。"""
    dialog.adjustSize()
    QApplication.processEvents()
    positions: dict[str, int] = {}
    for label in dialog.findChildren(QLabel):
        if label.text() in {"国家", "省 / 州", "城市", "纬度", "经度"}:
            positions[label.text()] = label.mapTo(dialog, label.rect().topLeft()).y()
    return positions


def test_the_city_box_is_a_plain_input(qt_app: QApplication) -> None:
    """**不是下拉。**

    那一栏和上面两栏一样是「填一个地方」，朝下的小箭头会让人以为清单是封闭的 ——
    而城市库里没有的名字恰恰是最该能填进去的那些。补全仍在，它只是**建议**。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert isinstance(dialog._city, QLineEdit)
    assert dialog._city.completer() is not None


def test_one_letter_does_not_run_a_look_up(qt_app: QApplication) -> None:
    """城市库里有一个真叫「苏」的条目。

    用户敲下第一个字就恰好命中它的话，屏幕上会立刻跳出「中国 · 安徽省」和一串坐标 ——
    那是数据里的碎片，不是用户要填的地方。**少于两个字不查、也不给候选。**
    """
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏")
    dialog._refresh()

    assert dialog._country.text() == ""
    assert dialog._latitude.text() == ""


def test_two_letters_are_enough_to_press_the_button(qt_app: QApplication) -> None:
    """**两个字母就够按那个按钮了** —— 门槛还在，只是从「自动」挪到了「手动」（ADR 0039）。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._names_to_coordinates_button.click()

    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)
    assert float(dialog._longitude.text()) == pytest.approx(120.621, abs=1e-4)


def test_one_letter_is_not_enough(qt_app: QApplication) -> None:
    """城市库里有一个真叫「苏」的条目（安徽某个被折叠进市里的区）：敲第一个字就查它，
    屏幕上会立刻跳出「中国 · 安徽省」和一串坐标 —— 那是数据里的碎片，不是用户要填的地方。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏")
    dialog._names_to_coordinates_button.click()

    assert dialog._latitude.text() == ""
    assert "城市" in dialog._error.text()


def test_typing_a_city_fills_in_nothing_until_the_button_is_pressed(
    qt_app: QApplication,
) -> None:
    """**敲字不再顺手改别的框**（ADR 0039 决策 1）。

    跨层填充只发生在按下按钮的那一刻：敲一个「苏州」就自己长出省与国家、填上坐标的话，
    「框里显示的」与「真正写下去的」在不同路上就不是同一份 —— 界面那三个
    ``if place.region and ...`` 查不到就不动，而写盘时那一层是被**清空**的。
    """
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._refresh()

    assert dialog._region.text() == ""
    assert dialog._country.text() == ""
    assert dialog._latitude.text() == ""


def test_typing_a_city_by_itself_writes_nothing(qt_app: QApplication) -> None:
    """只敲了城市而没按任何按钮：**一个坐标都不写进去**，保存也不会配上（ADR 0031 决策 3）。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())
    dialog._city.setText("苏州")

    plan = plan_place_edit(
        PlaceState(),
        PlaceEdit(city="苏州", region="", country="", latitude=None, longitude=None),
        resolver=StubResolver(),
    )

    assert [a.tag for a in plan.assignments if "GPS" in a.tag] == []


def test_the_fields_run_country_province_city(qt_app: QApplication) -> None:
    """**由大到小**：国家 → 省 / 州 → 城市。读地点就是这么读的。"""
    dialog = _dialog(None, PlaceState(city="苏州", region="江苏省", country="中国"))

    rows = _row_positions(dialog)
    assert rows["国家"] < rows["省 / 州"] < rows["城市"] < rows["纬度"] < rows["经度"]


def test_the_button_fills_in_the_province_and_the_country(qt_app: QApplication) -> None:
    """**只敲城市就够**：按一下按钮，省与国家按库里那一带的准确写法长出来。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._names_to_coordinates_button.click()

    assert dialog._region.text() == "江苏省"
    assert dialog._country.text() == "中国"


def test_a_place_updates_the_coordinates_when_the_button_is_pressed(
    qt_app: QApplication,
) -> None:
    """**地点 → 坐标**：按一下按钮，保存之前就能看见会写进去的是哪一对。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._names_to_coordinates_button.click()

    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)
    assert float(dialog._longitude.text()) == pytest.approx(120.621, abs=1e-4)


def test_coordinates_update_the_place_when_the_button_is_pressed(
    qt_app: QApplication,
) -> None:
    """**坐标 → 地点**：把坐标拖到苏州附近，按一下按钮，上面三栏说出「中国 · 江苏省 · 苏州」。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._latitude.setText("31.3110")
    dialog._longitude.setText("120.6210")
    dialog._coordinates_to_names_button.click()

    assert dialog._city.text() == "苏州"
    assert dialog._region.text() == "江苏省"
    assert dialog._country.text() == "中国"


def test_a_layer_the_database_does_not_have_is_cleared_so_the_form_tells_the_truth(
    qt_app: QApplication,
) -> None:
    """**库里没有的那一层要显示成空** —— 这个对话框显示的就是将被写入的那一份（ADR 0039 决策 2）。

    城市库里北京、上海、天津、重庆、香港、澳门这 151 条的 ``region`` 是空的。照片原本写着
    「杭州 / 浙江省 / 中国」，坐标改到上海之后，界面那三个 ``if place.region and ...``
    一个都不成立，框里留着「浙江省」；而写盘时那一层被写成空串 —— **删掉**。照片变成
    「上海 / （无省）」，而对话框从头到尾显示着「浙江省」。
    """
    dialog = _dialog(
        None,
        PlaceState(city="杭州", region="浙江省", country="中国"),
        resolver=NoRegionResolver(),
    )

    dialog._latitude.setText("31.2304")
    dialog._longitude.setText("121.4737")
    dialog._coordinates_to_names_button.click()

    assert dialog._city.text() == "上海"
    assert dialog._region.text() == "", "库里没有省，框里就必须显示成空 —— 否则它在骗人"
    assert dialog._country.text() == "中国"


def test_the_button_overwrites_a_layer_the_user_typed_by_hand(
    qt_app: QApplication,
) -> None:
    """**按钮以城市库为准，手敲的也覆盖**（ADR 0039 决策 3）。

    保留一半才是更坏的答案：那半栏显示旧值、保存时照样被覆盖，界面上就有一半是假的。
    """
    dialog = _dialog(
        None,
        PlaceState(city="杭州", region="浙江省", country="中国"),
        resolver=NoRegionResolver(),
    )

    dialog._latitude.setText("31.2304")
    dialog._longitude.setText("121.4737")
    dialog._region.setText("我自己填的")
    dialog._coordinates_to_names_button.click()

    assert dialog._region.text() == ""


def test_the_two_directions_do_not_talk_to_each_other(qt_app: QApplication) -> None:
    """**程序自己填进去的框不再触发下一次回填** —— 否则地名↔坐标会自己打起来。"""
    dialog = _dialog(
        None, PlaceState(city="苏州", latitude=31.311, longitude=120.621), resolver=StubResolver()
    )

    dialog._refresh()
    dialog._refresh()

    assert dialog._city.text() == "苏州"
    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)


def test_a_name_the_database_does_not_know_leaves_everything_alone(qt_app: QApplication) -> None:
    """查不到就一个字都不填 —— 绝不编一个地名或一个坐标出来。"""
    dialog = _dialog(
        None, PlaceState(city="南京", latitude=32.06, longitude=118.79), resolver=StubResolver()
    )

    dialog._city.setText("西湖·断桥")
    dialog._refresh()

    assert dialog._region.text() == ""
    assert dialog._country.text() == ""
    assert float(dialog._latitude.text()) == pytest.approx(32.06)


def test_changing_the_city_is_not_blocked_by_the_stale_province(qt_app: QApplication) -> None:
    """照片原本写着「日本 · 京都府 · 京都」，用户敲一个「苏州」进去 ——
    **按上面那两栏的旧范围去查，当然找不到**。

    那就只按名字再查一遍：查到的结果本来就会把上面两栏改掉，所以「范围」这会儿只是个
    过期的前提，不该把它当成筛选条件把人挡在门外。
    """
    dialog = _dialog(
        None,
        PlaceState(city="京都", region="京都府", country="日本"),
        resolver=_StaleAreaResolver(),
    )

    dialog._city.setText("苏州")
    dialog._names_to_coordinates_button.click()

    assert dialog._country.text() == "中国"
    assert dialog._region.text() == "江苏省"


def test_there_is_no_checkbox_to_turn_the_sync_off(qt_app: QApplication) -> None:
    """**一个勾都没有**：联动默认永远是开的，那就是这个功能存在的意义；
    「清除位置」同样不需要 —— 三层地名与两个坐标框全清空就是那个意思。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert not [
        box
        for box in dialog.findChildren(QCheckBox)
        if "同时" in box.text() or "清除" in box.text()
    ]


def test_the_clear_button_empties_the_whole_place(qt_app: QApplication) -> None:
    """**全清空就是「把位置整个删掉」** —— 不需要单独的勾选框（ADR 0031）。

    这里只清**框**，仍然要按保存才写盘。
    """
    dialog = _dialog(
        None,
        PlaceState(city="苏州", region="江苏省", country="中国", latitude=31.3, longitude=120.6),
    )

    dialog._clear_button.click()

    assert dialog._city.text() == ""
    assert dialog._region.text() == ""
    assert dialog._country.text() == ""
    assert dialog._latitude.text() == ""
    assert dialog._longitude.text() == ""
    result = dialog.edit()
    assert result.place.city is None and result.place.country is None
    assert result.place.clear_coordinates


def test_three_buttons_on_each_of_the_first_two_rows_and_three_actions_below(
    qt_app: QApplication,
) -> None:
    """**上面一行两个方向，下面 3×3 九个键**，每一行都占满，共用同一套列网格。

    ```
    地点→经纬度  经纬度→地点
    复制        复制时间      复制位置
    粘贴        粘贴时间      粘贴位置
    清空位置    取消          保存
    ```

    有了两段可搬的内容，「复制 / 粘贴」就分出了「全搬」与「搬一段」；最后一行是三个动作。
    第一行那两个是 ADR 0039 加的：跨层填充只发生在按下的那一刻，所以它们摆得比搬与清的
    九宫格更靠上一点 —— 它们改的是上面的框。
    **宽度与位置都是布局算出来的**，不是三排手工对齐的推算。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))
    dialog.show()
    QApplication.processEvents()

    cancel = dialog.findChild(QPushButton, "cancelButton")
    save = dialog.findChild(QPushButton, "saveButton")
    assert cancel is not None and save is not None
    directions = (dialog._names_to_coordinates_button, dialog._coordinates_to_names_button)
    rows = (
        (dialog._copy_button, dialog._copy_time_button, dialog._copy_place_button),
        (dialog._paste_button, dialog._paste_time_button, dialog._paste_place_button),
        (dialog._clear_button, cancel, save),
    )

    assert [button.text() for button in directions] == ["地点 → 经纬度", "经纬度 → 地点"]
    assert [[button.text() for button in row] for row in rows[:2]] == [
        ["复制", "复制时间", "复制位置"],
        ["粘贴", "粘贴时间", "粘贴位置"],
    ]
    assert [button.text() for button in rows[2]] == ["清空位置", "取消", "保存"]
    # 方向那一行确实在九宫格**上面**，不是被塞进了它里面某一格的位置。
    assert directions[0].y() < rows[0][0].y()
    # **三列等宽**：九个子控件的 x 只有三个值。
    assert len({button.x() for row in rows for button in row}) == 3
    # 宽度允许差一个像素 —— 整除有余数时 Qt 会把它摊到某一列，那是像素取整，不是没对齐。
    widths = [button.width() for row in rows for button in row]
    assert max(widths) - min(widths) <= 1
    assert [row[0].y() for row in rows] == sorted(row[0].y() for row in rows)
    margins = dialog.layout().contentsMargins()
    # 占满整行：最右边那一列的右边缘就是对话框右边减去下边距。
    assert rows[0][2].x() + rows[0][2].width() == dialog.width() - margins.right()


def test_the_transfer_buttons_have_no_tooltip(qt_app: QApplication) -> None:
    """**不挂悬浮提示。** 六个键的字面意思就是它们要做的事。"""
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert [
        button.toolTip()
        for button in (
            dialog._copy_button,
            dialog._copy_time_button,
            dialog._copy_place_button,
            dialog._paste_button,
            dialog._paste_time_button,
            dialog._paste_place_button,
            dialog._clear_button,
        )
    ] == [""] * 7


def test_no_input_box_has_a_placeholder(qt_app: QApplication) -> None:
    """**空框里不浮字。** 左边那一列标签已经写着它们是什么，再说一遍只是说两遍。"""
    dialog = _dialog(None, PlaceState())

    assert [
        field.placeholderText()
        for field in (
            dialog._country,
            dialog._region,
            dialog._city,
            dialog._latitude,
            dialog._longitude,
        )
    ] == ["", "", "", "", ""]


def test_the_buttons_sit_at_the_bottom_with_no_dangling_space_under_them(
    qt_app: QApplication,
) -> None:
    """**「保存」下面只留布局自己的下边距。**

    报错那一行本来压在按钮下面，而它平时是空的 —— 那块空着的两行垫在最底下，看起来像一大
    截多余的边。挪到按钮**上面**之后，报错说的是上面那几个框里哪一个不对，也才找得回去。
    """
    dialog = _dialog(None, PlaceState(city="苏州", region="江苏省", country="中国"))
    dialog.show()
    QApplication.processEvents()

    save = dialog.findChild(QPushButton, "saveButton")
    assert save is not None
    assert dialog._error.y() < dialog._copy_button.y()  # 报错在按钮上面
    assert save.y() + save.height() == dialog.height() - dialog.layout().contentsMargins().bottom()


def test_the_error_line_keeps_its_room_even_while_it_is_empty(qt_app: QApplication) -> None:
    """**空着也占着那两行** —— 报错时对话框不跟着长高一截，位置一动就晃眼。"""
    dialog = _dialog(None, PlaceState(city="苏州"))
    dialog.show()
    QApplication.processEvents()

    before = dialog.height()
    dialog._error.setText("纬度不是有效数字「30.abc」。请填十进制度，例如 30.27415。")
    QApplication.processEvents()

    assert dialog.height() == before
    assert dialog._error.height() >= 2 * dialog.fontMetrics().height()


def test_the_capture_time_says_nothing_under_it(qt_app: QApplication) -> None:
    """拍摄时间下面**没有说明行**。

    左边那一列已经写着「拍摄时间」「时区」，用户改自己的照片不需要先被教育一遍。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert not dialog.findChildren(QLabel, "metadataEditHint")


def test_the_time_boxes_sit_above_the_place_boxes(qt_app: QApplication) -> None:
    """拍摄时间与时区**在最上面两行**，位置那五栏在它们下面。

    顺序与元数据面板里的分组顺序一致 —— 两处反过来会让人在两个界面里找同一栏。
    """
    dialog = _dialog(None, PlaceState(city="苏州"), capture=CaptureTime("2024:05:16 20:52:02"))
    dialog.show()
    QApplication.processEvents()

    order = (dialog._taken_at, dialog._offset, dialog._country, dialog._region, dialog._city)
    ys = [field.y() for field in order]

    assert ys == sorted(ys)
    assert len(set(ys)) == len(ys)  # 五个框各占一行，没有并排


def test_the_field_outline_has_no_darker_dots(qt_app: QApplication) -> None:
    """输入框那一圈轮廓上**那八个等分点不比别处更深**。

    与改名输入框、搜索胶囊、事件面板同源（量法在 :mod:`tests.ui.outline_helpers`）：Qt 把
    样式表那一圈描成八段，接缝处的半透明边框被调两遍就叠成更深的点
    （理由见 :mod:`gallery.ui.outline`）。
    """
    app_theme = theme.current_theme()
    fill = opaque_over(app_theme.text_at(0.08), app_theme.background)
    dialog = _dialog(None, PlaceState(city="苏州"))
    dialog.show()
    qt_app.processEvents()
    field = dialog._city
    # 焦点那一档还带一个光标，量的不是常态那一圈。
    field.clearFocus()
    dialog.setFocus()
    qt_app.processEvents()
    # 底色必须先是不透明的：半透明底色在离屏抓取里抓到一片透明，下面那条断言就成了空转
    # （抓出来全是 0，看着像「一圈都很深」）。做法见 :func:`dialog_style`。
    inside = field.grab().toImage().pixelColor(field.width() - 20, field.height() // 2)
    assert inside.alpha() == 255, "输入框底色不透明，这条守护才量得到东西"

    assert_no_darker_dots(field, fill.lightness(), radius=FIELD_RADIUS, label="元数据输入框")


def test_copy_then_paste_moves_the_place_to_another_photo(qt_app: QApplication) -> None:
    """**复制 / 粘贴**：一个地点从一张照片搬到另一张，不用一个数字一个数字地抄。"""
    source = _dialog(
        None,
        PlaceState(
            city="苏州", region="江苏省", country="中国", latitude=31.3111, longitude=120.6213
        ),
    )
    target = _dialog(None, PlaceState(), resolver=StubResolver())

    source._on_copy()
    assert QGuiApplication.clipboard().text() == "31.311100, 120.621300\n苏州, 江苏省, 中国"
    assert source._copy_button.text() == "已复制"

    target._on_paste()

    assert target._country.text() == "中国"
    assert target._region.text() == "江苏省"
    assert target._city.text() == "苏州"
    assert float(target._latitude.text()) == pytest.approx(31.3111, abs=1e-4)
    assert float(target._longitude.text()) == pytest.approx(120.6213, abs=1e-4)


def test_copy_then_paste_moves_the_capture_time_too(qt_app: QApplication) -> None:
    """**时间也搬得动** —— 这是编辑器扩到元数据之后新增的那一段（ADR 0032）。"""
    source = _dialog(
        None,
        PlaceState(city="苏州", latitude=31.3111, longitude=120.6213),
        capture=CaptureTime("2024:05:16 20:52:02", "+09:00"),
    )
    target = _dialog(None, PlaceState())

    source._on_copy()
    assert QGuiApplication.clipboard().text().splitlines()[0] == "2024-05-16 20:52:02 +09:00"

    target._on_paste()

    assert target._taken_at.text() == "2024-05-16 20:52:02"
    assert target._offset.text() == "+09:00"
    # 位置那一段也一起搬过来了 —— 「复制」搬的是全部。
    assert target._city.text() == "苏州"


def test_copying_only_the_time_leaves_the_place_out(qt_app: QApplication) -> None:
    """**「复制时间」只写时间那一行** —— 位置是空的就不该往剪贴板里塞空行。"""
    dialog = _dialog(
        None,
        PlaceState(city="苏州", region="江苏省", country="中国"),
        capture=CaptureTime("2024:05:16 20:52:02", "+09:00"),
    )

    dialog._on_copy_time()

    assert QGuiApplication.clipboard().text() == "2024-05-16 20:52:02 +09:00"


def test_pasting_only_the_time_leaves_the_place_alone(qt_app: QApplication) -> None:
    """**「粘贴时间」只动上面那两栏** —— 位置是用户自己填好的，不该被覆盖。"""
    dialog = _dialog(None, PlaceState(city="苏州", region="江苏省", country="中国"))
    QGuiApplication.clipboard().setText("2024-05-16 20:52:02 +09:00")

    dialog._on_paste_time()

    assert dialog._taken_at.text() == "2024-05-16 20:52:02"
    assert dialog._offset.text() == "+09:00"
    assert dialog._city.text() == "苏州"


def test_pasting_the_time_only_when_it_is_not_there_says_so(qt_app: QApplication) -> None:
    """剪贴板里只有位置时按「粘贴时间」—— **说清没有，不要顺手把位置也填了**。"""
    dialog = _dialog(None, PlaceState())
    QGuiApplication.clipboard().setText("31.311100, 120.621300\n苏州, 江苏省, 中国")

    dialog._on_paste_time()

    assert "没有可用的拍摄时间" in dialog._error.text()
    assert dialog._taken_at.text() == ""
    assert dialog._city.text() == ""


@pytest.mark.parametrize(
    "text",
    [
        "31.311100, 120.621300\n苏州, 江苏省, 中国",  # 我们自己那个格式
        "120.621300 31.311100",  # 经度在前（用范围判出来）
        "31.3111N 120.6213E",  # 半球标记
        "31.3111, 120.6213",
    ],
)
def test_paste_reads_the_common_ways_of_writing_a_coordinate(
    qt_app: QApplication, text: str
) -> None:
    dialog = _dialog(None, PlaceState())

    QGuiApplication.clipboard().setText(text)
    dialog._on_paste()

    assert float(dialog._latitude.text()) == pytest.approx(31.3111, abs=1e-4)
    assert float(dialog._longitude.text()) == pytest.approx(120.6213, abs=1e-4)


def test_pasting_nonsense_says_so_instead_of_guessing(qt_app: QApplication) -> None:
    """**猜错的坐标会被原样写进照片** —— 所以认不出来就说认不出来。"""
    dialog = _dialog(None, PlaceState())

    QGuiApplication.clipboard().setText("明天开会记得带伞")
    dialog._on_paste()

    # 「粘贴」搬的是时间与位置两段，一段都认不出时说「没有可以用的内容」
    assert "没有可用的内容" in dialog._error.text()
    assert dialog._latitude.text() == ""
    assert dialog._taken_at.text() == ""


def test_pasting_a_place_name_alone_fills_in_nothing_but_the_names(
    qt_app: QApplication,
) -> None:
    """**粘到地名不再顺手正查出坐标**（ADR 0039 决策 1）—— 要坐标就按那个按钮。

    粘进来的坐标本该是用户自己那份；被程序顺手改一遍，就成了「框里写的不是他要的」。
    """
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    QGuiApplication.clipboard().setText("苏州, 江苏省, 中国")
    dialog._on_paste()
    dialog._refresh()

    assert dialog._city.text() == "苏州"
    assert dialog._latitude.text() == "", "坐标不该被自动正查改掉"
    dialog._names_to_coordinates_button.click()
    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)


def test_the_debounced_lookup_really_is_wired_to_typing(qt_app: QApplication) -> None:
    """改字之后定时器确实会响 —— 上面那几条直接调 ``_refresh``，这里补上「真的连着」。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())
    dialog._timer.setInterval(1)

    dialog._city.setText("苏州")

    wait_until(qt_app, lambda: dialog._country.text() == "中国")


def test_cmd_w_closes_the_dialog_without_touching_the_window(
    qt_app: QApplication,
) -> None:
    """⌘W 关的是**这个对话框**，不是主窗口（ADR 0037）。

    模态框的快捷键优先于主窗口，不挂这一条的话那一下会把整个窗口连同正在编辑的那一摞
    一起带走。**键是显式写出来的**：Qt 在 macOS 上把 ``StandardKey.Close`` 解析成 **⌘F4**，
    而 macOS 上按的键是 ⌘W。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    close_actions = [
        action for action in dialog.actions() if action.shortcut() == QKeySequence("Ctrl+W")
    ]

    assert len(close_actions) == 1, "框里得有一条真的按得到的 ⌘W"
    close_actions[0].trigger()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_cmd_w_in_the_dialog_closes_the_dialog_and_leaves_the_window_open(
    qt_app: QApplication, focused: MainWindow
) -> None:
    """框开着的时候按 ⌘W：关掉的是**框**，窗口还开着。

    这里发的是**真按键**（走 Qt 的快捷键表）而不是直接调槽。**窗口为什么不会跟着没**，
    两件事各管一半：**模态**挡着，而**框自己那一条**让 ⌘W 在框里有事可做 —— 少了后一半，
    按键在框里落下来什么也不会发生。
    """
    window = focused
    dialog = MetadataEditDialog(window, MetadataState())
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    qt_app.processEvents()
    assert window.isVisible() is True

    QTest.keyClick(dialog, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    qt_app.processEvents()

    assert window.isVisible() is True, "⌘W 顺手把主窗口也带走了"
    assert dialog.isVisible() is False, "⌘W 应当关掉这个框"


def test_the_dialog_cannot_be_resized(qt_app: QApplication) -> None:
    """拖一下窗口边不该把提示框拉成一整屏。"""
    dialog = _dialog(None, PlaceState(city="苏州", region="江苏省", country="中国"))
    dialog.show()
    QApplication.processEvents()

    assert dialog.layout().sizeConstraint() == QLayout.SizeConstraint.SetFixedSize
    assert dialog.minimumSize() == dialog.maximumSize()


def test_a_photo_without_any_place_still_has_the_entry_point(
    qt_app: QApplication, focused: MainWindow
) -> None:
    """**照片本来就没有位置时，按钮照样在那儿。**

    之前它摆在「位置」那一组的标题右边，而没有位置的照片压根不会渲染出那一组。
    """
    window = focused
    window.toggle_metadata(True)
    wait_until(qt_app, lambda: window._panel.isVisible())

    panel = window._panel
    panel.set_report(
        build_report(
            {"EXIF:Model": "Canon EOS R6"},
            path=Path("/库/一张.jpg"),
            size=1,
            mtime_ns=0,
            source="exiftool",
        )
    )
    qt_app.processEvents()

    assert panel.metadata_editable
    assert panel._edit_button.isEnabled()
    assert not panel._footer.isHidden()
    assert "位置" not in panel.report.as_text()  # 那一组确实不存在


def test_the_three_footer_widgets_fill_the_row(qt_app: QApplication) -> None:
    """复制全部 / 编辑元数据 / 显示全部字段**平分整行**，右边不空出一小条。

    **扣掉的是两层边距**：面板自己那一层与页脚自己那一层 —— 页脚的右边距补的是滚动条挪到
    窗口边上之后少掉的那一档内容留白（见 ``viewer.metadata._CONTENT_PAD_RIGHT``），
    只扣面板那一层的话，扣掉的数与实际可用的宽度对不上。
    """
    panel = _panel(qt_app)
    row = panel._footer.layout()

    widgets = [row.itemAt(index).widget() for index in range(row.count())]

    assert [widget.text() for widget in widgets] == ["复制全部", "编辑元数据", "显示全部字段"]
    used = sum(widget.width() for widget in widgets)
    used += row.spacing() * (len(widgets) - 1)
    margins = panel.layout().contentsMargins()  # 面板自己那一层
    insets = row.contentsMargins()  # 页脚自己那一层
    assert used == panel.width() - (
        margins.left() + margins.right() + insets.left() + insets.right()
    )
    # 两层右边距加起来仍是那一档内容留白 —— 滚动条挪到窗口边上动的是**位置**，不是留白。
    assert margins.right() + insets.right() == CONTENT_PAD, (
        f"页脚离面板右缘 {margins.right() + insets.right()} 点，要的是 {CONTENT_PAD} 点"
    )
    assert len({widgets[0].width(), widgets[1].width()}) == 1  # 两个按钮一样宽


def test_the_button_goes_grey_and_explains_when_it_cannot_be_used(qt_app: QApplication) -> None:
    """**摆着一个点下去只会报错的按钮，比没有更糟**（ADR 0014 决策 4）。

    不用时置灰并把理由放进悬浮提示，而「位置」那一组仍然把这句话写出来 ——
    看得见，不必去猜。
    """
    panel = _panel(qt_app)

    panel.set_metadata_editable(False, reason="没有装 exiftool")

    assert not panel._edit_button.isEnabled()
    assert panel._edit_button.toolTip() == "没有装 exiftool"


def _panel(qt_app: QApplication) -> MetadataPanel:
    """一个**页脚可见**的面板。

    页脚在读到元数据之前是藏着的，而藏着的控件不参与布局 —— 拿它量宽度量出来的是
    Qt 的默认值（640），那正是空转绿的一种。
    """
    panel = MetadataPanel()
    panel.show()
    panel.set_report(
        build_report(
            {"EXIF:Model": "Canon EOS R6"},
            path=Path("/库/一张.jpg"),
            size=1,
            mtime_ns=0,
            source="exiftool",
        )
    )
    qt_app.processEvents()
    assert not panel._footer.isHidden()
    return panel
