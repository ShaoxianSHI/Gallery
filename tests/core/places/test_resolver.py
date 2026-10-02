"""``gallery.core.places.resolver`` 的测试。

这个解析器干三件事：**用到才加载数据**、**必要时先下载并把进度说出来**、
**同一坐标只查一次**。第三条不是微优化 —— 一次旅行的几百张照片共用一个坐标，
逐张反查就是几百次全表扫。

不联网：下载走注入的假取数函数，数据用 ``tmp_path`` 现场造。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from gallery.core.places.geonames import ADMIN1_FILE, CITIES_FILE, COUNTRIES_FILE, Place
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace

#: 假城市的坐标。
LATITUDE, LONGITUDE = 31.2304, 121.4737


class FakeIndex:
    """只实现 ``nearest`` 的假索引（解析器只用到它）。"""

    def __init__(self, answer: Place | None) -> None:
        self.answer = answer
        self.calls = 0

    def nearest(self, latitude: float, longitude: float) -> Place | None:
        self.calls += 1
        return self.answer


def _place(city: str = "上海") -> Place:
    return Place(city=city, admin1="Shanghai", country="China", distance_km=1.2, local_city="上海")


def _fake_fetch(payloads: dict[str, bytes]) -> object:
    def fetch(url: str) -> bytes:
        for name, data in payloads.items():
            if url.endswith(name):
                return data
        raise AssertionError(f"不该去取 {url}")

    return fetch


def _cities_zip() -> bytes:
    line = "12345\t假市\t假市\t\t31.23\t121.47\tP\tPPL\tCN\t\t00\t\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(CITIES_FILE, line)
    return buffer.getvalue()


def test_place_comes_from_the_index() -> None:
    resolver = LazyPlaceResolver(FakeIndex(_place()), download=False)  # type: ignore[arg-type]

    place = resolver.place_for(LATITUDE, LONGITUDE)

    assert place == ResolvedPlace(city="上海", region="Shanghai", country="China", distance_km=1.2)
    assert resolver.ready


def test_same_coordinates_are_looked_up_once() -> None:
    """一次旅行的几百张照片共用一个坐标 —— 反查也该只做一次。"""
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index, download=False)  # type: ignore[arg-type]

    for _ in range(50):
        resolver.place_for(LATITUDE, LONGITUDE)

    assert index.calls == 1


def test_coordinates_one_metre_apart_share_the_answer() -> None:
    """照片的 GPS 抖动在米级，不该因此多查一次。"""
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index, download=False)  # type: ignore[arg-type]

    resolver.place_for(31.230400, 121.473700)
    resolver.place_for(31.230405, 121.473710)  # 相距约 1.5 米

    assert index.calls == 1


def test_different_coordinates_are_looked_up_separately() -> None:
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index, download=False)  # type: ignore[arg-type]

    resolver.place_for(31.23, 121.47)
    resolver.place_for(48.85, 2.35)  # 另一座城市

    assert index.calls == 2


def test_nowhere_is_remembered_as_a_conclusion() -> None:
    """附近没有城市记录是结论，会记下来 —— 不必每次都重算一遍。"""
    index = FakeIndex(None)
    resolver = LazyPlaceResolver(index, download=False)  # type: ignore[arg-type]

    resolver.place_for(0.0, 0.0)
    resolver.place_for(0.0, 0.0)

    assert index.calls == 1
    assert resolver.place_for(0.0, 0.0) is None


def test_missing_data_is_downloaded_once_and_announced(tmp_path: Path) -> None:
    """数据没就绪时当场下载，并且**说出**自己在下 —— 悄悄联网是不行的。"""
    statuses: list[str] = []
    resolver = LazyPlaceResolver(
        None,
        directory=tmp_path / "geo",
        fetch=_fake_fetch(
            {
                "cities15000.zip": _cities_zip(),
                ADMIN1_FILE: "CN.00\t假省\n".encode(),
                COUNTRIES_FILE: "CN\t\t\t中国\n".encode(),
            }
        ),  # type: ignore[arg-type]
    )

    place = resolver.place_for(LATITUDE, LONGITUDE, status=statuses.append)

    assert statuses and "下载" in statuses[0]
    # 距离由真实索引算出来，这里只断言「有」—— 面板要靠它提醒「这只是最近的城市」。
    assert place is not None and place.distance_km is not None
    assert (place.city, place.region, place.country) == ("假市", "假省", "中国")
    assert resolver.ready

    # 第二次不该再下载。
    resolver.place_for(LATITUDE + 1, LONGITUDE)
    assert statuses.count(statuses[0]) == 1


def test_failed_download_does_not_poison_the_cache(tmp_path: Path) -> None:
    """下载失败时**不缓存**「查不到」：这次没数据、下次有了，不该还停在未知。"""
    statuses: list[str] = []
    resolver = LazyPlaceResolver(
        None,
        directory=tmp_path / "geo",
        fetch=lambda url: (_ for _ in ()).throw(OSError("没网")),
    )

    assert resolver.place_for(LATITUDE, LONGITUDE, status=statuses.append) is None
    assert resolver.ready is False
    assert statuses and "下载" in statuses[0]

    # 数据后来到位了：换个解析器应当能给出结论。
    late = LazyPlaceResolver(
        None,
        directory=tmp_path / "geo",
        fetch=_fake_fetch(
            {
                "cities15000.zip": _cities_zip(),
                ADMIN1_FILE: "CN.00\t假省\n".encode(),
                COUNTRIES_FILE: "CN\t\t\t中国\n".encode(),
            }
        ),
    )
    assert late.place_for(LATITUDE, LONGITUDE) is not None


def test_existing_index_is_reused_for_the_panel() -> None:
    """面板已经加载过索引时，分组这边不该再读一遍 8.5 MB 城市表。"""
    index = FakeIndex(_place())

    resolver = LazyPlaceResolver(index, download=False)  # type: ignore[arg-type]

    assert resolver.index is index


def test_cached_data_is_used_without_downloading(tmp_path: Path) -> None:
    """数据已经在缓存里就别再下 —— ``fetch`` 一旦被调用这个测试就红。"""
    directory = tmp_path / "geo"
    directory.mkdir(parents=True)
    (directory / CITIES_FILE).write_text(
        "12345\t旧市\t旧市\t\t31.23\t121.47\tP\tPPL\tCN\t\t00\t\n", encoding="utf-8"
    )
    (directory / ADMIN1_FILE).write_text("CN.00\t旧省\n", encoding="utf-8")
    (directory / COUNTRIES_FILE).write_text("CN\t\t\t旧国\n", encoding="utf-8")

    def never(_url: str) -> bytes:
        raise AssertionError("数据都齐了，不该联网")

    resolver = LazyPlaceResolver(None, directory=directory, fetch=never)

    # 国家名走内置映射（core.places.countries），与数据文件里写的无关。
    place = resolver.place_for(LATITUDE, LONGITUDE)
    assert place is not None
    assert (place.city, place.region, place.country) == ("旧市", "旧省", "中国")
    assert place.distance_km is not None
