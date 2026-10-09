"""分组与排序在窗口里的整条链：菜单选字段 → 组卡片 → 双击进入 → 组内排序 → `Esc` 退回。

元数据读取用假的批量来源 ``fake_facet_source``，不依赖本机装了 exiftool。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402
from waiting import wait_until, wait_while_watching, window_is_idle

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    load_config,
    prepare_library,
)
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.write_ops import flattened_entries  # noqa: E402

#: 测试库里每张照片的拍摄时间（按文件名对应）。
DATES = {
    "三月.jpg": "2024:03:16 10:00:00",
    "四月.jpg": "2024:04:16 10:00:00",
    "去年十二月.jpg": "2023:12:16 10:00:00",
    "没有时间.jpg": None,
}

#: 照片自带的 GPS（相机原图通常只写坐标、不写 IPTC 地点）。
GPS: dict[str, dict[str, str]] = {
    "三月.jpg": {
        "EXIF:GPSLatitude": "34.215",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "108.965",
        "EXIF:GPSLongitudeRef": "East",
    },
    "四月.jpg": {
        "EXIF:GPSLatitude": "48.805",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "2.120",
        "EXIF:GPSLongitudeRef": "East",
    },
    "去年十二月.jpg": {
        "EXIF:GPSLatitude": "48.805",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "2.120",
        "EXIF:GPSLongitudeRef": "East",
    },
}


@pytest.fixture
def fake_places(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """把地点反查换成假的：按「纬度 > 40 是巴黎，否则西安」回答，并数着问了几次。

    连「按坐标缓存」一起照搬：不缓存的话，计数里混进去的全是这份假实现独有的重复。
    """
    calls = {"count": 0}
    memo: dict[tuple[float, float], ResolvedPlace] = {}

    def place_for(
        self: object, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace:
        key = (round(latitude, 4), round(longitude, 4))
        if key in memo:
            return memo[key]
        calls["count"] += 1
        answer = (
            ResolvedPlace(city="巴黎", region="法兰西岛", country="法国")
            if latitude > 40
            else ResolvedPlace(city="西安", region="陕西省", country="中国")
        )
        memo[key] = answer
        return answer

    # 只换掉「按坐标查」这一步，解析器的其余行为（数据加载）都是真的，测试因此不联网。
    # ``ready`` 一并改成真：不改的话索引会把每一行都记成「这次没查成」。
    monkeypatch.setattr(LazyPlaceResolver, "place_for", place_for)
    monkeypatch.setattr(LazyPlaceResolver, "ready", property(lambda _self: True))
    return lambda: calls["count"]


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
            raw: dict[str, object] = dict(GPS.get(Path(path).name, {}))
            when = DATES.get(Path(path).name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)
    return lambda: list(asked)


def _library(tmp_path: Path, make_photo: Callable[..., Path], names: Sequence[str]) -> Path:
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        make_photo(name, directory=root)
    prepare_library(root)
    return root


#: 测试里的修改时间基准（2023-11-14 前后）。具体值无所谓，只要先后是确定的。
MTIME_BASE = 1_700_000_000.0


def _touch(path: Path, step: float) -> None:
    """把照片的修改时间钉到 :data:`MTIME_BASE` + ``step``：默认排序按**修改时间**，
    未分组的顺序不能靠「谁先被创建」——文件系统给的是真实时间。
    """
    os.utime(path, (MTIME_BASE + step, MTIME_BASE + step))


def _touch_all(root: Path, names: Sequence[str]) -> None:
    """按**给定顺序**给这些照片钉修改时间（第几个就是第几个时间点）。"""
    for step, name in enumerate(names):
        _touch(root / name, float(step))


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
    """开一个窗口。``remember=False`` 表示沿用上一次写进会话的那份，否则测试自己
    把刚记住的选择覆盖掉了。
    """
    if remember:
        save_session(SessionState(recent_roots=(root,)), base=session_base)
        set_library_ui(
            root,
            browse_mode=browse_mode,
            group_facet=group_facet,
            sort_facet=sort_facet,
            sort_descending=sort_descending,
        )
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    return window


def _row_of_group(window: MainWindow, label: str) -> int:
    """名字是 ``label`` 的那张组卡片在第几行。**别硬编码第 0 行**：组卡片的顺序会变。"""
    for row in range(window._model.rowCount()):
        group = window._model.group_at(row)
        if group is not None and group.label == label:
            return row
    raise AssertionError(f"这一层上没有名为「{label}」的组卡片")


def _select_rows(window: MainWindow, *rows: int) -> None:
    """按代码选中若干行（等价于用户按住 ⌘ 连点）。**先设当前项再选**：
    ``setCurrentIndex`` 会把选区收成那一格，顺序反了多选就只剩一格。
    """
    window._grid.setCurrentIndex(window._model.index(rows[0], 0))
    selection = window._grid.selectionModel()
    assert selection is not None
    selection.clearSelection()
    for row in rows:
        selection.select(
            window._model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def _settle(qt_app: QApplication, window: MainWindow, *, groups: bool = False) -> None:
    """转到「扫描结束、元数据读完、地点解析完、顺序已经排好」为止。

    等条件与轮询只在 :mod:`waiting` 里那一份；不成立**直接抛** —— 超时也返回的话，
    扫描卡住与扫描成功长得一模一样。
    """
    assert wait_until(
        qt_app,
        lambda: window_is_idle(window) and (not groups or window._model.group_count() > 0),
    ), "扫描、分组与地点解析没有在时限内收工"
    qt_app.processEvents()  # 扫描是流式的，再转一圈确认没有迟到的一批


def _watch_until_settled(
    qt_app: QApplication, window: MainWindow, ready: Callable[[], bool]
) -> list[tuple[str, ...]]:
    """一边转事件循环一边记录网格上出现过哪些条目（按出现顺序去重）。"""
    seen = _watch_names(qt_app, window, ready)
    return [tuple(type(name).__name__ for name in step) for step in seen]


def _watch_names(
    qt_app: QApplication, window: MainWindow, ready: Callable[[], bool]
) -> list[tuple[str, ...]]:
    """同上，但记录**条目名字**：顺序对不对只有名字能说明白。

    靠模型自己的通知（``modelReset`` / ``rowsInserted``）记下每一版，不靠轮询：换排序是
    同步清空后重排的，两步可能落在**同一轮** ``processEvents`` 里，轮询会整个漏掉中间
    那帧空网格。轮询那次取样仍然留着 —— 改缩略图、改名走的是 ``dataChanged``，
    只有轮询看得见。
    """
    seen: list[tuple[str, ...]] = []
    model = window._model

    def record() -> None:
        snapshot = tuple(entry.name for entry in model.entries())
        if not seen or seen[-1] != snapshot:
            seen.append(snapshot)

    def on_rows_inserted(*_args: object) -> None:
        record()

    model.modelReset.connect(record)
    model.rowsInserted.connect(on_rows_inserted)
    try:
        record()  # 观察的第一帧：此刻摆着的东西（换排序同步清空后的空网格）
        # 轮询与超时用 waiting 里那一份（它顺带在收尾时多记一帧，见 wait_while_watching）。
        wait_while_watching(
            qt_app,
            lambda: window._scan_job is None and window._facet_job is None and ready(),
            record,
            what="这一层排好",
        )
    finally:
        model.modelReset.disconnect(record)
        model.rowsInserted.disconnect(on_rows_inserted)
    return list(dict.fromkeys(seen))


def _names(window: MainWindow) -> list[str]:
    return [entry.name for entry in window._model.entries()]


def _labels(window: MainWindow) -> list[str]:
    return [entry.name for entry in window._model.entries()]


def _groups(window: MainWindow) -> list[GroupRef]:
    return [entry for entry in window._model.entries() if isinstance(entry, GroupRef)]


def test_menus_expose_group_and_sort_fields(qt_app: QApplication, tmp_path: Path) -> None:
    window = MainWindow(session_base=tmp_path / "状态")

    group_menu = window._group_actions
    sort_menu = window._sort_actions

    assert set(group_menu) >= {"captured_month", "captured_year", "camera", "keyword"}
    assert set(sort_menu) >= {"name", "size", "captured"}
    assert None not in sort_menu  # 没有「不排序」：网格永远按某个字段排
    assert window._descending_action.text() == "逆序"
    window.close()


def test_restored_session_checks_the_right_items(qt_app: QApplication, tmp_path: Path) -> None:
    """从会话恢复出来的选择要把菜单勾上，否则菜单与实际状态对不上。"""
    root = _library(tmp_path, lambda *a, **k: None, [])  # type: ignore[arg-type]

    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_month")

    assert window._group_actions["captured_month"].isChecked()
    assert window._group_actions[None].isChecked() is False
    window.close()


def test_grouping_turns_the_level_into_group_cards(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    _touch_all(root, sorted(DATES))  # 默认排序按修改时间，固定这一层未分组的顺序
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    assert _labels(window) == sorted(DATES)

    window.set_group_facet("captured_month")
    _settle(qt_app, window, groups=True)

    groups = _groups(window)
    assert [(group.label, group.count) for group in groups] == [
        ("2023 年 12 月", 1),
        ("2024 年 3 月", 1),
        ("2024 年 4 月", 1),
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

    # 分组选择是**库级**偏好，写在那个库的 config.toml 里而不在 state.json 里（ADR 0038 决策 4），
    # 于是「在 A 库按年份分组」不会跟着切到 B 库。
    assert load_config(root).ui.group_facet == "captured_year"

    again = _open(qt_app, root, session_base=session_base, remember=False)
    _settle(qt_app, again, groups=True)
    assert [group.label for group in _groups(again)] == ["2023 年", "2024 年", "时间未知"]
    again.close()


def test_grouping_by_country_resolves_places_from_gps(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """相机原图只写 GPS、不写 IPTC 地点 —— 分组必须能用坐标反查出国家。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="country")
    _settle(qt_app, window, groups=True)

    # 国家之间按名称升序（时间类分组才是新 → 旧）
    assert [(group.label, group.count) for group in _groups(window)] == [
        ("中国", 1),
        ("法国", 2),
    ]
    # 问的是**几个不同的坐标**而不是几张照片：另外两张在同一个巴黎坐标上（解析器按坐标缓存）。
    assert fake_places() == 2
    window.close()


def test_photos_without_gps_land_in_the_unknown_group(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """没有坐标也没有文件地点的照片归到「国家未知」，而不是被编一个国家。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "没有时间.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="country")
    _settle(qt_app, window, groups=True)

    assert [(group.label, group.count) for group in _groups(window)] == [
        ("中国", 1),
        ("国家未知", 1),
    ]
    assert fake_places() == 1  # 没有坐标的那张不必问
    window.close()


def test_opening_a_library_resolves_places_for_the_whole_library(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """**不用**先选分组：打开库就跑一遍，之后索引里就有结论了（以前要等用户手动分组才落库）。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert sorted(_labels(window)) == sorted(["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    stored = window._library.cached_facets(
        [PhotoRef.from_path(root / name) for name in ("三月.jpg", "四月.jpg", "去年十二月.jpg")]
    )
    assert {row.country for row in stored.values()} == {"中国", "法国"}
    assert all(row.place_checked for row in stored.values())
    window.close()


def test_reopening_the_same_library_does_not_resolve_again(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """关掉再打开同一个库：结论还在索引里，一次反查都不必再来 ——「每开一次库就重算一遍
    整库」是这套缓存最怕的毛病。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    first = fake_places()
    assert first > 0
    window.close()

    again = _open(qt_app, root, session_base=tmp_path / "状态", remember=False)
    _settle(qt_app, again)

    assert fake_places() == first
    again.close()


def _banners_while_busy(qt_app: QApplication, window: MainWindow) -> list[str]:
    """转事件循环直到后台活干完，把横幅上出现过的每一句记下来。"""
    seen: list[str] = []

    def record() -> None:
        text = window._banner.text()
        if window._banner.isVisible() and text and (not seen or seen[-1] != text):
            seen.append(text)

    # 不成立直接抛：超时也返回的话，「后台没干完」与「干完了、只说过两句」在返回值上没有
    # 区别，「空跑就一声不吭」那条用例会把缺的那几句算到账上。
    wait_while_watching(
        qt_app,
        lambda: (
            window._scan_job is None and window._facet_job is None and window._place_job is None
        ),
        record,
        what="后台那一遍",
    )
    return seen


def test_an_already_resolved_library_shows_no_banner(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """空跑就**一声不吭**：横幅不许说「正在解析…」。那句话每次打开都闪一下的话，
    看着像又在重算一遍，分不清是又在干活还是软件出了毛病。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg"])
    first = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, first)
    first.close()

    again = _open(qt_app, root, session_base=tmp_path / "状态", remember=False)
    seen = _banners_while_busy(qt_app, again)

    assert seen == [], f"库里都解析过了，不该再报进度：{seen}"
    again.close()


def test_switching_back_and_forth_does_not_start_it_twice(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """同一个会话里 A → B → A：回到 A 不该把那一遍再起一次。记账要是只记「上一个库」，
    切回来就会起一遍空跑，白占一个后台线程。
    """
    first_root = _library(tmp_path / "一", make_photo, ["三月.jpg", "四月.jpg"])
    second_root = _library(tmp_path / "二", make_photo, ["五月.jpg"])
    window = _open(qt_app, first_root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    assert first_root in window._place_pass_roots

    assert window.open_library(second_root)
    _settle(qt_app, window)
    assert second_root in window._place_pass_roots

    assert window.open_library(first_root)
    _settle(qt_app, window)

    assert first_root in window._place_pass_roots
    assert window._place_job is None
    window.close()


def test_a_cancelled_place_pass_can_start_again(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """**取消那一遍之后还能再起**（ADR 0051 实施）。

    半途取消等于那一遍**没做完**，所以「起过哪些库」那份记账里必须把根拿掉 ——
    而 `_place_pass_roots` 原本只 `.add()` 从不 `discard()`，于是这个库在本进程里
    再也起不来那一遍：面板不出地名、按地点分组也空，**而这两处都不报错**。
    """
    root = _library(tmp_path / "一", make_photo, ["三月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    assert root in window._place_pass_roots

    window._place_job = 999  # 假装那一遍还在跑（同 test_watch_flow 那个手法）
    window._cancel_job("_place_job")

    assert window._place_job is None
    assert root not in window._place_pass_roots, (
        "取消的那一遍没做完，根还留在「起过」名单里就再也起不来"
    )
    window.close()


def test_the_pass_does_not_steal_the_banner_from_someone_else(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """后台进度不许顶掉用户刚做完的事的确认：横幅是全局共享的一条，谁都能写，
    而「已把 3 项移到废纸篓」顶掉了用户就再也看不到自己刚才做了什么。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    # 任务进行中，横幅上已经有别人的话了
    window._banner.setText("已把 3 项移到废纸篓")
    window._banner.show()
    window._banner_text.place.clear()
    window._on_place_pass_work(3)

    assert window._banner.text() == "已把 3 项移到废纸篓", "后台进度不该抢别人的话"
    window.close()


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
    assert [group.label for group in _groups(window)] == ["2023 年", "2024 年", "时间未知"]
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


def _crumb_segments(window: MainWindow) -> list[tuple[str, str]]:
    """面包屑上每一段：``(文字, 是按钮还是标签)``。"""
    out: list[tuple[str, str]] = []
    for index in range(window._breadcrumb._layout.count()):
        item = window._breadcrumb._layout.itemAt(index)
        widget = item.widget() if item else None
        if widget is not None:
            out.append((widget.text(), "按钮" if isinstance(widget, QToolButton) else "标签"))
    return out


def _album_with_a_group(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> MainWindow:
    """开一个相册模式的窗口，停在「旅行」这一层里的「2024 年」那一组里。"""
    root = tmp_path / "照片"
    make_photo("三月.jpg", directory=root / "旅行")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )
    _settle(qt_app, window)
    window.enter_folder(root / "旅行")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)
    return window


def test_the_album_stays_a_link_while_a_group_of_it_is_open(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """相册底下开着分组时，面包屑上的**相册那一段仍然要能点回去**：当前所在的是那一组，
    面包屑若把最后一段路径当成「当前所在」，相册就被画成不可点的粗体标签。
    """
    window = _album_with_a_group(qt_app, tmp_path, make_photo)

    assert _crumb_segments(window) == [
        ("照片", "按钮"),
        ("▸", "标签"),
        ("旅行", "按钮"),
        ("▸", "标签"),
        ("2024 年", "标签"),
    ], "当前所在是那一组：路径上的每一段都该是可点的链接"
    window.close()


def test_clicking_the_album_leaves_the_group_and_lands_on_that_album(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """点面包屑上的相册：既退出了那一组，也回到了那个相册本身。"""
    root = tmp_path / "照片"
    make_photo("三月.jpg", directory=root / "旅行")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )
    _settle(qt_app, window)
    window.enter_folder(root / "旅行")
    _settle(qt_app, window, groups=True)
    at_album = _labels(window)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    window._on_breadcrumb_requested(root / "旅行")
    _settle(qt_app, window)

    assert window._group_view is None, "点相册就是回到相册这一层，不该还留在组里"
    assert window._current_dir == root / "旅行"
    assert _labels(window) == at_album, "回到的是相册那一层本身"
    window.close()


def test_clicking_the_library_leaves_the_group_and_lands_on_the_root(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """点面包屑上的**库根**：退到根那一层，而不是把同一个分组又列一遍。

    这一条盯的是「弹掉旧的那摞」：目标是库根时新链一帧都不压，于是旧链没弹掉就是**整个栈
    没变**，重列一遍仍是那个分组 —— 症状是「点了没反应」。
    """
    root = tmp_path / "照片"
    make_photo("三月.jpg", directory=root / "旅行")
    prepare_library(root)
    window = _open(
        qt_app,
        root,
        session_base=tmp_path / "状态",
        browse_mode=BROWSE_MODE_ALBUMS,
        group_facet="captured_year",
    )
    _settle(qt_app, window)
    at_root = _labels(window)
    window.enter_folder(root / "旅行")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)

    window._on_breadcrumb_requested(root)
    _settle(qt_app, window)

    assert window._group_view is None, "点库根就该退出那一组"
    assert window._current_dir is None, "根那一层没有相册帧"
    assert _labels(window) == at_root, "回到的是库根那一层本身"
    window.close()


def test_escape_after_clicking_an_album_does_not_fall_back_into_the_group(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """点面包屑上的相册之后按 Esc，退的是**上一层**，不是那个分组。

    旧相册的帧若留在栈里（压在分组底下），弹掉新压的那一帧就直接回到分组了 ——
    而「落地那一层对不对」量不出来：上一条用例那时也是绿的。
    """
    window = _album_with_a_group(qt_app, tmp_path, make_photo)

    window._on_breadcrumb_requested(window._library.root / "旅行")
    _settle(qt_app, window)

    window.go_up()
    _settle(qt_app, window)  # 库根那一层只有文件夹，没有组卡片

    assert window._group_view is None, "退上去又回到那个分组了 —— 旧的那一帧没弹掉"
    assert window._current_dir is None, "退上去退到了库根，不是那个相册"
    window.close()


def test_photos_are_sorted_by_modified_time_without_asking(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认排序就是按修改时间，且菜单要把这一项勾上，否则菜单与实际状态对不上。"""
    root = _library(tmp_path, make_photo, list(DATES))
    # 故意按与拍摄时间、与文件名都不同的顺序钉修改时间：默认排的确实是它
    _touch_all(root, ["三月.jpg", "没有时间.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert _labels(window) == ["三月.jpg", "没有时间.jpg", "四月.jpg", "去年十二月.jpg"]
    assert window._sort_actions["modified"].isChecked()
    assert window._descending_action.isChecked() is False
    assert window._descending_action.isEnabled() is True
    window.close()


def test_the_default_sort_needs_no_metadata_read(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认排序只看文件本身：打开库不必等 exiftool，也就不必报那一屏进度。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert window._needs_facet_read() is False
    assert _labels(window) == ["三月.jpg", "四月.jpg"]
    window.close()


def test_there_is_no_sorting_off_option(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """「不排序」这一项没了：摆着它等于承认「不按任何字段排」也是正当选择，
    那条路只会让人在两种看不懂的顺序之间来回试。
    """
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert None not in window._sort_actions
    assert not any("不排序" in action.text() for action in window._sort_actions.values())
    # 点了也换不成：窗口手上永远有一个排序字段
    window.set_sort_facet(None)
    _settle(qt_app, window)

    assert window._sort_actions["modified"].isChecked()
    window.close()


def test_the_chosen_sort_facet_is_remembered_across_restarts(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    session_base = tmp_path / "状态"
    window = _open(qt_app, root, session_base=session_base)
    _settle(qt_app, window)

    window.set_sort_facet("name")
    _settle(qt_app, window)
    assert _labels(window) == sorted(DATES)
    window.close()

    again = _open(qt_app, root, session_base=session_base, remember=False)
    _settle(qt_app, again)

    assert again._sort_actions["name"].isChecked()
    assert _labels(again) == sorted(DATES)
    again.close()


def test_sorting_reorders_without_grouping(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, ["b.jpg", "a.jpg", "c.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    window.set_sort_facet("name")
    _settle(qt_app, window)
    assert _labels(window) == ["a.jpg", "b.jpg", "c.jpg"]

    # 名称类的自然方向是升序，所以「逆序」把它翻成降序。
    window.set_sort_descending(True)
    _settle(qt_app, window)
    assert _labels(window) == ["c.jpg", "b.jpg", "a.jpg"]
    window.close()


def test_the_default_sort_shows_the_newest_photo_first(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认那一档（修改时间）**升序**（旧 → 新），而「逆序」没有勾（ADR 0049）。

    这两半必须一起钉：只钉顺序的话，把「逆序」的默认值翻成勾上也能过 —— 那读起来
    同样是「最新那张不在最前」，而用户点一下「逆序」会看到顺序没变。
    """
    root = _library(tmp_path, make_photo, ["最早.jpg", "最新.jpg", "中间.jpg"])
    _touch_all(root, ["最早.jpg", "中间.jpg", "最新.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert _labels(window) == ["最早.jpg", "中间.jpg", "最新.jpg"]
    assert window._descending_action.isChecked() is False
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


def test_a_second_grouping_says_nothing_about_reading_metadata(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: Callable[[], list[Path]],
) -> None:
    """**索引里全都有时不报「正在读取」** —— 报的是一件没发生的事。

    第一次换分组字段要读一次 EXIF，那句横幅是对的；第二次索引里已经全有了，看到的应该是
    「立刻排好」，而不是又冒一句「正在读取 N 张照片的元数据」—— 分不清这是又在干活还是软件
    出了毛病（ADR 0014 决策 4 同一个取向）。

    「查索引」搬进后台之后尤其要紧：判定挪到工作线程里，**报横幅的时机也跟着挪**。
    """
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    window.set_group_facet("captured_month")
    _settle(qt_app, window, groups=True)

    seen: list[str] = []
    window._show_progress_notice = lambda text: seen.append(text)  # type: ignore[method-assign]
    window.set_group_facet("captured_year")
    _settle(qt_app, window, groups=True)

    assert not [text for text in seen if "正在读取" in text], f"索引里全都有却还是报了进度：{seen}"
    window.close()


def test_sorting_by_file_name_never_touches_exiftool(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: Callable[[], list[Path]],
) -> None:
    """按文件名排序是纯算的，切换到它不该**新增**一次 EXIF 读取：比的是增量而不是零，
    打开库时那一遍地点解析会读整个库（ADR 0010），与排序字段无关。
    """
    root = _library(tmp_path, make_photo, ["b.jpg", "a.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    before = fake_facet_source()

    window.set_sort_facet("name")
    _settle(qt_app, window)

    assert _labels(window) == ["a.jpg", "b.jpg"]
    assert fake_facet_source() == before
    window.close()


def test_entering_an_album_never_shows_the_ungrouped_photos_first(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """点进相册不许先出一屏照片、下一秒全变成组卡片：网格要么是空的，要么直接就是最终的
    组卡片，**中间那一屏错误的顺序不该出现**。
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
    # 库根的相册模式下一张相册卡片、**一个组都没有**（分组只作用在照片上），所以这里只能等
    # 「活干完了」：`groups=True` 是永远等不到的条件。组由下面那一行负责等。
    _settle(qt_app, window)
    row = next(
        index for index, entry in enumerate(window._model.entries()) if entry.name == "子相册"
    )

    window._on_entry_activated(window._model.index(row, 0))
    seen = _watch_until_settled(qt_app, window, lambda: window._model.group_count() > 0)

    assert not any("PhotoRef" in step for step in seen)  # 一帧都不许出现未分组的照片
    assert window._model.group_count() == 2
    window.close()


def test_switching_the_sort_never_shows_the_old_order_first(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """换排序字段同样先累积后摆：旧顺序一帧都不许出现。钉出来的修改时间顺序与按拍摄时间排
    不一样，所以这一条真的在看「有没有闪」。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    _touch_all(root, ["三月.jpg", "去年十二月.jpg", "四月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    assert _names(window) == ["三月.jpg", "去年十二月.jpg", "四月.jpg"]

    window.set_sort_facet("captured")
    seen = _watch_names(qt_app, window, lambda: window._model.rowCount() > 0)

    assert _names(window) == ["去年十二月.jpg", "三月.jpg", "四月.jpg"]
    assert seen == [(), ("去年十二月.jpg", "三月.jpg", "四月.jpg")]
    window.close()


def test_entering_a_group_also_waits_for_the_metadata_sort(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """进组之后再换排序字段，同样不能先闪一屏旧顺序。"""
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    # 默认排序（修改时间）下这一组与按拍摄时间排**反过来**——换字段才有东西可换。
    # 两档都是升序（ADR 0049），所以「反过来」只可能来自两个字段给出的先后不同。
    _touch_all(root, ["去年十二月.jpg", "四月.jpg", "三月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)
    before = _names(window)
    # 三月的修改时间比四月**新**，而拍摄时间比四月**旧** —— 两档给的先后正好相反。
    assert before == ["四月.jpg", "三月.jpg"]

    window.set_sort_facet("captured")  # 三月（03-16）在四月（04-16）之前
    _settle(qt_app, window)
    assert _names(window) == ["三月.jpg", "四月.jpg"]

    window.set_sort_descending(True)
    seen = _watch_names(qt_app, window, lambda: window._model.rowCount() > 0)

    # 勾了逆序：拍摄时间降序，于是变成四月在前。
    assert _names(window) == ["四月.jpg", "三月.jpg"]
    # 关键在这一行：**旧顺序一帧都不许出现**（先摆一屏三月/四月、下一帧才换成四月/三月也不行）。
    assert seen == [(), ("四月.jpg", "三月.jpg")]
    window.close()


def test_group_cards_refuse_rename_and_trash(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """改不了名的是**那张卡片**；删得掉的是它代表的那批照片。⌘R 仍要说清为什么
    （卡片不是磁盘上的东西，ADR 0004），⌘⌫ 则把组里的照片整组送进废纸篓。
    """
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window._grid.select_row(0)
    window._update_action_states()
    target = _groups(window)[0]

    assert window._rename_action.isEnabled() is False
    assert window._trash_action.isEnabled() is True
    window.rename_current_entry()
    assert "分组" in window._banner.text()

    window.trash_selected()
    _settle(qt_app, window)

    assert not any(photo.path.exists() for photo in target.photos), "组里的照片一张都不该剩"
    # 组空了，那张卡片也就没有意义了：它代表的就是「这些照片」（ADR 0020 决策 4）。
    assert all(group.key != target.key for group in _groups(window))
    window.close()


def test_a_group_card_acts_on_all_of_its_photos(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """选中卡片就是选中它代表的**全部**组员：分组那一层里只有相册与组卡片，照片被分组忽略掉了，
    「只删组里一部分」只可能发生在**进组之后**选了几张。
    """
    root = _library(tmp_path, make_photo, list(DATES))
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    year = next(group for group in _groups(window) if group.label == "2024 年")
    assert len(year.photos) == 2
    # **点名选 2024 那一组**，不硬编码第 0 行：组卡片默认升序（ADR 0049），第 0 行是 2023。
    _select_rows(window, _row_of_group(window, "2024 年"))
    window._update_action_states()

    assert window._trash_action.isEnabled() is True
    assert sorted(window._selected_paths()) == sorted(photo.path for photo in year.photos), (
        "⌘⌫ 要作用的是整组，不该只挑其中几张"
    )
    window.close()


def test_flattening_never_counts_one_photo_twice() -> None:
    """同一张照片既是组员、又被单独选中时只算一次：同一个路径递两遍，第二次要么在废纸篓那里
    报成一次「没移走」，要么在粘贴时造出一张「一张 2.jpg」。
    """
    photo = PhotoRef(path=Path("/库/一张.jpg"), size=1, mtime_ns=1)
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))

    assert flattened_entries([group, photo]) == [photo]
    assert flattened_entries([photo, group]) == [photo]


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
