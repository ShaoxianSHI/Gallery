"""「编辑位置」的界面测试（ADR 0030 / 0031）。

三块：

1. **菜单摆不摆得对** —— 单选照片有、多选没有、分组卡片没有；没装 exiftool 时更没有
   （摆一条点下去只会报错的，比没有更糟）。
2. **对话框自己** —— 预填照片此刻的值、把用户填的读成 ``PlaceEdit``、坐标不合法时**不关
   自己**并说清是哪一处。
3. **写完之后** —— 横幅把「已经发生的事」说出来、网格里那一行换了新引用、⌘Z 撤得回去。

**不碰真 exiftool**：窗口建好之后把库里的 writer 换成一份内存里的假表（与
``tests/core/metadata/test_writeback.py`` 同一形状）。于是这条链在没装 exiftool 的机器上
也照样跑，而真去起 exiftool 的那一半在那份测试里。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.errors import MetadataWriteError  # noqa: E402
from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.core.metadata.journal import read_records  # noqa: E402
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState  # noqa: E402
from gallery.core.metadata.writeback import Assignment  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels.place_edit import PlaceEditDialog  # noqa: E402

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
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_ALBUMS), base=session_base
    )
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance._library._writer = writer  # 不起真的 exiftool（测试不得依赖本机装没装）
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描与地点解析")
    yield instance
    instance.close()


def _enter_album(qt_app: QApplication, window: MainWindow, root: Path) -> int:
    """进那个相册并等这一层排好，返回照片那一行的行号。

    列一层是**后台**跑的：转一圈事件循环不等于扫完了（``test_album_cover_flow`` 里
    写着同一段理由），所以必须等 ``row_of_path`` 真的能给出行号。
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
    """停在大图页、正在看那一张 —— 「编辑位置」作用的对象（ADR 0021）。"""
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


# ---- 入口摆不摆 ----------------------------------------------------------------


def test_the_photo_menu_has_it_in_the_write_group(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """它落在**「会动文件」**那一段，与重命名、移到废纸篓同族：改的是照片里的字节。"""
    row = _enter_album(qt_app, window, root)
    texts = _context_at(window, row)

    assert "编辑位置…" in texts
    assert texts.index("编辑位置…") > texts.index("重命名…")
    assert texts.index("编辑位置…") < texts.index("移到废纸篓")


def test_a_group_card_has_no_place_editor(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """分组卡片不是磁盘上的东西 —— 「编辑它的位置」说不清指的是哪一张（ADR 0020）。"""
    _enter_album(qt_app, window, root)
    member = _photo_ref(window, root)
    window._model.reorder(
        [GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(member,))]  # type: ignore[arg-type]
    )

    assert "编辑位置…" not in _context_at(window, 0)


def test_it_disappears_when_exiftool_is_missing(
    qt_app: QApplication, window: MainWindow, root: Path, writer: FakeWriter
) -> None:
    """没装 exiftool 时**不出现**，而不是摆一条点了只会报错的（ADR 0014 决策 4）。"""
    writer.available_flag = False
    row = _enter_album(qt_app, window, root)
    qt_app.processEvents()

    assert "编辑位置…" not in _context_at(window, row)


# ---- 对话框 --------------------------------------------------------------------


def _dialog(window: MainWindow, state: PlaceState, **kwargs: object) -> PlaceEditDialog:
    dialog = PlaceEditDialog(None, state, **kwargs)  # type: ignore[arg-type]
    return dialog


def test_the_dialog_prefills_what_the_photo_says(qt_app: QApplication) -> None:
    dialog = _dialog(
        None,  # type: ignore[arg-type]
        PlaceState(
            city="南京", region="江苏省", country="中国", latitude=32.0603, longitude=118.7969
        ),
    )

    assert dialog._city.currentText() == "南京"
    assert dialog._region.text() == "江苏省"
    assert dialog._country.text() == "中国"
    assert dialog._latitude.text().startswith("32.0603")
    assert dialog._longitude.text().startswith("118.7969")
    assert dialog._sync.isChecked()


def test_the_dialog_returns_the_final_state_not_a_patch(qt_app: QApplication) -> None:
    """``PlaceEdit`` 收的是**最终状态**：空框表示「清空那一层」，不是「别动它」。

    这正是对话框打开时要把三层地名与坐标全填上的原因（见 ``PlaceEdit`` 的文档）。
    """
    dialog = _dialog(None, PlaceState(city="南京", latitude=32.06, longitude=118.79))  # type: ignore[arg-type]

    result = dialog.edit()

    assert result == PlaceEdit(city="南京", latitude=32.06, longitude=118.79)


def test_emptying_both_coordinate_boxes_means_remove_them(qt_app: QApplication) -> None:
    """定位漂了、地名没错 —— 清空两个坐标框要能被表达成「删坐标」。"""
    dialog = _dialog(None, PlaceState(city="南京", latitude=32.06, longitude=118.79))  # type: ignore[arg-type]
    dialog._latitude.clear()
    dialog._longitude.clear()

    assert dialog._coordinates_emptied()
    assert dialog.edit().clear_coordinates


def test_an_impossible_coordinate_keeps_the_dialog_open(qt_app: QApplication) -> None:
    """**不弹窗、不关自己**：那行红字就够，关掉的话用户填的别的内容全丢了。"""
    dialog = _dialog(None, PlaceState(city="南京"))  # type: ignore[arg-type]
    dialog._latitude.setText("91")
    dialog._longitude.setText("120")

    dialog._on_accept()

    assert dialog.result() == 0  # 还没 Accepted
    assert "纬度超范围了" in dialog._error.text()


def test_half_a_coordinate_is_refused_too(qt_app: QApplication) -> None:
    dialog = _dialog(None, PlaceState(city="南京"))  # type: ignore[arg-type]
    dialog._latitude.setText("32.06")

    dialog._on_accept()

    assert dialog.result() == 0
    assert "经度和纬度要一起填" in dialog._error.text()


def test_the_hint_tells_the_truth_before_saving(qt_app: QApplication) -> None:
    """**保存之前**就把后果说出来 —— 尤其是「坐标会变成市级精度」那一条。"""
    dialog = _dialog(None, PlaceState(city="南京", latitude=32.06, longitude=118.79))  # type: ignore[arg-type]

    assert "重新推断" not in dialog._hint.text()
    dialog._region.setText("浙江省")
    assert "市级精度" in dialog._hint.text()


# ---- 写回与撤销 ----------------------------------------------------------------


def _write(window: MainWindow, ref: object, state: PlaceState, edit: PlaceEdit) -> None:
    """替掉对话框，直接把一份 ``PlaceEdit`` 交给写回那条路。"""
    assert window._library is not None
    window._apply_place_edit(ref, state, edit)  # type: ignore[arg-type]


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
    assert focused._undo_action.text() == "撤销 编辑位置"
    # 横幅把**已经发生的事**说出来：地名与坐标各自被谁改成了什么。
    assert "已更新" in focused._banner.text()
    assert "坐标已按" in focused._banner.text()
    assert "不是照片的具体位置" in focused._banner.text()


def test_the_journal_has_the_field_level_record(
    focused: MainWindow, root: Path, writer: FakeWriter
) -> None:
    # 照对话框那样把三层都填上 —— ``PlaceEdit`` 是最终状态，空的那一层意思是「清掉它」。
    _write(
        focused,
        _photo_ref(focused, root),
        PlaceState(),
        PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    records = read_records(root)
    assert [record.action for record in records] == ["metadata"]
    # 只填了城市，坐标是城市库里查出来的那个 —— 于是两组字段都在记录里，各带原值。
    # 「删掉海拔与定位时间」那三条**不在里面**：这张照片本来就没有它们，写入它们是空操作，
    # 空操作不该进日志（否则 ⌘Z 会去「撤销」一件根本没发生过的事）。
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

    # 照对话框那样把三层都填上 —— ``PlaceEdit`` 是最终状态，空的那一层意思是「清掉它」。
    _write(
        focused,
        _photo_ref(focused, root),
        PlaceState(),
        PlaceEdit(city="杭州", region="浙江省", country="中国"),
    )

    records = read_records(root)
    assert len(records) == before + 1
    assert records[-1].result == "failed"
    assert "只读" in records[-1].message
    assert "没有权限写入" in focused._banner.text()


def test_the_panel_offers_the_editor(qt_app: QApplication, focused: MainWindow) -> None:
    """面板「位置」那一组的按钮是同一个入口（点它与 ⇧⌘I 走的是同一条路）。"""
    window = focused
    window.toggle_metadata(True)
    wait_until(qt_app, lambda: window._panel.isVisible())

    assert window._panel.place_editable
