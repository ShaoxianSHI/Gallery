"""``Library.photo_facets`` 的地点反查。

相机原图几乎不写 IPTC / XMP 地点，只写 GPS —— 所以「按国家 / 省州 / 城市分组」
能不能用，全看这一层有没有把坐标反查成地点。这里守住四条：

- 有反查器就用它补齐地点；
- **文件里写了地点就以文件为准**；
- 没给反查器时不许替用户联网查东西（那三栏显示成「未知」，是诚实的）；
- 结论进索引，第二次不再反查；旧行（有坐标、没地点）会被顺手补上。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from gallery.core.library.facade import Library
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata import batch
from gallery.core.places.resolver import ResolvedPlace

GPS = {
    "EXIF:GPSLatitude": "34 deg 12' 54.34\"",
    "EXIF:GPSLatitudeRef": "North",
    "EXIF:GPSLongitude": "108 deg 57' 52.44\"",
    "EXIF:GPSLongitudeRef": "East",
}
PARIS_GPS = {
    "EXIF:GPSLatitude": "48 deg 48' 17.51\"",
    "EXIF:GPSLatitudeRef": "North",
    "EXIF:GPSLongitude": "2 deg 7' 13.28\"",
    "EXIF:GPSLongitudeRef": "East",
}


class FakeResolver:
    """按「西安」/「巴黎」两处坐标给出固定答案，并数着被问了几次。"""

    def __init__(self, answer: ResolvedPlace | None = None) -> None:
        self.answer = answer or ResolvedPlace(city="西安", region="陕西省", country="中国")
        self.calls = 0

    def place_for(self, latitude: float, longitude: float, *, status: object = None):
        self.calls += 1
        if latitude > 40:  # 巴黎
            return ResolvedPlace(city="巴黎", region="法兰西岛", country="法国")
        return self.answer


@pytest.fixture
def gps_library(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> Library:
    """一个库：两张带坐标的照片 + 一张没坐标的；元数据读取走假来源。"""
    raw = {"西安.jpg": GPS, "巴黎.jpg": PARIS_GPS, "无坐标.jpg": {}}

    def fake_read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            yield Path(path), raw.get(Path(path).name)

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", fake_read_many)
    for name in raw:
        make_photo(name)
    return Library.open(tmp_path)


def _refs(library: Library) -> list[PhotoRef]:
    return [PhotoRef.from_path(path) for path in sorted(library.root.glob("*.jpg"))]


def test_place_is_resolved_from_coordinates(gps_library: Library) -> None:
    resolver = FakeResolver()

    facets = gps_library.photo_facets(_refs(gps_library), resolver=resolver)

    assert resolver.calls == 2  # 没坐标的那张不必问
    assert facets[gps_library.root / "西安.jpg"].country == "中国"
    assert facets[gps_library.root / "巴黎.jpg"].country == "法国"
    assert facets[gps_library.root / "无坐标.jpg"].country is None


def test_resolved_place_is_remembered_in_the_index(gps_library: Library) -> None:
    resolver = FakeResolver()
    refs = _refs(gps_library)
    gps_library.photo_facets(refs, resolver=resolver)

    fresh = FakeResolver()
    again = gps_library.photo_facets(refs, resolver=fresh)

    assert fresh.calls == 0  # 全部命中索引，一次反查都不必
    assert again[gps_library.root / "西安.jpg"].city == "西安"


def test_without_a_resolver_the_place_is_left_unknown(gps_library: Library) -> None:
    """没给反查器就不许替用户联网查东西 —— 那三栏显示「未知」是诚实的。"""
    facets = gps_library.photo_facets(_refs(gps_library))

    assert facets[gps_library.root / "西安.jpg"].latitude == pytest.approx(34.215, abs=0.001)
    assert facets[gps_library.root / "西安.jpg"].country is None
    assert facets[gps_library.root / "西安.jpg"].place_inferred is False


def test_place_written_in_the_file_is_not_overwritten(
    gps_library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件里写了地点就以文件为准（Lightroom 导出的比反查准）。"""
    original = batch.ExifToolBatchSource.read_many

    def with_file_place(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path, raw in original(self, paths, should_cancel=should_cancel):  # type: ignore[arg-type]
            if raw is not None and Path(path).name == "西安.jpg":
                raw = {**raw, "IPTC:City": "Xi'an"}
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", with_file_place)
    library = Library.open(gps_library.root)

    facets = library.photo_facets(_refs(library), resolver=FakeResolver())

    assert facets[library.root / "西安.jpg"].city == "Xi'an"
    assert facets[library.root / "西安.jpg"].country == "中国"  # 缺的照补


def test_old_rows_are_filled_in_later(gps_library: Library) -> None:
    """第一次分组时城市数据还没下好 → 索引里留下「有坐标没地点」的行；
    等数据到位后，**不必再读一次 EXIF** 就该补上。"""
    refs = _refs(gps_library)
    gps_library.photo_facets(refs)  # 不给反查器
    assert gps_library.facet_index.lookup(refs)[gps_library.root / "西安.jpg"].city is None

    resolver = FakeResolver()
    filled = gps_library.photo_facets(refs, resolver=resolver)

    assert resolver.calls == 2
    assert filled[gps_library.root / "西安.jpg"].city == "西安"
    assert gps_library.facet_index.lookup(refs)[gps_library.root / "西安.jpg"].city == "西安"


def test_a_photo_without_coordinates_is_never_asked(gps_library: Library) -> None:
    resolver = FakeResolver()

    gps_library.photo_facets(
        [PhotoRef.from_path(gps_library.root / "无坐标.jpg")], resolver=resolver
    )

    assert resolver.calls == 0


def test_cancellation_stops_the_second_pass(gps_library: Library) -> None:
    """补旧行时也要能取消：点进别处之后不该还在后台反查。"""
    refs = _refs(gps_library)
    gps_library.photo_facets(refs)

    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    resolver = FakeResolver()
    gps_library.photo_facets(refs, resolver=resolver, should_cancel=should_cancel)

    assert resolver.calls <= 2
