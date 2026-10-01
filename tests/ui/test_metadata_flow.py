"""元数据面板在**切换照片**时不得清空重画。

切图闪烁是真实缺陷：先前每次切图都先 ``show_loading`` 把面板清空成「正在读取元数据…」，
读完了再换成内容 —— 一闪一闪的，观感很差。现在切图不改面板：命中缓存直接同步显示，
未命中也保留原有内容等新结果换上去；加载提示只在面板本来就是空的时候出现。

这里不比对界面像素，而是记录面板每次 ``set_report`` / ``show_loading`` 的时序，
断言切换过程中没有加载提示。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.config import prepare_library  # noqa: E402
from gallery.core.metadata import build_report  # noqa: E402
from gallery.ui import metadata_panel as panel_module  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描 / 读取完成的时限。
READY_TIMEOUT_SECONDS = 8.0


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


@pytest.fixture
def timeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录面板状态变化的时序。"""
    marks: list[str] = []
    original_set_report = panel_module.MetadataPanel.set_report
    original_show_loading = panel_module.MetadataPanel.show_loading

    def traced_set_report(self: object, report: object) -> None:
        marks.append("set_report")
        original_set_report(self, report)  # type: ignore[arg-type]

    def traced_show_loading(self: object, name: str = "") -> None:
        marks.append("show_loading")
        original_show_loading(self, name)

    monkeypatch.setattr(panel_module.MetadataPanel, "set_report", traced_set_report)
    monkeypatch.setattr(panel_module.MetadataPanel, "show_loading", traced_show_loading)
    return marks


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path, make_photo: object) -> MainWindow:
    root = tmp_path / "库"
    for index in range(6):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)  # type: ignore[operator]
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1200, 800)
    window.show()

    def settle(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.005)

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline and window._model.rowCount() < 6:
        settle(0.05)
    window._open_detail(window._model.index(0, 0))
    settle(0.6)
    yield window  # type: ignore[misc]
    window.close()


def _settle(qt_app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)


def test_first_open_may_show_loading_hint(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """用户主动展开面板、面板本来是空的时候，允许一次加载提示。"""
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)

    assert "show_loading" in timeline, "面板为空时应当有等待提示"
    assert "set_report" in timeline


def test_switching_photos_never_shows_loading_hint(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """来回翻照片时不得再出现「正在读取元数据…」。"""
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)  # 先把首张读进来并预读邻居
    timeline.clear()

    for _ in range(5):
        window.step_detail(1)
        _settle(qt_app, 0.03)  # 远小于一次读取（约 50ms），模拟快速翻图

    assert "show_loading" not in timeline, f"切图时又闪出了加载提示：{timeline}"


def test_switching_keeps_panel_content_until_new_data_arrives(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """缓存未命中时也不能先清空：保留原有内容，等新结果一次换上去。"""
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)
    window._metadata_cache.clear()  # 模拟「没预读到」
    timeline.clear()

    window._open_detail(window._model.index(3, 0))
    _settle(qt_app, 0.02)  # 切换瞬间

    assert "show_loading" not in timeline
    assert timeline == [], "切换瞬间既不该清空也不该改内容"
    _settle(qt_app, 1.0)
    assert "set_report" in timeline, "新数据到位后应当显示出来"
    ref = window._model.photo_at(window._current_row)
    report = window._panel.report
    assert any(field.value == ref.name for group in report.groups for field in group.fields)


def test_metadata_is_not_read_while_the_panel_is_closed(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """面板收起时不做任何读取，也不该有面板状态变化。"""
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)
    window._metadata_action.setChecked(False)
    timeline.clear()
    cached = len(window._metadata_cache)

    window.step_detail(1)
    _settle(qt_app, 0.3)

    assert timeline == []
    assert len(window._metadata_cache) == cached, "面板关闭时不该读取元数据"


def test_place_lookup_result_is_cached(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """推断出的地点要入缓存，否则切回这张照片会「先没地点、再被替换」闪一次。"""
    from gallery.core.metadata import MetadataReport, build_report

    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)
    ref = window._model.photo_at(window._current_row)
    base = build_report({}, path=ref.path, size=ref.size, mtime_ns=ref.mtime_ns, source="exiftool")

    window._place_index_saved(base, window._current_row, window._generation, "法国 · 波尔多")

    report = window._panel.report
    assert any(
        field.label == "地点（推断）" and "波尔多" in field.value
        for group in report.groups
        for field in group.fields
    )
    cached = window._cached_metadata(ref)
    assert isinstance(cached, MetadataReport)
    assert any(field.label == "地点（推断）" for group in cached.groups for field in group.fields)


def test_switching_photos_shows_no_loading_overlay(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """切图时右上角不再出现「正在载入」角标。

    角标只用来在加载失败时说明原因了：切图时多一个一闪而过的提示比多等 50ms 更扎眼。
    """
    detail = window._detail
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)

    for _ in range(3):
        window.step_detail(1)
        _settle(qt_app, 0.03)

    assert not detail._overlay.isVisible(), "切图时不该出现任何角标"
    assert "正在载入" not in detail._overlay_text


def test_failed_photo_still_explains_why(window: MainWindow, qt_app: QApplication) -> None:
    """删掉载入角标不能连错误提示一起删掉：加载失败仍要说原因。"""
    detail = window._detail
    detail.show_error("这个文件解码不了")

    assert detail._overlay.isVisible()
    assert detail._overlay.text() == "这个文件解码不了"

    detail.prepare_switch()
    assert not detail._overlay.isVisible(), "切图时上一张的错误提示应当撤掉"


def test_cached_photo_still_gets_its_place(
    window: MainWindow,
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """命中缓存也必须推断地点 —— 预读只读元数据，缓存里本来没有地点。

    这是真实缺陷：命中缓存的分支直接 return，漏掉了地点反查，于是凡是落在预读
    窗口（前后各 2 张）内的照片，带坐标却不显示推断地点。
    """

    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)
    ref = window._model.photo_at(window._current_row)

    # 造一份带坐标的元数据，并用一个假索引替代真实城市库（测试不联网）。
    report = build_report(
        {
            "EXIF:GPSLatitude": "44.83780",
            "EXIF:GPSLatitudeRef": "North",
            "EXIF:GPSLongitude": "0.57920",
            "EXIF:GPSLongitudeRef": "West",
        },
        path=ref.path,
        size=ref.size,
        mtime_ns=ref.mtime_ns,
        source="exiftool",
    )
    assert report.latitude is not None
    window._cache_metadata(ref, report)  # 预读缓存里就是这种「还没有地点」的报告

    looked_up: list[tuple[float, float]] = []

    class FakeIndex:
        def nearest(self, latitude: float, longitude: float) -> object:
            looked_up.append((latitude, longitude))
            return None

    monkeypatch.setattr(window, "_place_index", FakeIndex())
    monkeypatch.setattr(type(window), "_place_index_saved", lambda *args, **kwargs: None)

    window._open_detail(window._model.index(0, 0))
    _settle(qt_app, 0.5)

    assert looked_up == [(pytest.approx(44.8378), pytest.approx(-0.5792))], (
        "命中缓存时也应当按坐标推断地点"
    )
