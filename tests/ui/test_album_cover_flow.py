"""「设为相册封面」/「取消相册封面」的界面测试（ADR 0028）。

覆盖用户真正会经历的那条链：在相册里选中一张照片 → 右键「设为相册封面」→
``.gallery/config.toml`` 里多了一行 → 退上一层，那张相册卡片换成他刚选的那张。

还有几件同样重要的事：

- **菜单里摆不摆得对**：单选照片才有「设为相册封面」，多选没有，分组卡片没有，
  空白处没有；「取消相册封面」只在**真的设过**的相册上出现（ADR 0014 决策 4）。
- **相册是照片的父目录**，不问「现在站在哪一层」——「全部照片」那一屏里选的也是
  它自己那个相册。
- **大图页上同样能设**（ADR 0021：作用的是正在看的那一张）。
- **照片一个字节都不动**，而且**不进撤销栈**：这一条写的是 Gallery 自己的配置文件。
- 照片挪走 / 改名之后退回「第一张照片」，而那一行配置留着。

全部在 ``tmp_path`` 里进行，照片都是当场造的临时文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import wait_for_idle, wait_for_level, wait_until  # noqa: E402

from gallery.core import paths  # noqa: E402
from gallery.core.errors import ConfigError  # noqa: E402
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    load_config,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata.journal import read_records  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

ALBUM = "京都"
FIRST = "一张.png"
SECOND = "另一张.png"

#: 两张照片各有各的颜色：封面换成哪一张，靠像素就能看出来，不靠「缩略图不是 None」。
#: **用 PNG 而不是 JPEG**：有损解码会把颜色推偏，这条断言就只好写成「差不多」。
FIRST_COLOR = (200, 120, 40)
SECOND_COLOR = (20, 60, 200)

#: 认「是这一张」时允许偏多少个色阶（理由见 :func:`_shows`）。
_COLOR_TOLERANCE = 24


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个库：``京都`` 里两张颜色不同的照片，配置目录已建好。"""
    library = tmp_path / "照片"
    (library / ALBUM).mkdir(parents=True)
    # SECOND 后写 = 更新，而默认方向是「新 → 旧」（ADR 0049）—— 于是网格第一行是 SECOND。
    # 下面那些用例都拿 FIRST 当「翻过去之后到的那一张」，靠的正是这个先后。
    Image.new("RGB", (16, 12), FIRST_COLOR).save(library / ALBUM / FIRST)
    Image.new("RGB", (16, 12), SECOND_COLOR).save(library / ALBUM / SECOND)
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
    wait_for_idle(qt_app, instance, what="扫描、分组与地点解析")
    yield instance
    instance.close()


# ---- 一点点小工具 ------------------------------------------------------------


def _texts(menu: QMenu | None) -> list[str]:
    assert menu is not None
    return [action.text() for action in menu.actions()]


def _select(window: MainWindow, path: Path) -> int:
    """选中某一格（按代码选，与用户左键点一下等价），返回它的行号。"""
    row = window._model.row_of_path(path)
    assert row >= 0, f"{path} 不在当前这一层上"
    window._grid.select_row(row)
    return row


def _enter_album(qt_app: QApplication, window: MainWindow, root: Path) -> None:
    window.enter_folder(root / ALBUM)
    wait_for_idle(qt_app, window, what="列相册里那一层")


def _cover_color(window: MainWindow, row: int) -> tuple[int, int, int]:
    """那一格画出来的那张缩略图的主色。

    读的是**模型给委托的那一份**，不是磁盘上的缓存：用户看到的就是它。
    """
    pixmap = window._model.data(window._model.index(row, 0), Qt.ItemDataRole.DecorationRole)
    assert isinstance(pixmap, QPixmap), "这一格还没有缩略图"
    return tuple(pixmap.toImage().pixelColor(0, 0).getRgb()[:3])  # type: ignore[return-value]


def _shows(window: MainWindow, row: int, color: tuple[int, int, int]) -> bool:
    """那一格画的是不是这张底色的照片。

    **按色阶比，不按整数比**：缩略图缓存里存的是 JPEG（``core.media.thumbs``，
    质量 82 / 4:2:0），解码回来会偏几个色阶 —— 写成 ``==`` 的话这条断言钉的是
    JPEG 的参数而不是封面。两张测试照片差着上百个色阶，±24 分得开，也不会碰边界。
    """
    got = _cover_color(window, row)
    return all(abs(one - want) <= _COLOR_TOLERANCE for one, want in zip(got, color, strict=True))


def _wait_for_cover(qt_app: QApplication, window: MainWindow, path: Path) -> int:
    """等那一格真的画出缩略图来（解码在后台线程上），返回它的行号。"""
    wait_for_level(
        window,
        qt_app,
        lambda: window._model.has_thumbnail(path),
        what=f"「{path.name}」的缩略图",
    )
    return window._model.row_of_path(path)


# ---- 菜单上摆不摆 -----------------------------------------------------------


def test_the_photo_menu_offers_it(qt_app: QApplication, window: MainWindow, root: Path) -> None:
    """单选一张照片：这一条出现在「看一眼」那一组的末尾（ADR 0023）。"""
    _enter_album(qt_app, window, root)
    row = _select(window, root / ALBUM / FIRST)

    assert window._grid.select_for_context(row) == row
    labels = _texts(window.context_menu_for(row))

    assert "设为相册封面" in labels
    assert "取消相册封面" not in labels
    # 挨着「在访达中显示」，同属第一组；它与「移到废纸篓」隔着一段。
    assert labels.index("设为相册封面") == labels.index("在访达中显示") + 1
    assert labels.index("设为相册封面") < labels.index("重命名…")


def test_multi_selection_does_not_offer_it(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """多选时不摆：把哪一张当封面说不清（与「打开」「重命名…」同一个理由）。"""
    _enter_album(qt_app, window, root)
    first = _select(window, root / ALBUM / FIRST)
    second = window._model.row_of_path(root / ALBUM / SECOND)
    window._grid.selectionModel().select(
        window._model.index(second, 0),
        window._grid.selectionModel().SelectionFlag.Select
        | window._grid.selectionModel().SelectionFlag.Rows,
    )

    assert window._grid.select_for_context(first) == first
    assert "设为相册封面" not in _texts(window.context_menu_for(first))


def test_a_group_card_does_not_offer_it(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """分组卡片上不摆：卡片不是磁盘上的东西，「把这个卡片当封面」没有对象。

    与「重命名…」同一处置 —— 那一条也是因为卡片没有名字可改才不出现的。
    """
    photo = PhotoRef.from_path(root / ALBUM / FIRST)
    window._model.reorder([GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))])
    qt_app.processEvents()
    window._grid.select_row(0)

    assert window._grid.select_for_context(0) == 0
    assert "设为相册封面" not in _texts(window.context_menu_for(0))


def test_a_photo_straight_in_the_library_root_has_no_album(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """库根下的照片没有相册卡片可以当封面 —— 那一条压根不出现（而不是按下去再说）。"""
    Image.new("RGB", (16, 12), SECOND_COLOR).save(root / "散着的.jpg")
    _enter_album(qt_app, window, root)
    window.go_up()
    wait_for_idle(qt_app, window, what="退回顶层")
    row = _select(window, root / "散着的.jpg")

    assert window._grid.select_for_context(row) == row
    assert "设为相册封面" not in _texts(window.context_menu_for(row))


def test_clearing_appears_only_after_a_cover_was_set(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """「取消相册封面」只给**真的设过**的相册：用户看得见自己设过。

    没设过的那个相册上摆一条点得动、点了什么也不变的命令，不如不摆。
    """
    album_row = _select(window, root / ALBUM)
    assert window._grid.select_for_context(album_row) == album_row
    assert "取消相册封面" not in _texts(window.context_menu_for(album_row))

    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)
    window.set_selected_as_album_cover()
    qt_app.processEvents()
    window.go_up()
    wait_for_idle(qt_app, window, what="退回顶层")

    album_row = _select(window, root / ALBUM)
    assert window._grid.select_for_context(album_row) == album_row
    labels = _texts(window.context_menu_for(album_row))
    assert "取消相册封面" in labels
    assert "设为相册封面" not in labels, "相册上说的是取消，不是设置"
    assert labels.index("取消相册封面") == labels.index("在访达中显示") + 1


# ---- 设上去 -----------------------------------------------------------------


def test_it_lands_in_the_config_file(qt_app: QApplication, window: MainWindow, root: Path) -> None:
    """端到端的那一步：``.gallery/config.toml`` 里真的多了一行。

    **写的是相对路径**：库挪个位置、换台机器，那几行照样指着同一张照片。
    """
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)

    window.set_selected_as_album_cover()

    assert load_config(root).album_covers == {ALBUM: f"{ALBUM}/{SECOND}"}
    body = paths.config_file(root).read_text(encoding="utf-8")
    assert f'"{ALBUM}" = "{ALBUM}/{SECOND}"' in body


def test_the_banner_says_which_photo_became_which_albums_cover(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """两样都要说出来：用户是在相册内部按的右键，只说「已设为封面」他得自己回去确认。"""
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)

    window.set_selected_as_album_cover()

    assert window._banner.text() == f"已把「{SECOND}」设为「{ALBUM}」的封面。"


def test_the_album_card_redraws_with_the_new_cover(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """退上一层，那张相册卡片换成刚选的那张 —— 靠像素认，不靠「有没有图」。

    这一段是最容易漏的一半：模型里已经缩放好的旧封面不清掉，
    ``needs_thumbnail`` 就说「这张我有了」，用户看着自己刚设的封面没换上。
    """
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)
    window.set_selected_as_album_cover()
    qt_app.processEvents()

    window.go_up()
    wait_for_idle(qt_app, window, what="退回顶层")
    row = _wait_for_cover(qt_app, window, root / ALBUM)

    assert _shows(window, row, SECOND_COLOR)


def test_setting_a_cover_touches_no_photo_and_leaves_no_journal_record(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """照片一个字节都没动，也没有一条写操作日志。

    写的是 Gallery 自己的配置文件，不是用户的文件；因此它不进 ⌘Z 那一摞栈
    （那一摞装的是「动了用户文件」的那些操作，ADR 0028 决策 5）。
    """
    photos = sorted(path.name for path in (root / ALBUM).iterdir())
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)

    window.set_selected_as_album_cover()

    assert sorted(path.name for path in (root / ALBUM).iterdir()) == photos
    assert read_records(root) == []


def test_the_flat_view_sets_the_album_the_photo_really_lives_in(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """「全部照片」那一屏里也有这一条：相册是照片的**父目录**，不是「当前这一层」。

    这一屏上照片是各个相册混在一起的，问用户「哪个相册」说不通，而答案就在路径里。
    """
    window.set_browse_mode(BROWSE_MODE_FLAT)
    wait_for_idle(qt_app, window, what="切到全部照片")
    row = _select(window, root / ALBUM / FIRST)
    assert window._grid.select_for_context(row) == row
    assert "设为相册封面" in _texts(window.context_menu_for(row))

    window.set_selected_as_album_cover()

    assert load_config(root).album_covers == {ALBUM: f"{ALBUM}/{FIRST}"}


def test_the_viewer_sets_the_cover_of_the_photo_you_are_looking_at(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """大图页上同样能设，且作用的是**正在看的那一张**（ADR 0021）。

    翻过图之后网格的选区早就停在第一张了 —— 设成别的照片的封面，是这一页最容易出的错。
    """
    _enter_album(qt_app, window, root)
    # 从网格的第一行（SECOND）翻到下一张（FIRST）：翻过去之后正在看的**不是**选中的那张。
    row = _select(window, root / ALBUM / SECOND)
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image), "大图没有在时限内显示出来"
    window.step_detail(1)
    qt_app.processEvents()

    assert "设为相册封面" in _texts(window.detail_context_menu())

    window.set_selected_as_album_cover()

    assert load_config(root).album_covers == {ALBUM: f"{ALBUM}/{FIRST}"}


def test_a_cover_from_a_sub_album_reaches_the_parent(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """子相册里的照片可以当**父**相册的封面：设在哪一级由照片自己待在哪决定。"""
    nested = root / ALBUM / "奈良"
    nested.mkdir()
    Image.new("RGB", (16, 12), SECOND_COLOR).save(nested / "鹿.jpg")
    _enter_album(qt_app, window, root)
    window.enter_folder(nested)
    wait_for_idle(qt_app, window, what="列子相册那一层")
    _select(window, nested / "鹿.jpg")

    window.set_selected_as_album_cover()

    assert load_config(root).album_covers == {f"{ALBUM}/奈良": f"{ALBUM}/奈良/鹿.jpg"}


# ---- 改回去 -----------------------------------------------------------------


def test_clearing_puts_the_first_photo_back(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    """取消之后是**第一张照片**当封面，而不是文件夹图标 —— 与从没设过的那天一模一样。

    而且是**当场**换回来：相册卡片就在屏幕上，模型里那份旧记忆必须立刻作废
    （否则 ``needs_thumbnail`` 说「这张我有了」，用户看着自己取消的封面还在）。
    """
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)
    window.set_selected_as_album_cover()
    qt_app.processEvents()
    window.go_up()
    wait_for_idle(qt_app, window, what="退回顶层")
    _select(window, root / ALBUM)
    assert _wait_for_cover(qt_app, window, root / ALBUM) >= 0

    window.clear_album_cover()
    # 「当场」是这条用例要钉的另一半：旧封面那份记忆必须在这次调用里作废，否则下面
    # ``_wait_for_cover`` 会在旧图还在的时候就返回，读到的还是取消之前那一张。
    assert window._model.has_thumbnail(root / ALBUM) is False, "旧封面没有当场作废"
    row = _wait_for_cover(qt_app, window, root / ALBUM)

    assert _shows(window, row, FIRST_COLOR)
    assert load_config(root).album_covers == {}


def test_the_clear_banner_says_what_it_fell_back_to(
    qt_app: QApplication, window: MainWindow, root: Path
) -> None:
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)
    window.set_selected_as_album_cover()
    window.go_up()
    wait_for_idle(qt_app, window, what="退回顶层")
    _select(window, root / ALBUM)

    window.clear_album_cover()

    assert window._banner.text() == f"「{ALBUM}」恢复用里面的第一张照片当封面。"


# ---- 设不了的时候 -----------------------------------------------------------


def test_a_failure_is_said_out_loud_in_the_core_wording(
    qt_app: QApplication, window: MainWindow, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设不上时横幅上说的是 ``core`` 给的那句中文，而不是一句含糊的「发生错误」。

    真的会失败的情形有好几种（这个库没开配置目录、目录刚被删了、磁盘只读），
    共同点是**都已经想清楚了该怎么说** —— 界面这一侧只负责把它说出来，
    而且失败就是失败：不改内存里那份配置（否则封面会「记得」一个压根没存下来的路径）。

    「绝不把配置目录偷偷建回来」那条红线由 ``tests/core/library/test_facade.py``
    在真实文件系统上守着：那里删掉的是真的目录，不必与缩略图线程赛跑。
    """
    message = "这个库里没有 .gallery 目录，封面设置没有地方保存。"
    _enter_album(qt_app, window, root)
    _select(window, root / ALBUM / SECOND)

    def _refuse(_album: Path, _photo: Path) -> None:
        raise ConfigError(message, path=root)

    library = window._library
    assert library is not None
    monkeypatch.setattr(library, "set_album_cover", _refuse)

    window.set_selected_as_album_cover()

    assert window._banner.text() == message
    assert load_config(root).album_covers == {}
    assert library.has_album_cover(root / ALBUM) is False


# ---- 写封面这件事在代码里也要一处可查 ----------------------------------------


#: 界面上写封面只该发生在一个地方，与 ``tests/test_guards.py`` 那条同一个理由。这一条**不进
#: 那一份清单**：封面写的是 ``config.toml`` 而不是照片，混进去会让那份清单变得不真
#: （ADR 0028 决策 5）。
COVER_WRITE_CALLS = ("set_album_cover(", "clear_album_cover(")
COVER_WRITE_MODULE = "write_ops.py"
UI_ROOT = Path(__file__).resolve().parents[2] / "src" / "gallery" / "ui"


def test_setting_a_cover_happens_in_write_ops_and_nowhere_else() -> None:
    """界面上写封面的调用点只许出现在 ``ui/write_ops.py`` 里。

    封面落在那里（它确实是那种写入），那就该被同样地钉住，否则下一条命令又会散回
    几千行装配代码里，而那时只剩人记。
    """
    offenders: list[str] = []
    for path in sorted(UI_ROOT.rglob("*.py")):
        if path.name == COVER_WRITE_MODULE or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if any(call in line for call in COVER_WRITE_CALLS):
                offenders.append(f"{path.name}:{number} {line.strip()}")

    assert not offenders, f"界面上写封面的调用只许出现在 ui/{COVER_WRITE_MODULE}：{offenders}"
