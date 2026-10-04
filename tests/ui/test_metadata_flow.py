"""元数据面板在**切换照片**时不得清空重画。

切图闪烁是真实缺陷：先前每次切图都先 ``show_loading`` 把面板清空成「正在读取元数据…」，
读完了再换成内容 —— 一闪一闪的，观感很差。现在切图不改面板：命中缓存直接同步显示，
未命中也保留原有内容等新结果换上去；加载提示只在面板本来就是空的时候出现。

这里不比对界面像素，而是记录面板每次 ``set_report`` / ``show_loading`` 的时序，
断言切换过程中没有加载提示。

等待分两种，别混：确认「某事发生了」一律等**条件成立**（实测面板出内容约 56ms、
预读填满约 107ms，原来无脑等满 1 秒，十倍空转）；确认「某事没发生」才等**固定
时长** —— 那种时长本身就是断言的一部分，不能提前收工。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.metadata.exif import build_report  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.core.places.resolver import ResolvedPlace  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels import metadata as panel_module  # noqa: E402
from gallery.ui.window.viewer_flow import METADATA_PREFETCH_RADIUS  # noqa: E402  （同上）

#: 「元数据已读进缓存」的判定：当前这张加上前后预读窗口都填满。
PREFETCHED_ENTRIES = METADATA_PREFETCH_RADIUS + 1


class FakeResolver:
    """假反查器：记下被问过的坐标，一律回答「附近没有记录」。

    只实现 ``core.places.resolver.PlaceResolver`` 的形状（``place_for`` + ``ready``），
    因此测试不联网、也不读磁盘上的城市库。回答「没有记录」而不是某个城市，是刻意的：
    「查了但查不到」与「压根没查」的区别正是这一版要守住的东西。
    """

    def __init__(self) -> None:
        self.queried: list[tuple[float, float]] = []

    @property
    def ready(self) -> bool:
        return True

    def place_for(
        self, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace | None:
        self.queried.append((latitude, longitude))
        return None


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
def window(
    qt_app: QApplication,
    wait_for: Callable[..., bool],
    tmp_path: Path,
    make_photo: object,
) -> MainWindow:
    root = tmp_path / "库"
    for index in range(6):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)  # type: ignore[operator]
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1200, 800)
    window.show()

    assert wait_for(lambda: window._model.rowCount() >= 6, timeout=DEFAULT_TIMEOUT_SECONDS), (
        "照片没能在时限内扫出来，后面几条测的都不是这条路径"
    )
    window._open_detail(window._model.index(0, 0))
    # 等首张真的解码上屏，而不是干等 0.6 秒 —— 这条 fixture 每条测试都要跑一遍。
    wait_for(lambda: window._detail.has_image, timeout=DEFAULT_TIMEOUT_SECONDS)
    yield window  # type: ignore[misc]
    window.close()


@pytest.fixture
def ready(window: MainWindow, wait_for: Callable[..., bool]) -> Callable[[], None]:
    """展开元数据面板，等首张显示且前后预读窗口填满。

    这是绝大部分测试的起点：先让缓存备好，后面「命中/未命中」的分支才是被测的那个。
    """

    def _ready() -> None:
        assert wait_for(
            lambda: len(window._metadata_cache) >= PREFETCHED_ENTRIES,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ), "元数据没能在时限内读完，预读窗口也没填满"

    return _ready


def _next_prefetched(window: MainWindow) -> bool:
    """当前这张的**下一张**是否已经进了缓存。

    翻到最后一张时压根没有下一张，那就算「已经就绪」—— 否则会一路等到超时。
    """
    following = window._model.photo_at(window._current_row + 1)
    return following is None or window._cached_metadata(following) is not None


def test_first_open_may_show_loading_hint(
    window: MainWindow,
    wait_for: Callable[..., bool],
    timeline: list[str],
    ready: Callable[[], None],
) -> None:
    """用户主动展开面板、面板本来是空的时候，允许一次加载提示。"""
    window._metadata_action.setChecked(True)
    assert wait_for(lambda: "set_report" in timeline, timeout=DEFAULT_TIMEOUT_SECONDS), (
        f"面板应当被换成真实内容：{timeline}"
    )

    assert "show_loading" in timeline, "面板为空时应当有等待提示"


def test_switching_photos_never_shows_loading_hint(
    window: MainWindow,
    wait_for: Callable[..., bool],
    timeline: list[str],
    ready: Callable[[], None],
) -> None:
    """预读命中时来回翻照片不得出现「正在读取元数据…」（命中就是一次到位）。

    每一步都先确认目标确实已在缓存里 —— 那是「命中」这条路径的前提。没命中时
    显示「正在读取」是**正确**行为（见 test_cache_miss_does_not_show_the_previous_photo）。
    """
    window._metadata_action.setChecked(True)
    ready()  # 先把首张读进来并预读邻居
    timeline.clear()

    for _ in range(5):
        target = window._model.photo_at(window._current_row + 1)
        assert target is not None
        assert window._cached_metadata(target) is not None, (
            f"预读应当已经把 {target.name} 备好，否则这条测的不是命中路径"
        )
        window.step_detail(1)
        # 预读是异步的（约 50ms）。等「下一张确实进了缓存」而不是干等固定时长：
        # 这样「切换期间没有加载提示」这条断言覆盖的是**整段真实过程**，
        # 比等满 250ms 更长、更贴近实际浏览节奏。
        wait_for(lambda: _next_prefetched(window), timeout=DEFAULT_TIMEOUT_SECONDS)

    assert "show_loading" not in timeline, f"命中时不该出现加载提示：{timeline}"


def test_metadata_cache_miss_does_not_show_the_previous_photo(
    window: MainWindow,
    settle: Callable[[float], None],
    wait_for: Callable[..., bool],
    timeline: list[str],
    ready: Callable[[], None],
) -> None:
    """缓存未命中时，不能把上一张的元数据留在屏幕上。

    那是错的照片的元数据，用户会当成当前这张来看 —— 比多等 50ms 糟糕得多。
    未命中就老实说「正在读取」。
    """
    window._metadata_action.setChecked(True)
    ready()
    window._metadata_cache.clear()
    window.show_grid()
    settle(0.1)
    timeline.clear()

    window._open_detail(window._model.index(min(4, window._model.rowCount() - 1), 0))
    settle(0.02)  # 刚切换，还没读完

    assert "show_loading" in timeline, (
        f"未命中时应当先显示「正在读取」，而不是留着上一张：{timeline}"
    )
    assert wait_for(lambda: "set_report" in timeline, timeout=DEFAULT_TIMEOUT_SECONDS), (
        f"读完了应当换成新照片的内容：{timeline}"
    )


def test_cache_miss_shows_loading_then_the_new_report(
    window: MainWindow,
    settle: Callable[[float], None],
    wait_for: Callable[..., bool],
    timeline: list[str],
    ready: Callable[[], None],
) -> None:
    """缓存未命中：先说「正在读取」，读到了再显示，不冒充上一张。

    （早先的实现是「保留上一张的内容等新结果换上去」，那样屏幕上停留的是**错的**
    元数据，用户会当成当前这张来看 —— 已经改成未命中就说在读。）
    """
    window._metadata_action.setChecked(True)
    ready()
    window._metadata_cache.clear()  # 模拟「没预读到」
    timeline.clear()

    window._open_detail(window._model.index(3, 0))
    settle(0.02)  # 切换瞬间

    assert timeline == ["show_loading"], f"未命中时应当立刻说明在读：{timeline}"
    assert wait_for(lambda: "set_report" in timeline, timeout=DEFAULT_TIMEOUT_SECONDS), (
        f"新数据到位后应当显示出来：{timeline}"
    )
    ref = window._model.photo_at(window._current_row)
    report = window._panel.report
    assert any(field.value == ref.name for group in report.groups for field in group.fields)


def test_metadata_is_not_read_while_the_panel_is_closed(
    window: MainWindow,
    settle: Callable[[float], None],
    timeline: list[str],
    ready: Callable[[], None],
) -> None:
    """面板收起时不做任何读取，也不该有面板状态变化。"""
    window._metadata_action.setChecked(True)
    ready()
    window._metadata_action.setChecked(False)
    timeline.clear()
    cached = len(window._metadata_cache)

    window.step_detail(1)
    settle(0.3)  # 这里断言的是「没有发生」，等多久本身就是断言的一部分

    assert timeline == []
    assert len(window._metadata_cache) == cached, "面板关闭时不该读取元数据"


def test_place_lookup_result_is_cached(
    window: MainWindow, timeline: list[str], ready: Callable[[], None]
) -> None:
    """推断出的地点要入缓存，否则切回这张照片会「先没地点、再被替换」闪一次。"""
    from gallery.core.metadata.exif import MetadataReport

    window._metadata_action.setChecked(True)
    ready()
    ref = window._model.photo_at(window._current_row)
    base = build_report({}, path=ref.path, size=ref.size, mtime_ns=ref.mtime_ns, source="exiftool")

    window._show_place(ref, base, window._generation, "法国 · 波尔多")

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
    window: MainWindow,
    settle: Callable[[float], None],
    ready: Callable[[], None],
) -> None:
    """切图时右上角不再出现「正在载入」角标。

    角标只用来在加载失败时说明原因了：切图时多一个一闪而过的提示比多等 50ms 更扎眼。
    """
    detail = window._detail
    window._metadata_action.setChecked(True)
    ready()

    for _ in range(3):
        window.step_detail(1)
        settle(0.03)  # 断言的是「切换瞬间没有角标」，等多久本身是被测的一部分

    assert not detail._overlay.isVisible(), "切图时不该出现任何角标"
    assert "正在载入" not in detail._overlay_text


def test_failed_photo_still_explains_why(window: MainWindow) -> None:
    """删掉载入角标不能连错误提示一起删掉：加载失败仍要说原因。"""
    detail = window._detail
    detail.show_error("这个文件解码不了")

    assert detail._overlay.isVisible()
    assert detail._overlay.text() == "这个文件解码不了"

    detail.prepare_switch()
    assert not detail._overlay.isVisible(), "切图时上一张的错误提示应当撤掉"


def test_the_panel_reads_the_place_out_of_the_index(
    window: MainWindow,
    wait_for: Callable[..., bool],
    monkeypatch: pytest.MonkeyPatch,
    ready: Callable[[], None],
) -> None:
    """索引里已经有结论时，面板**直接读它**，一次反查都不发起（ADR 0010）。

    打开库时那一遍已经算过并存进索引了，面板再算一次就是同一张照片两套说法。
    """
    window._metadata_action.setChecked(True)
    ready()
    ref = window._model.photo_at(window._current_row)
    window._library.facet_index.store(
        [
            (
                ref,
                PhotoFacets(
                    city="杭州",
                    region="浙江省",
                    country="中国",
                    latitude=30.27,
                    longitude=120.16,
                    place_checked=True,
                    place_inferred=True,
                    place_distance_km=120.0,
                ),
            )
        ]
    )
    window._cache_metadata(
        ref,
        build_report(
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
        ),
    )
    resolver = FakeResolver()
    monkeypatch.setattr(window, "_place_lookup", lambda: resolver)

    window._open_detail(window._model.index(0, 0))
    wait_for(
        lambda: any(
            field.label == "地点（推断）"
            for group in (window._panel.report.groups if window._panel.report else ())
            for field in group.fields
        ),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    assert resolver.queried == [], "索引里已经有结论，不该再发起反查"
    values = [
        field.value
        for group in (window._panel.report.groups if window._panel.report else ())
        for field in group.fields
        if field.label == "地点（推断）"
    ]
    # 与分组同一份结论、同一句话：``中国 · 浙江省 · 杭州``，偏得远时补一句距离。
    assert values == ["中国 · 浙江省 · 杭州（最近城市约 120 公里外）"]


def test_cached_photo_still_gets_its_place(
    window: MainWindow,
    wait_for: Callable[..., bool],
    monkeypatch: pytest.MonkeyPatch,
    ready: Callable[[], None],
    tmp_path: Path,
) -> None:
    """命中缓存也必须推断地点 —— 预读只读元数据，缓存里本来没有地点。

    这是真实缺陷：命中缓存的分支直接 return，漏掉了地点反查，于是凡是落在预读
    窗口（前后各 2 张）内的照片，带坐标却不显示推断地点。
    """
    window._metadata_action.setChecked(True)
    ready()
    ref = window._model.photo_at(window._current_row)

    # 造一份带坐标的元数据，并用一个假反查器替代真实城市库（测试不联网）。
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

    resolver = FakeResolver()
    monkeypatch.setattr(window, "_place_lookup", lambda: resolver)

    window._open_detail(window._model.index(0, 0))
    # 等「地点反查真的发生过」而不是干等：这是条正向断言，有明确条件可等。
    # 万一超时，下面那条断言会因为 queried 为空而报出来。
    wait_for(lambda: bool(resolver.queried), timeout=DEFAULT_TIMEOUT_SECONDS)

    assert resolver.queried == [(pytest.approx(44.8378), pytest.approx(-0.5792))], (
        "命中缓存时也应当按坐标推断地点"
    )


def test_switching_photos_resolves_the_place_before_display(
    window: MainWindow,
    wait_for: Callable[..., bool],
    timeline: list[str],
    monkeypatch: pytest.MonkeyPatch,
    ready: Callable[[], None],
) -> None:
    """切到一张照片时，地点必须已经查好，一次显示到位。

    这是真实缺陷：地点原先是在切过去之后才查的，于是面板先显示一遍没有地点的
    版本、再被「正在查询…」和结果替换 —— 用户看到的就是地点闪一下。
    预读时就应该把地点一起查完并入缓存。
    """
    window._metadata_action.setChecked(True)
    ready()

    resolver = FakeResolver()
    monkeypatch.setattr(window, "_place_lookup", lambda: resolver)

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

    # 预读过的照片，缓存里就该已经带上地点（即使结果是「附近没有记录」）
    prefetched = window._model.photo_at(row + 1)
    wait_for(
        lambda: getattr(window._cached_metadata(prefetched), "place_resolved", False),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    cached = window._cached_metadata(prefetched)
    assert cached is not None
    assert cached.place_resolved, "预读时就应当把地点推断完入缓存"
    assert any(
        field.label == "地点（推断）" for group in cached.groups for field in group.fields
    ), "缓存里应当已经带着地点，切换时才能一次显示到位"

    # 现在切过去：面板只应当被替换一次
    timeline.clear()
    window._open_detail(window._model.index(row + 1, 0))
    wait_for(lambda: bool(timeline), timeout=DEFAULT_TIMEOUT_SECONDS)

    assert timeline == ["set_report"], f"切图时面板被替换了 {len(timeline)} 次：{timeline}"
    assert resolver.queried, "预读时就应当已经发起过地点推断"


def test_opening_a_photo_does_not_show_the_previous_one(
    qt_app: QApplication,
    settle: Callable[[float], None],
    wait_for: Callable[..., bool],
    tmp_path: Path,
) -> None:
    """打开一张照片时，屏幕上不能还留着上一张。

    留着它用户会以为点错了文件 —— 看到的是错的照片，比短暂的黑屏更糟。
    这里先显示这张照片自己的缩略图：尺寸小一点，但至少是对的。

    必须先清掉预览缓存：预读已经把下一张放进去了的话，走的是「命中缓存直接
    显示」的路径，压根不会经过这里要验证的分支。
    """
    from PIL import Image as PilImage

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
    assert wait_for(lambda: window._model.rowCount() == 2, timeout=DEFAULT_TIMEOUT_SECONDS), (
        "两张照片没能在时限内扫出来"
    )

    def centre_colour() -> object:
        viewport = window._detail.viewport()
        image = window._detail.grab().toImage()
        return image.pixelColor(viewport.width() // 2, viewport.height() // 2)

    window._open_detail(window._model.index(0, 0))
    # 等第一张真的解码上屏再取色，否则取到的是还没画出来的空画面。
    wait_for(lambda: window._detail.has_image, timeout=DEFAULT_TIMEOUT_SECONDS)
    settle(0.1)  # 让最后一帧真的刷到 viewport 上
    first = centre_colour()

    window.show_grid()
    settle(0.1)
    target = window._model.photo_at(1)
    window._preview_cache.clear()  # 制造「这张的预览还没有」的情形
    assert target is not None and target.path not in window._preview_cache

    window._open_detail(window._model.index(1, 0))
    centre = centre_colour()  # 立刻取，不给解码留时间

    assert centre != first, "切换瞬间画面还是上一张照片的颜色"
    window.close()


def test_thumbnail_accessor_returns_none_when_missing() -> None:
    """缩略图访问器：没有就返回 None，不要凭空造一张空白图。"""
    from gallery.ui.browser.model import PhotoListModel

    model = PhotoListModel()
    assert model.thumbnail_pixmap(Path("/没有/这张照片.jpg")) is None


def test_the_panel_keeps_switching_photos_with_the_arrow_keys(
    window: MainWindow,
    wait_for: Callable[..., bool],
    ready: Callable[[], None],
) -> None:
    """焦点停在面板里，← / → 仍然换照片 —— 大图页上。

    这正是用户按「复制全部」之后会遇到的事：焦点落在面板的按钮上，方向键本来就被它吃掉，
    什么也没发生，而用户还看着同一张照片的元数据。
    """
    window._metadata_action.setChecked(True)
    ready()
    first = window._model.photo_at(window._current_row)
    assert first is not None

    window._panel._copy_button.setFocus()
    QApplication.processEvents()
    QTest.keyClick(window._panel._copy_button, Qt.Key.Key_Right)
    wait_for(lambda: window._current_row == 1, timeout=DEFAULT_TIMEOUT_SECONDS)
    assert window._model.photo_at(window._current_row) != first

    QTest.keyClick(window._panel._copy_button, Qt.Key.Key_Left)
    wait_for(lambda: window._current_row == 0, timeout=DEFAULT_TIMEOUT_SECONDS)
    assert window._model.photo_at(window._current_row) == first


def test_the_focus_stays_in_the_panel_after_stepping(
    window: MainWindow, ready: Callable[[], None]
) -> None:
    """**焦点不跟着走** —— 用户还停在面板里，接着按 ← / → 能继续翻。"""
    window._metadata_action.setChecked(True)
    ready()
    window._panel._copy_button.setFocus()
    QApplication.processEvents()

    QTest.keyClick(window._panel._copy_button, Qt.Key.Key_Right)

    assert QApplication.focusWidget() is window._panel._copy_button
