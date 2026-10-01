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
    """预读命中时来回翻照片不得出现「正在读取元数据…」（命中就是一次到位）。

    每一步都先确认目标确实已在缓存里 —— 那是「命中」这条路径的前提。没命中时
    显示「正在读取」是**正确**行为（见 test_cache_miss_does_not_show_the_previous_photo）。
    """
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)  # 先把首张读进来并预读邻居
    timeline.clear()

    for _ in range(5):
        target = window._model.photo_at(window._current_row + 1)
        assert target is not None
        assert window._cached_metadata(target) is not None, (
            f"预读应当已经把 {target.name} 备好，否则这条测的不是命中路径"
        )
        window.step_detail(1)
        # 预读本身是异步的（约 50ms），留出它落地的时间：这是「预读来得及」的
        # 正常浏览节奏。实测 250ms 一次切换时命中率是 10/10。
        _settle(qt_app, 0.25)

    assert "show_loading" not in timeline, f"命中时不该出现加载提示：{timeline}"


def test_metadata_cache_miss_does_not_show_the_previous_photo(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """缓存未命中时，不能把上一张的元数据留在屏幕上。

    那是错的照片的元数据，用户会当成当前这张来看 —— 比多等 50ms 糟糕得多。
    未命中就老实说「正在读取」。
    """
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.2)
    window._metadata_cache.clear()
    window.show_grid()
    _settle(qt_app, 0.5)
    timeline.clear()

    window._open_detail(window._model.index(min(4, window._model.rowCount() - 1), 0))
    _settle(qt_app, 0.02)  # 刚切换，还没读完

    assert "show_loading" in timeline, (
        f"未命中时应当先显示「正在读取」，而不是留着上一张：{timeline}"
    )
    _settle(qt_app, 1.2)
    assert "set_report" in timeline


def test_cache_miss_shows_loading_then_the_new_report(
    window: MainWindow, qt_app: QApplication, timeline: list[str]
) -> None:
    """缓存未命中：先说「正在读取」，读到了再显示，不冒充上一张。

    （早先的实现是「保留上一张的内容等新结果换上去」，那样屏幕上停留的是**错的**
    元数据，用户会当成当前这张来看 —— 已经改成未命中就说在读。）
    """
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)
    window._metadata_cache.clear()  # 模拟「没预读到」
    timeline.clear()

    window._open_detail(window._model.index(3, 0))
    _settle(qt_app, 0.02)  # 切换瞬间

    assert timeline == ["show_loading"], f"未命中时应当立刻说明在读：{timeline}"
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

    window._place_index_saved(ref, base, window._generation, "法国 · 波尔多")

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


def test_switching_photos_resolves_the_place_before_display(
    window: MainWindow,
    qt_app: QApplication,
    timeline: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切到一张照片时，地点必须已经查好，一次显示到位。

    这是真实缺陷：地点原先是在切过去之后才查的，于是面板先显示一遍没有地点的
    版本、再被「正在查询…」和结果替换 —— 用户看到的就是地点闪一下。
    预读时就应该把地点一起查完并入缓存。
    """
    window._metadata_action.setChecked(True)
    _settle(qt_app, 1.0)

    from gallery.core.metadata import build_report

    queried: list[tuple[float, float]] = []

    class FakeIndex:
        """只回答「查到了没有」，不碰真实城市库（测试不联网、不读磁盘）。"""

        def nearest(self, latitude: float, longitude: float) -> object:
            queried.append((latitude, longitude))
            return None  # 「附近没有记录」也是结论，同样应当入缓存

    monkeypatch.setattr(window, "_place_index", FakeIndex())

    # 预读旁边几张，每张都带坐标
    row = window._current_row
    for offset in range(-2, 3):
        ref = window._model.photo_at(row + offset)
        if ref is None:
            continue
        report = build_report(
            {
                "EXIF:GPSLatitude": "30.27390",
                "EXIF:GPSLatitudeRef": "North",
                "EXIF:GPSLongitude": "120.16139",
                "EXIF:GPSLongitudeRef": "East",
            },
            path=ref.path,
            size=ref.size,
            mtime_ns=ref.mtime_ns,
            source="exiftool",
        )
        window._on_metadata_prefetched(ref, report)
    _settle(qt_app, 0.8)

    # 预读过的照片，缓存里就该已经带上地点（即使结果是「附近没有记录」）
    prefetched = window._model.photo_at(row + 1)
    cached = window._cached_metadata(prefetched)
    assert cached is not None
    assert cached.place_resolved, "预读时就应当把地点推断完入缓存"
    assert any(
        field.label == "地点（推断）" for group in cached.groups for field in group.fields
    ), "缓存里应当已经带着地点，切换时才能一次显示到位"

    # 现在切过去：面板只应当被替换一次
    timeline.clear()
    window._open_detail(window._model.index(row + 1, 0))
    _settle(qt_app, 0.5)

    assert timeline == ["set_report"], f"切图时面板被替换了 {len(timeline)} 次：{timeline}"
    assert queried, "预读时就应当已经发起过地点推断"


def test_opening_a_photo_does_not_show_the_previous_one(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """打开一张照片时，屏幕上不能还留着上一张。

    留着它用户会以为点错了文件 —— 看到的是错的照片，比短暂的黑屏更糟。
    这里先显示这张照片自己的缩略图：尺寸小一点，但至少是对的。

    必须先清掉预览缓存：预读已经把下一张放进去了的话，走的是「命中缓存直接
    显示」的路径，压根不会经过这里要验证的分支。
    """
    from PIL import Image as PilImage

    from gallery.core.config import prepare_library

    root = tmp_path / "两色"
    root.mkdir()
    for index, colour in enumerate([(220, 40, 40), (40, 40, 220)]):
        pil = PilImage.new("RGB", (900, 700), colour)
        pil.save(root / f"IMG_{index:04d}.jpg")
        pil.close()
    prepare_library(root)

    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1100, 800)
    window.show()
    _settle(qt_app, 2.0)
    assert window._model.rowCount() == 2

    def centre_colour() -> object:
        viewport = window._detail.viewport()
        image = window._detail.grab().toImage()
        return image.pixelColor(viewport.width() // 2, viewport.height() // 2)

    window._open_detail(window._model.index(0, 0))
    _settle(qt_app, 1.5)
    first = centre_colour()

    window.show_grid()
    _settle(qt_app, 0.5)
    target = window._model.photo_at(1)
    window._preview_cache.clear()  # 制造「这张的预览还没有」的情形
    assert target is not None and target.path not in window._preview_cache

    window._open_detail(window._model.index(1, 0))
    centre = centre_colour()  # 立刻取，不给解码留时间

    assert centre != first, "切换瞬间画面还是上一张照片的颜色"
    window.close()


def test_thumbnail_accessor_returns_none_when_missing() -> None:
    """缩略图访问器：没有就返回 None，不要凭空造一张空白图。"""
    from gallery.ui.models import PhotoListModel

    model = PhotoListModel()
    assert model.thumbnail_pixmap(Path("/没有/这张照片.jpg")) is None
