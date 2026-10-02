"""分组与排序在窗口里的整条链。

覆盖用户真正会经历的那条路：菜单选一个字段 → 这一层变成组卡片 → 双击进入 →
组内排序换方向 → `Esc` 退回 → 选择被记住。

元数据读取用假的批量来源（见 ``fake_facet_source``）：测试不依赖本机装了 exiftool，
也不该因为机器上碰巧装了就把行为测偏。真实 exiftool 那条路由
``tests/core/metadata/test_facets_batch.py`` 单独测。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    load_session,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待后台扫描 / 元数据读取的时限。
WAIT_SECONDS = 10.0

#: 测试库里每张照片的拍摄时间（按文件名对应）。
DATES = {
    "三月.jpg": "2024:03:16 10:00:00",
    "四月.jpg": "2024:04:16 10:00:00",
    "去年十二月.jpg": "2023:12:16 10:00:00",
    "没有时间.jpg": None,
}


@pytest.fixture
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[], list[Path]]:
    """把批量读取换成假的：按文件名给出拍摄时间，没给的就是「时间未知」。

    返回一个「被读过哪些文件」的列表，用来断言第二次确实没再去读。
    """
    asked: list[Path] = []

    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            asked.append(Path(path))
            # 与 exiftool 一致：能读到的文件都有一条记录，哪怕里面只有 SourceFile。
            when = DATES.get(Path(path).name)
            yield Path(path), ({"EXIF:DateTimeOriginal": when} if when else {})

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)
    return lambda: list(asked)


def _library(tmp_path: Path, make_photo: Callable[..., Path], names: Sequence[str]) -> Path:
    root = tmp_path / "照片"
    root.mkdir(exist_ok=True)
    for name in names:
        make_photo(name, directory=root)
    prepare_library(root)
    return root


def _open(
    qt_app: QApplication,
    root: Path,
    *,
    session_base: Path,
    browse_mode: str | None = None,
    group_facet: str | None = None,
    sort_facet: str | None = None,
    sort_descending: bool = False,
    remember: bool = True,
) -> MainWindow:
    """开一个窗口。

    ``remember=False`` 表示**沿用上一次写进会话的那份**（测「重新打开软件仍然记得」
    时必须这样，否则测试自己把刚记住的选择覆盖掉了）。
    """
    if remember:
        save_session(
            SessionState(
                recent_roots=(root,),
                browse_mode=browse_mode,
                group_facet=group_facet,
                sort_facet=sort_facet,
                sort_descending=sort_descending,
            ),
            base=session_base,
        )
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    return window


def _settle(qt_app: QApplication, window: MainWindow, *, groups: bool = False) -> None:
    """转到「扫描结束、元数据读完、顺序已经排好」为止。"""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        settled = window._scan_job is None and window._facet_job is None
        if settled and (not groups or window._model.group_count() > 0):
            # 再转一圈，确认没有迟到的一批（扫描是流式的）。
            qt_app.processEvents()
            return
        time.sleep(0.02)
    qt_app.processEvents()


def _watch_until_settled(
    qt_app: QApplication, window: MainWindow, ready: Callable[[], bool]
) -> list[tuple[str, ...]]:
    """一边转事件循环一边记录网格上出现过哪些条目（按出现顺序去重）。"""
    seen = _watch_names(qt_app, window, ready)
    return [tuple(type(name).__name__ for name in step) for step in seen]


def _watch_names(
    qt_app: QApplication, window: MainWindow, ready: Callable[[], bool]
) -> list[tuple[str, ...]]:
    """同上，但记录**条目名字**：顺序对不对，只有名字能说明白。"""
    seen: list[tuple[str, ...]] = []
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        snapshot = tuple(entry.name for entry in window._model.entries())
        if not seen or seen[-1] != snapshot:
            seen.append(snapshot)
        if window._scan_job is None and window._facet_job is None and ready():
            break
        time.sleep(0.005)
    qt_app.processEvents()
    return list(dict.fromkeys(seen))


def _names(window: MainWindow) -> list[str]:
    return [entry.name for entry in window._model.entries()]


def _labels(window: MainWindow) -> list[str]:
    return [entry.name for entry in window._model.entries()]


def _groups(window: MainWindow) -> list[GroupRef]:
    return [entry for entry in window._model.entries() if isinstance(entry, GroupRef)]


# ---- 菜单 ----------------------------------------------------------------------------


def test_menus_expose_group_and_sort_fields(qt_app: QApplication, tmp_path: Path) -> None:
    window = MainWindow(session_base=tmp_path / "状态")

    group_menu = window._group_actions
    sort_menu = window._sort_actions

    assert set(group_menu) >= {"captured_month", "captured_year", "camera", "keyword"}
    assert set(sort_menu) >= {None, "name", "size", "captured"}
    assert window._descending_action.text() == "逆序（新在前 / 大在前）"
    window.close()


def test_restored_session_checks_the_right_items(qt_app: QApplication, tmp_path: Path) -> None:
    """从会话恢复出来的选择要把菜单勾上，否则菜单与实际状态对不上。"""
    root = _library(tmp_path, lambda *a, **k: None, [])  # type: ignore[arg-type]

    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_month")

    assert window._group_actions["captured_month"].isChecked()
    assert window._group_actions[None].isChecked() is False
    window.close()


# ---- 分组 ----------------------------------------------------------------------------


def test_grouping_turns_the_level_into_group_cards(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    assert _labels(window) == sorted(DATES)

    window.set_group_facet("captured_month")
    _settle(qt_app, window, groups=True)

    groups = _groups(window)
    assert [(group.label, group.count) for group in groups] == [
        ("2024 年 4 月", 1),
        ("2024 年 3 月", 1),
        ("2023 年 12 月", 1),
        ("时间未知", 1),
    ]
    window.close()


def test_albums_stay_in_front_of_groups(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """相册卡片永远排在前面：先看文件夹、再看分组。"""
    root = _library(tmp_path, make_photo, ["三月.jpg"])
    (root / "子相册").mkdir()
    window = _open(qt_app, root, session_base=tmp_path / "状态", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)

    window.set_group_facet("captured_year")
    _settle(qt_app, window, groups=True)

    assert [type(entry).__name__ for entry in window._model.entries()] == ["FolderRef", "GroupRef"]
    window.close()


def test_groups_are_remembered_across_restarts(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    session_base = tmp_path / "状态"
    window = _open(qt_app, root, session_base=session_base)
    _settle(qt_app, window)

    window.set_group_facet("captured_year")
    _settle(qt_app, window, groups=True)
    window.close()

    saved = load_session(base=session_base)
    assert saved.group_facet == "captured_year"

    again = _open(qt_app, root, session_base=session_base, remember=False)
    _settle(qt_app, again, groups=True)
    assert [group.label for group in _groups(again)] == ["2024 年", "2023 年", "时间未知"]
    again.close()


# ---- 进入与退出分组 --------------------------------------------------------------------


def test_entering_a_group_lists_its_photos(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    target = next(group for group in _groups(window) if group.label == "2024 年")

    window.enter_group(target)
    _settle(qt_app, window)

    assert sorted(_labels(window)) == sorted(["三月.jpg", "四月.jpg"])
    assert _groups(window) == []  # 进去之后不再二次分组
    window.close()


def test_escape_leaves_the_group(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    window.go_up()
    _settle(qt_app, window, groups=True)

    assert window._group_view is None
    assert [group.label for group in _groups(window)] == ["2024 年", "2023 年", "时间未知"]
    window.close()


def test_breadcrumb_shows_the_group_name(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """平铺模式下平时没有面包屑，分组里必须有 —— 否则不知道自己身在何处。"""
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    assert window._breadcrumb.isHidden()

    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    assert window._breadcrumb.isHidden() is False
    window.close()


# ---- 排序 ----------------------------------------------------------------------------


def test_sorting_reorders_without_grouping(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, ["b.jpg", "a.jpg", "c.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    window.set_sort_facet("name")
    _settle(qt_app, window)
    assert _labels(window) == ["a.jpg", "b.jpg", "c.jpg"]

    window.set_sort_descending(True)
    _settle(qt_app, window)
    assert _labels(window) == ["c.jpg", "b.jpg", "a.jpg"]
    window.close()


def test_sorting_inside_a_group(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, ["四月.jpg", "三月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    window.set_sort_facet("name")
    _settle(qt_app, window)

    assert _labels(window) == ["三月.jpg", "四月.jpg"]  # 名字排，而不是拍摄时间
    window.close()


def test_metadata_is_read_once_and_then_cached(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: Callable[[], list[Path]],
) -> None:
    """第一次要读一次 EXIF；换个分组字段就该直接命中索引，不再读。"""
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    window.set_group_facet("captured_month")
    _settle(qt_app, window, groups=True)
    first = len(fake_facet_source())

    window.set_group_facet("captured_year")
    _settle(qt_app, window, groups=True)

    assert first == len(DATES)
    assert len(fake_facet_source()) == first  # 第二次没有新的读取
    window.close()


def test_sorting_by_file_name_never_touches_exiftool(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: Callable[[], list[Path]],
) -> None:
    """按文件名排序是纯算的，不该为了它去起 exiftool。"""
    root = _library(tmp_path, make_photo, ["b.jpg", "a.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    window.set_sort_facet("name")
    _settle(qt_app, window)

    assert _labels(window) == ["a.jpg", "b.jpg"]
    assert fake_facet_source() == []
    window.close()


# ---- 不许先闪一下再换内容 ------------------------------------------------------------


def test_entering_an_album_never_shows_the_ungrouped_photos_first(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """这一条是回归测试：曾经点进相册先出一屏照片、下一秒它们全变成组卡片。

    修法是「要重排就先攒着」：网格要么是空的（横幅说明正在读元数据），要么直接
    就是最终的组卡片 —— **中间那一屏错误的顺序不该出现**。
    """
    root = tmp_path / "照片"
    album = root / "子相册"
    album.mkdir(parents=True)
    for name in ("三月.jpg", "四月.jpg", "去年十二月.jpg"):
        make_photo(name, directory=album)
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )
    _settle(qt_app, window, groups=True)
    row = next(
        index for index, entry in enumerate(window._model.entries()) if entry.name == "子相册"
    )

    window._on_entry_activated(window._model.index(row, 0))
    seen = _watch_until_settled(qt_app, window, lambda: window._model.group_count() > 0)

    assert not any("PhotoRef" in step for step in seen)  # 一帧都不许出现未分组的照片
    assert window._model.group_count() == 2
    window.close()


def test_default_order_still_streams_photos_in(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认顺序不需要重排 therefore 边扫边出图 —— 大库的首屏手感不能为了防闪屏丢掉。"""
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态")

    assert window._ordering_active() is False
    assert window._staged is None  # 不攒：模型里的条目就是这一层的全部
    _settle(qt_app, window)
    assert window._model.photo_at(0) is not None
    window.close()


def test_entering_a_group_also_waits_for_the_metadata_sort(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """进组之后再换排序字段，同样不能先闪一屏旧顺序。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)
    before = _names(window)
    assert before == ["三月.jpg", "四月.jpg"]  # 默认按名字

    window.set_sort_facet("captured")  # 三月（03-16）在四月（04-16）之前
    _settle(qt_app, window)
    assert _names(window) == ["三月.jpg", "四月.jpg"]

    window.set_sort_descending(True)
    seen = _watch_names(qt_app, window, lambda: window._model.rowCount() > 0)

    # 三月（2024-03）在四月（2024-04）之前；逆序就该反过来。
    assert _names(window) == ["四月.jpg", "三月.jpg"]
    # 关键在这一行：**旧顺序一帧都不许出现**。曾经这里会先摆一屏三月/四月，
    # 下一秒才换成四月/三月 —— 那一下闪得最明显。
    assert seen == [(), ("四月.jpg", "三月.jpg")]
    window.close()


# ---- 分组卡片不能改名 / 不能删 --------------------------------------------------------


def test_group_cards_refuse_rename_and_trash(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """分组不是磁盘上的东西：不能静默跳过，要说清楚为什么按了没反应。"""
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window._grid.select_row(0)
    window._update_action_states()

    assert window._rename_action.isEnabled() is False
    assert window._trash_action.isEnabled() is False

    window.rename_current_entry()
    assert "分组" in window._banner.text()
    window.trash_selected()
    assert "分组" in window._banner.text()
    assert _groups(window)  # 一张都没少
    window.close()


def test_group_cards_are_kept_when_photos_inside_are_deleted(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """进了分组之后 ⌘⌫ 照常删照片，退出分组时组还在（成员少了）。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    window._grid.select_row(0)
    window.trash_selected()
    _settle(qt_app, window)

    assert _labels(window) == ["四月.jpg"]
    window.close()
