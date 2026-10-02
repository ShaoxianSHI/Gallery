"""``Library.resolve_places``：打开库时把整库的地点算出来存进索引（ADR 0010）。

要守住的是四件事：

- **跑一遍之后，分组与元数据面板读的是同一份结论**（都从索引来，不再各算一次）；
- **幂等**：已经有结论的行连 EXIF 都不该再读第二次；
- **「查不到」也是结论**：附近没有城市的照片不会被反复重算；
- **没查成不算结论**：城市数据没下好时那次不记，下次还得能再来一遍。
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
HANGZHOU_GPS = {
    "EXIF:GPSLatitude": "30 deg 16' 20.0\"",
    "EXIF:GPSLatitudeRef": "North",
    "EXIF:GPSLongitude": "120 deg 9' 41.0\"",
    "EXIF:GPSLongitudeRef": "East",
}

#: 库里的照片，按文件名给坐标（文件名是假来源认人的唯一依据）。
PHOTOS: dict[str, dict[str, str]] = {
    "西安.jpg": GPS,
    "巴黎.jpg": PARIS_GPS,
    "无坐标.jpg": {},
    "杭州.jpg": HANGZHOU_GPS,
}

#: 放在子文件夹里的那一张 —— 整库解析必须递归下去。
IN_ALBUM = "杭州.jpg"
ALBUM = "相册"


class FakeResolver:
    """按坐标给固定答案，数着问了几次。

    ``ready=False`` 模拟「城市数据没下好」，``found=False`` 模拟「附近没有城市」
    —— 这两种 ``None`` 含义完全不同，正是这一版要分开的东西。
    """

    def __init__(self, *, ready: bool = True, found: bool = True) -> None:
        self._ready = ready
        self._found = found
        self.queried: list[tuple[float, float]] = []

    @property
    def ready(self) -> bool:
        return self._ready

    def place_for(
        self, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace | None:
        self.queried.append((latitude, longitude))
        if not self._ready or not self._found:
            return None
        if latitude > 40:  # 巴黎
            return ResolvedPlace(city="巴黎", region="法兰西岛", country="法国", distance_km=3.0)
        if latitude < 32:  # 杭州
            return ResolvedPlace(city="杭州", region="浙江省", country="中国", distance_km=1.0)
        return ResolvedPlace(city="西安", region="陕西省", country="中国", distance_km=2.0)


@pytest.fixture
def library_with_gps(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> Library:
    """一个库：三张带坐标（含子文件夹里的一张）+ 一张没坐标；读取走假来源。"""

    def fake_read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            yield Path(path), PHOTOS.get(Path(path).name)

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", fake_read_many)
    for name in PHOTOS:
        make_photo(name, directory=(tmp_path / ALBUM) if name == IN_ALBUM else None)
    return Library.open(tmp_path)


def _refs(library: Library) -> list[PhotoRef]:
    return [PhotoRef.from_path(path) for path in sorted(library.root.rglob("*.jpg"))]


def test_the_whole_library_is_covered_including_subfolders(
    library_with_gps: Library,
) -> None:
    """子文件夹里的照片也要算到 —— 否则按城市分组会漏掉一整个相册。"""
    library_with_gps.resolve_places(resolver=FakeResolver())

    refs = _refs(library_with_gps)
    facets = library_with_gps.cached_facets(refs)
    assert facets[library_with_gps.root / "西安.jpg"].country == "中国"
    assert facets[library_with_gps.root / "巴黎.jpg"].country == "法国"
    assert facets[library_with_gps.root / ALBUM / IN_ALBUM].city == "杭州"


def test_the_result_is_remembered_in_the_index(library_with_gps: Library) -> None:
    """跑完一遍，面板与分组都从索引读到同一份结论，不必再问反查器。"""
    library_with_gps.resolve_places(resolver=FakeResolver())

    stored = library_with_gps.cached_facets(_refs(library_with_gps))
    row = stored[library_with_gps.root / "西安.jpg"]
    assert (row.city, row.region, row.country) == ("西安", "陕西省", "中国")
    assert row.place_inferred is True
    assert row.place_checked is True
    assert row.place_distance_km == pytest.approx(2.0)


def test_a_photo_without_coordinates_is_left_alone(library_with_gps: Library) -> None:
    resolver = FakeResolver()

    result = library_with_gps.resolve_places(resolver=resolver)

    assert result.resolved == 3  # 没坐标的那张不必问
    row = library_with_gps.cached_facets(_refs(library_with_gps))[
        library_with_gps.root / "无坐标.jpg"
    ]
    assert (row.city, row.country) == (None, None)
    assert row.place_checked is False  # 永远没得反查，不必每次都问


def test_a_second_run_reads_no_exif_at_all(
    library_with_gps: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """幂等的关键：已经有结论的行**连 EXIF 都不读**，否则大库每开一次就是一遍全量。"""
    library_with_gps.resolve_places(resolver=FakeResolver())

    def must_not_read(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        raise AssertionError("已经有结论了，不该再去读 EXIF")
        yield  # pragma: no cover - 让它是个生成器

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", must_not_read)
    resolver = FakeResolver()

    result = library_with_gps.resolve_places(resolver=resolver)

    assert result.scanned == len(PHOTOS)
    assert result.resolved == 0
    assert resolver.queried == []


def test_nowhere_nearby_is_a_conclusion_not_a_retry(library_with_gps: Library) -> None:
    """附近没有城市是**结论**：记下来，别每次开库都重算一遍。"""
    first = FakeResolver(found=False)
    library_with_gps.resolve_places(resolver=first)
    assert first.queried

    second = FakeResolver(found=False)
    result = library_with_gps.resolve_places(resolver=second)

    assert second.queried == []  # 没有坐标之外的情况也都被记住了
    assert result.resolved == 0
    row = library_with_gps.cached_facets(_refs(library_with_gps))[
        library_with_gps.root / "西安.jpg"
    ]
    assert row.place_checked is True  # 查过了，只是没查到
    assert row.city is None


def test_a_lookup_that_could_not_happen_is_not_remembered(
    library_with_gps: Library,
) -> None:
    """城市数据没下好时那次**不算**结论，否则照片会被永久钉在「未知」上。"""
    library_with_gps.resolve_places(resolver=FakeResolver(ready=False))

    row = library_with_gps.cached_facets(_refs(library_with_gps))[
        library_with_gps.root / "西安.jpg"
    ]
    assert row.place_checked is False

    # 数据到位后，第二遍必须还会去问。
    resolver = FakeResolver()
    library_with_gps.resolve_places(resolver=resolver)
    assert resolver.queried


def test_work_is_announced_only_when_there_is_work(library_with_gps: Library) -> None:
    """``on_work`` 是界面「该不该报进度」的唯一依据：空跑时它必须一声不吭。

    不这样的话，每打开一次已经解析过的库，横幅都会闪一句「正在解析照片的地点…」——
    说一件根本没发生的事。
    """
    announced: list[int] = []
    library_with_gps.resolve_places(
        resolver=FakeResolver(), on_work=lambda count: announced.append(count)
    )
    assert announced == [len(PHOTOS)]  # 第一次：有活，四张都要读

    again: list[int] = []
    library_with_gps.resolve_places(
        resolver=FakeResolver(), on_work=lambda count: again.append(count)
    )
    assert again == []  # 第二次：全是空跑，一声不吭


def test_the_announcement_happens_before_the_photo_is_resolved(
    library_with_gps: Library,
) -> None:
    """顺序有讲究：得先说「要干活了」，反查时那条「正在加载城市库…」才有地方显示。"""
    events: list[str] = []

    def status(text: str) -> None:
        events.append("status")

    def on_work(count: int) -> None:
        events.append("work")

    class Talkative(FakeResolver):
        def place_for(
            self, latitude: float, longitude: float, *, status: object = None
        ) -> ResolvedPlace | None:
            answer = super().place_for(latitude, longitude, status=status)
            events.append("place")
            return answer

    library_with_gps.resolve_places(
        resolver=Talkative(),
        on_work=on_work,
        status=status,  # type: ignore[arg-type]
    )

    assert events[0] == "work"
    assert events.index("work") < events.index("place")


def test_cancellation_stops_the_pass(library_with_gps: Library) -> None:
    """切库之后不该还在后台反查。"""
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    result = library_with_gps.resolve_places(resolver=FakeResolver(), should_cancel=should_cancel)

    assert result.scanned < len(PHOTOS)


def test_progress_is_reported_without_a_known_total(library_with_gps: Library) -> None:
    """边扫边数，总数一开始是未知的：进度报 0 也不能算错。"""
    seen: list[tuple[int, int]] = []

    library_with_gps.resolve_places(
        resolver=FakeResolver(), on_progress=lambda done, total: seen.append((done, total))
    )

    assert seen, "总得报一次"
    assert seen[-1][0] == len(PHOTOS)  # 最后一次是收尾
    assert all(total == 0 or total == len(PHOTOS) for _done, total in seen)


def test_photo_facets_and_the_panel_share_one_answer(library_with_gps: Library) -> None:
    """面板那条兜底路也写回索引：它算出来的东西不许只留在内存里。"""
    ref = PhotoRef.from_path(library_with_gps.root / "无坐标.jpg")
    # 假装索引里已经有这一行（整库那一遍跑过了），坐标由调用方给出。
    library_with_gps.facet_index.store([(ref, library_with_gps.photo_facets([ref])[ref.path])])

    resolver = FakeResolver()
    result = library_with_gps.resolve_photo_place(ref, 34.215, 108.965, resolver=resolver)

    assert result.city == "西安"
    stored = library_with_gps.cached_facets([ref])[ref.path]
    assert (stored.city, stored.country) == ("西安", "中国")
    assert stored.place_checked is True


def test_the_panel_fallback_does_not_write_a_half_row(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """索引里还没有这一行时**不许**新写一行：半行会被当成「EXIF 已经读过了」，
    以后那次完整的读取就被挡住了。"""
    library = Library.open(tmp_path)
    ref = PhotoRef.from_path(make_photo("还没读过的照片.jpg"))

    result = library.resolve_photo_place(ref, 34.215, 108.965, resolver=FakeResolver())

    assert result.city == "西安"  # 结论照样给调用方
    assert library.facet_index.lookup([ref]) == {}  # 但索引里没有多出半行
