"""``gallery.core.places.resolver`` 的测试。

这个解析器只干两件事：**用到才加载城市库**、**同一坐标只查一次**。第二条不是微优化 ——
一次旅行的几百张照片共用一个坐标，逐张反查就是几百次全表扫。

这里不再有下载：城市库随应用分发。加载失败要**如实降级**而不是崩掉，
见 :func:`test_missing_database_does_not_poison_the_cache`。
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path

import pytest

from gallery.core.places.dataset import Place, PlaceIndex
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
    """一个**已经贴好标签**的城市（标签是城市库生成期做的，解析器不碰）。"""
    return Place(city=city, region="上海市", country="中国", distance_km=1.2)


def _db(path: Path, *, city: str = "杭州") -> Path:
    body = f"上海市\t{city}\t中国\t30.2300\t121.4700\t1000\n"
    path.write_bytes(gzip.compress(body.encode("utf-8"), 9))
    return path


def test_place_comes_from_the_index() -> None:
    resolver = LazyPlaceResolver(FakeIndex(_place()))  # type: ignore[arg-type]

    place = resolver.place_for(LATITUDE, LONGITUDE)

    assert place == ResolvedPlace(city="上海", region="上海市", country="中国", distance_km=1.2)
    assert resolver.ready


def test_place_is_loaded_from_the_given_file(tmp_path: Path) -> None:
    resolver = LazyPlaceResolver(path=_db(tmp_path / "p.tsv.gz"))

    # 查询点正好落在那条记录上，距离算出来是 0（这里走的是真索引，不是假实现）
    assert resolver.place_for(30.23, 121.47) == ResolvedPlace(
        city="杭州", region="上海市", country="中国", distance_km=0.0
    )
    assert resolver.ready


def test_same_coordinates_are_looked_up_once() -> None:
    """一次旅行的几百张照片共用一个坐标 —— 反查也该只做一次。"""
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]

    for _ in range(50):
        resolver.place_for(LATITUDE, LONGITUDE)

    assert index.calls == 1


def test_coordinates_one_metre_apart_share_the_answer() -> None:
    """照片的 GPS 抖动在米级，不该因此多查一次。"""
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]

    resolver.place_for(31.230400, 121.473700)
    resolver.place_for(31.230405, 121.473710)  # 相距约 1.5 米

    assert index.calls == 1


def test_different_coordinates_are_looked_up_separately() -> None:
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]

    resolver.place_for(31.23, 121.47)
    resolver.place_for(48.85, 2.35)  # 另一座城市

    assert index.calls == 2


#: 南太平洋上一片什么都没有的海域（**不是 (0,0)**：那个坐标另有含义，见 is_real_coordinate）
NOWHERE = (-58.0, -140.0)


def test_nowhere_is_remembered_as_a_conclusion() -> None:
    """附近没有城市记录是结论，会记下来 —— 不必每次都重算一遍。"""
    index = FakeIndex(None)
    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]

    resolver.place_for(*NOWHERE)
    resolver.place_for(*NOWHERE)

    assert index.calls == 1
    assert resolver.place_for(*NOWHERE) is None


def test_missing_database_does_not_poison_the_cache(tmp_path: Path) -> None:
    """城市库读不出来时**不缓存「查不到」**。

    这与「附近没有城市」必须分开：前者是**这次没查成**（安装坏了），记下来等于把
    这些照片永久钉在「未知」上。``ready`` 正是用来区分这两者的（ADR 0010 第 3 节）。
    """
    resolver = LazyPlaceResolver(path=tmp_path / "没有这个文件")

    assert resolver.place_for(LATITUDE, LONGITUDE) is None
    assert resolver.ready is False

    # 文件后来到位了：换个解析器应当能给出结论。
    assert LazyPlaceResolver(path=_db(tmp_path / "p.tsv.gz")).place_for(LATITUDE, LONGITUDE)


def test_loading_reports_status_once(tmp_path: Path) -> None:
    """加载要说一声：这是唯一会拖慢地点分组的一步，用户不该对着空白等。"""
    statuses: list[str] = []
    resolver = LazyPlaceResolver(path=_db(tmp_path / "p.tsv.gz"))

    resolver.place_for(LATITUDE, LONGITUDE, status=statuses.append)
    resolver.place_for(LATITUDE + 0.01, LONGITUDE, status=statuses.append)

    assert statuses == ["正在加载城市库…"]


def test_an_unusable_database_is_tried_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """城市库读不出来时，**整轮只试一次** —— 不是每张照片一次。

    「缓存未命中就再读一次」在这里是错的：那个文件要么在要么不在，同一个进程里它
    不会自己变好，再试多少次都一样 —— 而每张照片一次就是几百次重读重解析。
    """
    loads: list[Path | None] = []
    original = PlaceIndex.load

    def counting(path: Path | None = None) -> PlaceIndex | None:
        loads.append(path)
        return original(path)

    monkeypatch.setattr(PlaceIndex, "load", staticmethod(counting))
    statuses: list[str] = []
    resolver = LazyPlaceResolver(path=tmp_path / "没有这个文件")

    for step in range(20):
        resolver.place_for(LATITUDE + step * 0.01, LONGITUDE, status=statuses.append)

    assert len(loads) == 1, f"20 个不同坐标只该试一次，实际试了 {len(loads)} 次"
    assert statuses == ["正在加载城市库…"], "进度提示也不该一遍一遍刷"


def test_null_island_coordinates_are_refused(tmp_path: Path) -> None:
    """相机定不到 GPS 时填 0：那不是位置，不该被当成「附近没有城市」记进索引。"""
    index = FakeIndex(_place())
    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]

    assert resolver.place_for(0.0, 0.0) is None
    assert index.calls == 0, "坐标无效时不该去查索引"


def test_existing_index_is_reused_for_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """面板已经加载过索引时，分组这边**一次城市库都不该再读**。

    「断言 ``resolver.index is index``」是钉不住的 —— 构造函数里刚存进去的那一个，
    不成立的可能性为零。于是把 ``PlaceIndex.load`` 打成会炸的桩再走一遍完整查询：
    读得到结论就说明走的是手上那个索引。（桩必须会炸而不是返回 ``None``：``None`` 会被当成
    「库读不到」而正常降级，那样就证明不了任何事。）
    """
    index = FakeIndex(_place())

    def exploding_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("已经有索引了，不该再去读城市库")

    monkeypatch.setattr(PlaceIndex, "load", staticmethod(exploding_load))

    resolver = LazyPlaceResolver(index)  # type: ignore[arg-type]
    place = resolver.place_for(LATITUDE, LONGITUDE)

    assert resolver.index is index
    assert place is not None and place.city == "上海", "没有去读城市库，那结论从哪来"
    assert index.calls == 1


@pytest.mark.parametrize("payload", [b"", b"\x1f\x8b\x08", "不是 gzip".encode()])
def test_unreadable_database_falls_back_to_no_place(tmp_path: Path, payload: bytes) -> None:
    """装坏了就降级为「不显示地点」，其余功能照常 —— 不抛异常，也不联网。"""
    broken = tmp_path / "broken.tsv.gz"
    broken.write_bytes(payload)

    resolver = LazyPlaceResolver(path=broken)

    assert resolver.place_for(LATITUDE, LONGITUDE) is None
    assert resolver.ready is False


def test_real_database_answers() -> None:
    """端到端跑一遍真正随包分发的那份：松岗的照片要归属到深圳，不是「松岗」。"""
    resolver = LazyPlaceResolver()

    place = resolver.place_for(22.7787, 113.8454)

    assert place is not None
    assert (place.city, place.region, place.country) == ("深圳", "广东省", "中国")
    assert place.distance_km is not None


def test_real_database_loads_quickly() -> None:
    """两万条 gzip 要在可接受的时间内读完 —— 它挡在地点分组前面。"""
    started = time.perf_counter()
    index = PlaceIndex.load()
    elapsed = time.perf_counter() - started

    assert index is not None and index.city_count > 10_000
    assert elapsed < 5.0, f"加载城市库用了 {elapsed:.1f} 秒，太慢了"


# ---- 按名字查（ADR 0031）---------------------------------------------------------


def test_place_named_asks_the_index_once_and_caches_it() -> None:
    class Counting(PlaceIndex):
        """只数被问了几次 —— 补全是边打字边问的，不缓存就是每敲一个字问一遍。"""

        def __init__(self) -> None:
            super().__init__([])
            self.calls: list[str] = []

        def by_name(self, name: str, **kwargs: object) -> tuple[Place, ...]:
            self.calls.append(name)
            return ()

    index = Counting()
    resolver = LazyPlaceResolver(index)

    assert resolver.place_named("杭州") == ()
    assert resolver.place_named(" 杭州 ") == ()
    assert index.calls == ["杭州"]


def test_place_named_returns_nothing_when_the_database_is_unavailable() -> None:
    resolver = LazyPlaceResolver(path=Path("不存在.tsv.gz"))

    assert resolver.place_named("杭州") == ()
