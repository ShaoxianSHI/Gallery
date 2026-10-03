"""``gallery.core.places.dataset`` 的测试：读自带城市库，按坐标查最近的城市。

测试数据一律 ``tmp_path`` 现场造（AGENTS.md）：城市库本身是 380 KB 的生成产物，
不该为了测一个解析函数就把它塞进测试里。唯一碰真文件的是 :func:`test_bundled_database_is_usable`
—— 它要守住「随包分发的那份真的能用」，那是整个功能的地基。
"""

from __future__ import annotations

import gzip
import math
from pathlib import Path

import pytest

from gallery.core.places.dataset import (
    GRID_CELL_DEGREES,
    GRID_RADIUS,
    MAX_DISTANCE_KM,
    Place,
    PlaceIndex,
    _City,
    _haversine_km,
    _parse,
    is_real_coordinate,
)


def _city(
    city: str,
    latitude: float,
    longitude: float,
    *,
    region: str | None = None,
    country: str = "中国",
    population: int = 0,
) -> _City:
    return _City(
        region=region,
        city=city,
        country=country,
        population=population,
        latitude_rad=math.radians(latitude),
        longitude_rad=math.radians(longitude),
        latitude=latitude,
        longitude=longitude,
    )


def _index() -> PlaceIndex:
    """人造城市集：密集簇 + 分散孤点，覆盖窗口内外的各种位置。"""
    cities: list[_City] = []
    cities += [_city(f"密{i}", 31.20 + i * 0.01, 121.40 + i * 0.01) for i in range(40)]
    cities += [
        _city(f"欧{i}", 48.80 + i * 0.01, 2.30 + i * 0.01, country="法国") for i in range(20)
    ]
    cities += [
        _city(f"南{i}", -33.80 - i * 0.01, 151.20 + i * 0.01, country="澳洲") for i in range(10)
    ]
    cities += [_city(f"线{i}", 1.30 + i * 0.01, 179.50 + i * 0.01) for i in range(10)]
    cities.append(_city("孤点", -54.80, -68.30))
    cities.append(_city("极北", 78.20, 15.60, population=1_000_000))
    return PlaceIndex(cities)


def _write_db(path: Path, cities: list[_City]) -> Path:
    """按与生成脚本相同的格式写一份城市库。"""
    body = "# 测试用\n# 列：region\tcity\tcountry\tlatitude\tlongitude\tpopulation\n"
    body += "".join(
        f"{c.region or ''}\t{c.city}\t{c.country}"
        f"\t{c.latitude:.4f}\t{c.longitude:.4f}\t{c.population}\n"
        for c in cities
    )
    path.write_bytes(gzip.compress(body.encode("utf-8"), 9))
    return path


# ---- 解析 ---------------------------------------------------------------------


def test_parse_reads_the_six_columns(tmp_path: Path) -> None:
    path = _write_db(
        tmp_path / "p.tsv.gz",
        [_city("杭州", 30.29, 120.16, region="浙江省", population=1_193_601)],
    )

    index = PlaceIndex.load(path)

    assert index is not None and index.city_count == 1
    assert index.nearest(30.29, 120.16) == Place("杭州", "浙江省", "中国", 0.0)


def test_parse_skips_comments_and_bad_rows(tmp_path: Path) -> None:
    """缺列、坐标不是数字、空城市名：都只丢那一行，不让整份文件作废。"""
    body = (
        "# 注释\n"
        "\n"
        "\t\t中国\t30.29\t120.16\t100\n"  # 城市名为空 → 丢
        "浙江省\t杭州\t中国\t不是数字\t120.16\t100\n"  # 坐标坏了 → 丢
        "浙江省\t杭州\t中国\t30.29\n"  # 列数不够 → 丢
        "浙江省\t杭州\t中国\t30.29\t120.16\t不是数字\n"  # 人口坏了 → 保留，人口当 0
    )
    path = tmp_path / "p.tsv.gz"
    path.write_bytes(gzip.compress(body.encode("utf-8"), 9))

    index = PlaceIndex.load(path)

    assert index is not None and index.city_count == 1


def test_missing_or_broken_file_returns_none_instead_of_raising(tmp_path: Path) -> None:
    """城市库读不到只说明安装坏了，不该让地点分组整个崩掉（降级为不显示地点）。"""
    assert PlaceIndex.load(tmp_path / "没有这个文件") is None

    broken = tmp_path / "broken.tsv.gz"
    broken.write_bytes("这不是 gzip".encode())
    assert PlaceIndex.load(broken) is None

    empty = _write_db(tmp_path / "empty.tsv.gz", [])
    assert PlaceIndex.load(empty) is None


def test_parse_is_lazy_enough_not_to_materialize_everything() -> None:
    """_parse 是生成器：解析两万条不该先在内存里攒成列表。"""
    parsed = _parse("杭州\t浙江省\t中国\t30.29\t120.16\t100\n")
    assert not isinstance(parsed, list)
    assert len(list(parsed)) == 1


# ---- 最近邻 -------------------------------------------------------------------


def test_nearest_returns_the_closest_city() -> None:
    index = PlaceIndex([_city("远", 31.30, 121.50), _city("近", 31.23, 121.47)])

    place = index.nearest(31.23, 121.47)

    assert place is not None and place.city == "近"


def test_nearest_gives_up_when_far_away() -> None:
    """最近的记录在几百公里外就不给结论：说了反而更误导。

    查询点必须**是真实坐标**：``(0, 0)`` 是「没有位置」的占位写法，会在
    :func:`dataset.is_real_coordinate` 那一步就短路返回 ``None`` —— 压根到不了
    距离比较那一段。（用 ``(0, 0)`` 断言 ``None`` 与下面那条 Null Island 的用例
    完全重复，看着绿着，什么也没守住。）
    """
    index = PlaceIndex([_city("悉尼", -33.87, 151.21)])

    assert index.nearest(0.0, 120.0) is None, "隔着半个地球还给结论，那是在编地名"

    # 同一个查询点，把城市挪到窗口以内就该有结论 —— 这才说明「给不给」取决于
    # 距离，而不是恰好什么别的。
    near = PlaceIndex([_city("近处的城", 0.0, 119.0)])
    assert near.nearest(0.0, 120.0) is not None, "窗口以内却也不给结论，白白丢掉一条记录"


def test_same_point_prefers_the_bigger_city() -> None:
    """几乎同点的两条记录取人口多的：城市总比它内部的某个区块人多。

    这条替代的是过去靠 ``feature code`` 判行政级别的那套 —— 现在库里没有级别了。
    """
    index = PlaceIndex(
        [
            _city("闸北", 31.2304, 121.4737, region="上海市", population=840_000),
            _city("上海", 31.2304, 121.4737, region="上海市", population=24_870_000),
        ]
    )

    place = index.nearest(31.2304, 121.4737)

    assert place is not None and place.city == "上海"


# ---- 坐标为 (0, 0) ---------------------------------------------------------------


@pytest.mark.parametrize(("latitude", "longitude"), [(0.0, 0.0), (-0.0, 0.0)])
def test_null_island_query_gives_no_answer(latitude: float, longitude: float) -> None:
    """相机定不到 GPS 时填的是 0，不是空。

    那是几内亚湾里一个不存在的点。拿它反查会得到一个**自信的错答案** ——
    源数据里恰好有一条坐标为 (0,0) 的记录，于是所有坏照片都会显示成同一个地名。
    """
    index = PlaceIndex([_city("假市", 0.0, 0.0), _city("真市", 31.23, 121.47)])

    assert index.nearest(latitude, longitude) is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-0.1807, -78.4678),  # 赤道上的基多
        (51.4779, 0.0015),  # 本初子午线
        (0.0, 116.3913),  # 只有纬度是 0
        (39.9042, 0.0),  # 只有经度是 0
    ],
)
def test_equator_and_prime_meridian_are_real_places(latitude: float, longitude: float) -> None:
    """**只判两个同时为零**：赤道与本初子午线都是真实存在的位置，不能误伤。"""
    index = PlaceIndex([_city("赤道上的", -0.18, -78.47), _city("本初子午线上的", 51.48, 0.0)])

    assert is_real_coordinate(latitude, longitude) is True
    # 没被当成 (0,0) 挡掉：赤道上的那座查得到，另一处则是「附近没有城市」
    assert index.nearest(-0.18, -78.47) is not None


def test_records_at_null_island_are_dropped_when_parsing(tmp_path: Path) -> None:
    """生成期已经丢掉了，但解析的是随包分发的文件，不该悄悄收下坏行。"""
    body = "广东省\t坏行\t中国\t0.0000\t0.0000\t100\n浙江省\t杭州\t中国\t30.2900\t120.1600\t100\n"
    path = tmp_path / "p.tsv.gz"
    path.write_bytes(gzip.compress(body.encode("utf-8"), 9))

    index = PlaceIndex.load(path)

    assert index is not None and index.city_count == 1
    assert index.nearest(0.0, 0.0) is None


def test_empty_index_answers_nothing() -> None:
    assert PlaceIndex([]).nearest(31.2, 121.4) is None


def test_max_distance_is_configurable() -> None:
    index = PlaceIndex([_city("远处", 32.0, 121.0)], max_distance_km=10.0)

    assert index.nearest(31.2, 121.4) is None


# ---- 粗网格的等价性 -------------------------------------------------------------


def _brute_force(index: PlaceIndex, latitude: float, longitude: float) -> str | None:
    """没有网格时的答案（照 :meth:`PlaceIndex.nearest` 的算法重写一遍）。"""
    latitude_rad = math.radians(latitude)
    longitude_rad = math.radians(longitude)
    scored = [(_haversine_km(latitude_rad, longitude_rad, city), city) for city in index._cities]
    if not scored:
        return None
    nearest_distance = min(item[0] for item in scored)
    if nearest_distance > index._max_distance_km:
        return None
    within = [item for item in scored if item[0] <= nearest_distance + 1.0]
    return min(within, key=lambda item: (-item[1].population, item[0]))[1].city


def test_grid_matches_brute_force_on_every_query_point() -> None:
    """``nearest`` 为了速度只看查询点附近几格（见 GRID_CELL_DEGREES）。

    这条捷径的正确性全押在一句话上：**任何会被采纳的答案必然落在那个窗口里**
    （MAX_DISTANCE_KM 只有 300 公里，窗口半径至少 1100 公里）。
    等价性这种事抽查不能证明，所以整个造出来的城市集上逐点比一遍。
    """
    index = _index()
    queries = [
        (31.23, 121.45),  # 落在密集簇里
        (48.85, 2.35),
        (-33.87, 151.21),
        (1.35, 179.55),  # 跨日期变更线附近
        (-54.80, -68.30),  # 正好在城市上
        (78.22, 15.64),
        (0.0, 0.0),  # 海上：两边都该给「查不到」
        (89.9, 179.9),
        (-89.9, -179.9),
    ]
    queries += [(31.0 + i * 0.017, 121.0 + i * 0.017) for i in range(24)]  # 格子边界一个不落

    for latitude, longitude in queries:
        place = index.nearest(latitude, longitude)
        got = None if place is None else place.city
        assert got == _brute_force(index, latitude, longitude), (
            f"坐标 {latitude},{longitude} 上两条路径不同"
        )


def test_window_covers_more_than_any_accepted_answer() -> None:
    """把「窗口半径」这件事钉住：它必须远大于会被采纳的最大距离。"""
    assert GRID_RADIUS * GRID_CELL_DEGREES * 111.0 > MAX_DISTANCE_KM


def test_grid_actually_narrows_the_search() -> None:
    index = _index()

    picked = list(index._candidates(31.23, 121.45))

    assert 0 < len(picked) < len(index._cities)


# ---- 随包分发的那份 -------------------------------------------------------------


def test_bundled_database_is_usable() -> None:
    """守住地基：随包分发的那份真的能加载、能查出结果。

    整个地点功能都建立在它身上。文件缺失、没打进包、或者格式改坏了，
    都应该在这里就红，而不是等用户打开一个库才发现地点全是「未知」。
    """
    index = PlaceIndex.load()

    assert index is not None
    assert index.city_count > 10_000
    place = index.nearest(30.2489, 120.1473)  # 杭州西湖
    assert place is not None
    assert place.city == "杭州"
    assert place.country == "中国"


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected"),
    [
        # 这几个是 ADR 0012 记录下来的真实结论，改生成脚本时别把它们改坏了
        (22.7787, 113.8454, "深圳"),  # 深圳·松岗：区不是城市，要归属到深圳
        (22.5455, 114.0683, "深圳"),  # 深圳·福田：名字是「深圳」，不是旧县名「宝安」
        (31.2304, 121.4737, "上海"),  # 上海·浦东：直辖市没有省级那层
        (29.5600, 106.5800, "重庆"),  # 重庆·渝中：重庆横跨 470 公里，质心盖不住
        (22.3823, 114.1891, "香港"),
        (22.1560, 113.5580, "澳门"),
        (25.0370, 121.5640, "臺北"),  # 源数据的 native 是繁体，保持原样
        (44.8378, -0.5792, "Bordeaux"),  # 境外保持当地写法
    ],
)
def test_bundled_database_gives_the_documented_places(
    latitude: float, longitude: float, expected: str
) -> None:
    index = PlaceIndex.load()

    assert index is not None
    place = index.nearest(latitude, longitude)

    assert place is not None and place.city == expected


def test_bundled_database_shows_domestic_places_in_chinese() -> None:
    index = PlaceIndex.load()
    assert index is not None

    place = index.nearest(30.2489, 120.1473)

    assert place is not None
    assert place.label() == "中国 · 浙江省 · 杭州"


def test_bundled_database_has_no_province_for_direct_ly_runned_places() -> None:
    """直辖市、香港、澳门不设省级那一层，显示成「中国 · 上海」。"""
    index = PlaceIndex.load()
    assert index is not None

    assert index.nearest(31.2304, 121.4737).label() == "中国 · 上海"  # type: ignore[union-attr]
    assert index.nearest(22.3823, 114.1891).label() == "中国 · 香港"  # type: ignore[union-attr]
    assert index.nearest(22.1560, 113.5580).label() == "中国 · 澳门"  # type: ignore[union-attr]


def test_a_population_column_that_int_rejects_does_not_break_the_whole_file(
    tmp_path: Path,
) -> None:
    """人口列里出现 ``str.isdigit()`` 说行、``int()`` 却拒绝的字符（``"²"``）时，
    丢的是**这一条**，不是整份城市库。

    守卫与转换说的必须是同一件事：``"²".isdigit()`` 为真而 ``int("²")`` 抛
    ``ValueError``，于是「看起来像数字」的那一步放行了，真去转的那一步炸了。
    炸出来的地方是 :meth:`PlaceIndex.load` 的 ``try`` **之外**，一路抛到地点分组
    —— 而那份文件的承诺是「读不到或损坏时返回 ``None``」。
    """
    body = (
        "浙江省\t杭州\t中国\t30.29\t120.16\t²\n"  # isdigit() 为真，int() 抛
        "浙江省\t宁波\t中国\t29.87\t121.55\t500000\n"  # 正常的一条，必须还在
    )
    path = tmp_path / "p.tsv.gz"
    path.write_bytes(gzip.compress(body.encode("utf-8"), 9))

    index = PlaceIndex.load(path)  # 过去在这里抛 ValueError

    # 这一行**照常留下**（人口当 0，与同文件里已有的那条规矩一致）——
    # 丢的只是那个读不出来的数字，不是这座城市。
    assert index is not None and index.city_count == 2
    assert index.nearest(29.87, 121.55) == Place("宁波", "浙江省", "中国", 0.0)
    assert index.nearest(30.29, 120.16) == Place("杭州", "浙江省", "中国", 0.0)
