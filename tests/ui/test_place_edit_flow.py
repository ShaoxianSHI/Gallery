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
from PySide6.QtGui import QGuiApplication, QKeySequence  # noqa: E402
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
from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.core.metadata.exif import build_report  # noqa: E402
from gallery.core.metadata.journal import read_records  # noqa: E402
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState  # noqa: E402
from gallery.core.metadata.writeback import Assignment  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels.metadata import MetadataPanel  # noqa: E402
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

    assert dialog._country.text() == "中国"
    assert dialog._region.text() == "江苏省"
    assert dialog._city.text() == "南京"
    assert dialog._latitude.text().startswith("32.0603")
    assert dialog._longitude.text().startswith("118.7969")


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
    # 横幅只有一句：「接下来会发生什么」是对话框里那行小字的事，保存之前就说过了。
    assert focused._banner.text() == "已更新「一张.jpg」的位置。⌘Z 可以撤销。"


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
    """页脚那个按钮是同一个入口（点它与 ⇧⌘I 走的是同一条路）。

    **它摆在页脚，不摆在「位置」那一组里**：照片**本来就没有位置**的时候面板上根本没有
    「位置」那一组，入口也就跟着没了 —— 而那恰恰是最需要补一个位置的时候。
    """
    window = focused
    window.toggle_metadata(True)
    wait_until(qt_app, lambda: window._panel.isVisible())

    panel = window._panel
    assert panel.place_editable
    assert panel._place_button.parentWidget() is panel._footer  # 摆在页脚那一行
    assert panel._place_button.isEnabled()
    # 页脚在读到元数据之前是藏着的（那时照片还没选中，几句话说了也没用）。
    wait_until(qt_app, lambda: not panel._footer.isHidden())


# ---- 字段顺序、实时回填、⌘W 与不可缩放 -----------------------------------------


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
    """只有「不按范围查」才找得到 —— 用来钉住上面那条兜底重查。"""

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
    而城市库里没有的名字（用户自己的门牌、景区）恰恰是最该能填进去的那些。补全仍在，
    它只是**建议**。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert isinstance(dialog._city, QLineEdit)
    assert dialog._city.completer() is not None


def test_one_letter_does_not_run_a_look_up(qt_app: QApplication) -> None:
    """城市库里有一个真叫「苏」的条目（安徽某个被折叠进市里的区）。

    用户敲下第一个字就恰好命中它的话，屏幕上会立刻跳出「中国 · 安徽省」和一串坐标 ——
    那是数据里的碎片，不是用户要填的地方。**少于两个字不查、也不给候选。**
    """
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏")
    dialog._refresh()

    assert dialog._country.text() == ""
    assert dialog._latitude.text() == ""


def test_two_letters_are_enough(qt_app: QApplication) -> None:
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._refresh()

    assert dialog._country.text() == "中国"
    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)


def test_the_fields_run_country_province_city(qt_app: QApplication) -> None:
    """**由大到小**：国家 → 省 / 州 → 城市。读地点就是这么读的。"""
    dialog = _dialog(None, PlaceState(city="苏州", region="江苏省", country="中国"))

    rows = _row_positions(dialog)
    assert rows["国家"] < rows["省 / 州"] < rows["城市"] < rows["纬度"] < rows["经度"]


def test_typing_a_city_fills_in_the_province_and_the_country(qt_app: QApplication) -> None:
    """**只敲城市就行**：省与国家自己长出来，而且用的是库里那一带的准确写法。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._refresh()

    assert dialog._region.text() == "江苏省"
    assert dialog._country.text() == "中国"


def test_a_place_updates_the_coordinates_right_away(qt_app: QApplication) -> None:
    """**地点 → 坐标实时更新**：保存之前就能看见会写进去的是哪一对。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._city.setText("苏州")
    dialog._refresh()

    assert float(dialog._latitude.text()) == pytest.approx(31.311, abs=1e-4)
    assert float(dialog._longitude.text()) == pytest.approx(120.621, abs=1e-4)


def test_coordinates_update_the_place_right_away(qt_app: QApplication) -> None:
    """**坐标 → 地点实时更新**：把坐标拖到苏州附近，上面三栏自己说出「中国 · 江苏省 · 苏州」。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    dialog._latitude.setText("31.3110")
    dialog._longitude.setText("120.6210")
    dialog._refresh()

    assert dialog._city.text() == "苏州"
    assert dialog._region.text() == "江苏省"
    assert dialog._country.text() == "中国"


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
    dialog._refresh()

    assert dialog._country.text() == "中国"
    assert dialog._region.text() == "江苏省"


def test_there_is_no_checkbox_to_turn_the_sync_off(qt_app: QApplication) -> None:
    """**一个勾都没有**。

    「同时按这个地点更新经纬度」默认永远是开的 —— 那就是这个功能存在的意义，为它留一个
    勾等于让用户每次都要决定一遍「我要不要它联动」。「清除位置」同样不需要：三层地名与
    两个坐标框全清空就是那个意思。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert not [
        box
        for box in dialog.findChildren(QCheckBox)
        if "同时" in box.text() or "清除" in box.text()
    ]


def test_the_clear_button_empties_every_field(qt_app: QApplication) -> None:
    """**全清空就是「把位置整个删掉」** —— 不需要单独的勾选框（ADR 0031）。

    勾选框得先去勾一下才生效，放在五个按钮里反而更像一个动作；而「全清空」本来就是
    编辑器里唯一表达得出「全删」的形状。这里只清**框**，仍然要按保存才写盘。
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
    assert result.city is None and result.country is None
    assert result.clear_coordinates


def test_three_buttons_on_top_and_two_below_aligned_with_paste_and_clear(
    qt_app: QApplication,
) -> None:
    """复制 / 粘贴 / 清空**占满第一行**；取消 / 保存在第二行，**分别与粘贴和清空对齐**。

    同一套列网格 —— 宽度与位置都是布局算出来的，不是两排手工对齐的推算。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))
    dialog.show()
    QApplication.processEvents()

    top = (dialog._copy_button, dialog._paste_button, dialog._clear_button)
    cancel = dialog.findChild(QPushButton, "cancelButton")
    save = dialog.findChild(QPushButton, "saveButton")

    assert [button.text() for button in top] == ["复制", "粘贴", "清空"]
    assert cancel is not None and cancel.text() == "取消"
    assert save is not None and save.text() == "保存"
    assert len({button.width() for button in top}) == 1  # 三个等宽
    assert cancel.x() == dialog._paste_button.x()  # 取消与粘贴对齐
    assert save.x() == dialog._clear_button.x()  # 保存与清空对齐
    assert cancel.y() > dialog._clear_button.y()  # 在第二行
    margins = dialog.layout().contentsMargins()
    assert top[2].x() + top[2].width() == dialog.width() - margins.right()  # 占满整行


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


def test_the_transfer_buttons_have_no_tooltip(qt_app: QApplication) -> None:
    """**不挂悬浮提示。** 三个键的字面意思就是它们要做的事。"""
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert [
        button.toolTip()
        for button in (dialog._copy_button, dialog._paste_button, dialog._clear_button)
    ] == ["", "", ""]


def test_the_buttons_sit_at_the_bottom_with_no_dangling_space_under_them(
    qt_app: QApplication,
) -> None:
    """**「保存」下面只留布局自己的下边距。**

    报错那一行本来压在按钮下面，而它平时是空的 —— 那块空着的两行垫在最底下，看起来
    像一大截多余的边（对话框高度还因此虚胖了 30px）。挪到按钮**上面**之后：报错说的是
    上面那几个框里哪一个不对，放在那儿也才找得回去。
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
    dialog._error.setText("纬度不是一个数字：「30.abc」。可以直接填十进制度，例如 30.27415。")
    QApplication.processEvents()

    assert dialog.height() == before
    assert dialog._error.height() >= 2 * dialog.fontMetrics().height()


def test_there_is_no_hint_line_left(qt_app: QApplication) -> None:
    """那行「保存后，XXX」已经删掉了 —— 用户不要它。"""
    dialog = _dialog(None, PlaceState(city="苏州"))

    assert not dialog.findChildren(QLabel, "placeEditHint")


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

    assert "没有可以用的位置" in dialog._error.text()
    assert dialog._latitude.text() == ""


def test_pasting_a_place_name_alone_fills_in_the_coordinates(qt_app: QApplication) -> None:
    """只粘到地名也行 —— 正查补出坐标，那是双向联动的用处。"""
    dialog = _dialog(None, PlaceState(), resolver=StubResolver())

    QGuiApplication.clipboard().setText("苏州, 江苏省, 中国")
    dialog._on_paste()
    dialog._refresh()

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
    """⌘W 关的是**这个对话框**。

    不挂这一条的话，⌘W 会一路走到主窗口 —— 在一个模态框里按 ⌘W 结果整个窗口没了，
    那绝不是按键的人想做的事。
    """
    dialog = _dialog(None, PlaceState(city="苏州"))

    close_actions = [
        action
        for action in dialog.actions()
        if action.shortcut() == QKeySequence(QKeySequence.StandardKey.Close)
    ]

    assert len(close_actions) == 1
    close_actions[0].trigger()
    assert dialog.result() == QDialog.DialogCode.Rejected


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

    之前它摆在「位置」那一组的标题右边，而没有位置的照片压根不会渲染出那一组 ——
    用户想补一个位置的时候，界面上根本没有入口。
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

    assert panel.place_editable
    assert panel._place_button.isEnabled()
    assert not panel._footer.isHidden()
    assert "位置" not in panel.report.as_text()  # 那一组确实不存在


def test_the_three_footer_widgets_fill_the_row(qt_app: QApplication) -> None:
    """复制全部 / 编辑位置 / 显示全部字段**平分整行**，右边不空出一小条。"""
    panel = _panel(qt_app)
    row = panel._footer.layout()

    widgets = [row.itemAt(index).widget() for index in range(row.count())]

    assert [widget.text() for widget in widgets] == ["复制全部", "编辑位置", "显示全部字段"]
    used = sum(widget.width() for widget in widgets)
    used += row.spacing() * (len(widgets) - 1)
    margins = panel.layout().contentsMargins()  # 面板自己的左右边距
    assert used == panel.width() - (margins.left() + margins.right())
    assert len({widgets[0].width(), widgets[1].width()}) == 1  # 两个按钮一样宽


def test_the_button_goes_grey_and_explains_when_it_cannot_be_used(qt_app: QApplication) -> None:
    """**摆着一个点下去只会报错的按钮，比没有更糟**（ADR 0014 决策 4）。

    不用时置灰并把理由放进悬浮提示，而「位置」那一组仍然把这句话写出来 ——
    看得见，不必去猜。
    """
    panel = _panel(qt_app)

    panel.set_place_editable(False, reason="没有装 exiftool")

    assert not panel._place_button.isEnabled()
    assert panel._place_button.toolTip() == "没有装 exiftool"


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
