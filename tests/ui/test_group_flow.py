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
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    load_session,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
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

    **假实现连「按坐标缓存」一起照搬**：真解析器（``core.places.resolver``）问过一次
    的坐标就不再问，而分组与打开时那一遍解析会拿到同一批照片 —— 不缓存的话，
    计数里混进去的全是这份假实现独有的重复，问不出「到底问了几次」。
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

    # 只换掉「按坐标查」这一步：解析器的其余行为（数据加载）都是真的，
    # 但测试因此不联网、也不依赖本机有没有下过 GeoNames 数据。
    #
    # ``ready`` 一并改成真：真解析器是在 ``place_for`` 里加载数据、加载完才 ready 的，
    # 而这里这一步整个被换掉了，不改的话索引会把每一行都记成「这次没查成」。
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
    """把照片的修改时间钉到 :data:`MTIME_BASE` + ``step``。

    默认排序按**修改时间**（``DEFAULT_SORT_FACET``），于是未分组的顺序不能靠
    「谁先被创建」：文件系统给的是真实时间，那不是断言能说清楚的东西。
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
    """转到「扫描结束、元数据读完、地点解析完、顺序已经排好」为止。"""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        settled = (
            window._scan_job is None and window._facet_job is None and window._place_job is None
        )
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


# ---- 分组 ----------------------------------------------------------------------------


def test_grouping_turns_the_level_into_group_cards(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    root = _library(tmp_path, make_photo, list(DATES))
    _touch_all(root, sorted(DATES))  # 默认排序按修改时间，钉死这一层未分组的顺序
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)
    # 还没分组时这一层就是那四张照片，顺序按钉好的修改时间
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
    # 问的是**几个不同的坐标**，不是几张照片：三月在西安，另外两张在同一个巴黎坐标上
    # （解析器按坐标缓存，core 那边单独钉住）。分组那一遍与打开时的整库解析共用结论。
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


# ---- 打开库时把地点解析一遍（ADR 0010）-------------------------------------------------


def test_opening_a_library_resolves_places_for_the_whole_library(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """**不用**先选分组：打开库就跑一遍，之后索引里就有结论了。

    这是这一版改动的全部意义：以前地点要等用户手动分组才落库，面板只能自己临时算。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    # 没有分组，就是普通的三张照片（默认按修改时间排）
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
    """关掉再打开同一个库：结论还在索引里，一次反查都不必再来。

    「每开一次库就重算一遍整库」是这套缓存最怕的毛病。
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
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if (
            window._banner.isVisible()
            and window._banner.text()
            and (not seen or seen[-1] != window._banner.text())
        ):
            seen.append(window._banner.text())
        if window._scan_job is None and window._facet_job is None and window._place_job is None:
            qt_app.processEvents()
            return seen
        time.sleep(0.01)
    return seen


def test_an_already_resolved_library_shows_no_banner(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """空跑就**一声不吭**：横幅不许说「正在解析…」，因为根本没有东西在解析。

    那句话原本每次打开都闪一下，看着像又在重算一遍 —— 用户没法分辨是又在干活，
    还是软件出了毛病。换成按拍摄时间排序也一样：索引里已经有的那一遍直接排序，
    不起后台线程、不报「正在读取元数据」。
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
    """同一个会话里 A → B → A：回到 A 不该把那一遍再起一次。

    记账要是只记「上一个库」，切回来就会起一遍空跑 —— 白占一个后台线程，
    还会让用户以为软件在反复重算。
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

    # 两个库都记着 → 切回 A 不会再起一遍
    assert first_root in window._place_pass_roots
    assert window._place_job is None
    window.close()


def test_the_pass_does_not_steal_the_banner_from_someone_else(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_facet_source: object,
    fake_places: Callable[[], int],
) -> None:
    """后台进度不许顶掉用户刚做完的事的确认。

    横幅是全局共享的一条，谁都能写；而「已把 3 项移到废纸篓」这种确认比后台进度
    重要得多 —— 顶掉了用户就再也看不到自己刚才做了什么。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    # 任务进行中，横幅上已经有别人的话了
    window._banner.setText("已把 3 项移到废纸篓")
    window._banner.show()
    window._place_notice_text = None
    window._on_place_pass_work(3)

    assert window._banner.text() == "已把 3 项移到废纸篓", "后台进度不该抢别人的话"
    window.close()


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


def test_photos_are_sorted_by_modified_time_without_asking(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认排序就是按修改时间：没打开过排序菜单的人也该按时间看照片。

    菜单也要把这一项勾上 —— 否则菜单与实际状态对不上，用户会以为软件没听他的话。
    """
    root = _library(tmp_path, make_photo, list(DATES))
    # 故意按与拍摄时间、与文件名都不同的顺序钉修改时间：默认排的确实是它
    _touch_all(root, ["三月.jpg", "没有时间.jpg", "四月.jpg", "去年十二月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert _labels(window) == ["三月.jpg", "没有时间.jpg", "四月.jpg", "去年十二月.jpg"]
    assert window._sort_actions["modified"].isChecked()
    assert window._descending_action.isEnabled() is True
    window.close()


def test_the_default_sort_needs_no_metadata_read(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """默认排序只看文件本身：打开库不必等 exiftool，也就不必报那一屏进度。

    没有 exiftool 的人（以及不想为一屏照片等几秒的人）拿到的是同一个首屏。
    """
    root = _library(tmp_path, make_photo, ["三月.jpg", "四月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态")
    _settle(qt_app, window)

    assert window._needs_facet_read() is False
    assert _labels(window) == ["三月.jpg", "四月.jpg"]
    window.close()


def test_there_is_no_sorting_off_option(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: object
) -> None:
    """「不排序」这一项没了：网格永远按某个字段排。

    摆着它等于承认「不按任何字段排」也是正当选择，那条路只会让人在两种看不懂的
    顺序之间来回试；真想按名字看，「文件名」那一项摆在那儿。
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
    """按文件名排序是纯算的，切换到它不该**新增**一次 EXIF 读取。

    比的是增量而不是零：打开库时那一遍地点解析会读一遍整个库（ADR 0010），
    那次读取与排序字段无关。
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
    # 这里停在库根的相册模式，网格上是一张相册卡片，**一个组都没有** —— 分组只作用在
    # 照片上，照片要等点进相册之后才出现。所以此处只能等「活干完了」，不能等组：
    # `groups=True` 在这里是永远等不到的条件，会白白空转满 10 秒（这一条曾占掉整轮
    # 套件三成的时间）。组由下面 `_watch_until_settled` 那一行负责等。
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
    """换排序字段同样先攒后摆：旧顺序一帧都不许出现。

    钉出来的修改时间顺序是「三月 → 去年十二月 → 四月」，按拍摄时间则是
    「去年十二月 → 三月 → 四月」—— 两边不一样，所以这一条真的在看「有没有闪」。
    断言里数的是帧：重排期间网格要么是空的，要么直接就是排好的那一版。
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
    # 默认排序（修改时间）下这一组是「四月 → 三月」，与按拍摄时间排**反过来** ——
    # 换字段才有东西可换，「不许闪一屏旧顺序」才真的被测到。
    _touch_all(root, ["去年十二月.jpg", "四月.jpg", "三月.jpg"])
    window = _open(qt_app, root, session_base=tmp_path / "状态", group_facet="captured_year")
    _settle(qt_app, window, groups=True)
    window.enter_group(next(group for group in _groups(window) if group.label == "2024 年"))
    _settle(qt_app, window)
    before = _names(window)
    assert before == ["四月.jpg", "三月.jpg"]  # 组内按当前的排序字段（修改时间）

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
