"""生成自带的城市库 src/gallery/resources/places.tsv.gz。

**只在维护者更新数据时跑，应用本身从不联网取地名。** 用法::

    uv run python scripts/build_places_db.py            # 下载源数据并生成
    uv run python scripts/build_places_db.py --offline  # 只用已有源文件，不联网

归属在**生成期**一次算完（ADR 0012），运行时只剩一次最近邻查询：城市名取 ``native`` 字段，
区、街道沿 ``parent_id`` 归属到它所属的市（松岗离东莞比离深圳还近，只看距离会选错）。
源数据只用 ``csv-cities.csv.gz``（17 字段）—— 另一份精简 JSON 缺 ``type`` / ``native`` /
``parent_id``，用它会把整个境外过滤空。

四个坑（下面代码里都标注了）：直辖市在源数据里是 ``type=municipality`` 的**省**而不是
城市记录，辖区内的每个点都要标成市名（重庆横跨 470 公里，只有一个锚点会漏掉城区）；
港澳登记了两次（独立国家 + 「中国」下的 SAR），两边都要处理，否则香港会掉进广东省；
``parent_id`` 只有中国填了，「沿隶属链归属」只对中国的区、街道、县有效；``type`` 不可全信
（上海一个早已并入静安的旧区仍标着 ``city``），靠「同范围内人口大 3 倍就丢弃」消歧，但那条
只是补丁。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import sys
import urllib.request
from pathlib import Path
from typing import Any, Final

#: 允许从任意工作目录运行：把 src/ 挂到 sys.path 上，才能 import gallery.*。
_PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from gallery.core.places.countries import CHINA_PROVINCES_ZH  # noqa: E402

#: 源数据（GitHub Release 上的固定文件名，随版本更新）。
BASE_URL: Final = (
    "https://github.com/dr5hn/countries-states-cities-database/releases/latest/download"
)
CITIES_CSV: Final = "csv-cities.csv.gz"
COUNTRIES_JSON: Final = "json-countries+states+cities.json.gz"
DOWNLOAD_TIMEOUT: Final = 300.0

#: 生成结果的位置（运行时读的就是它）。
OUTPUT: Final = _PROJECT_ROOT / "src" / "gallery" / "resources" / "places.tsv.gz"

#: 列的顺序，也是运行时 ``core.places`` 解析的顺序。
COLUMNS: Final = ("region", "city", "country", "latitude", "longitude", "population")

#: 够格当「一座城市」的 ``type``。``adm1`` 在法国是「省」，但波尔多这种省驻地
#: 恰恰就是那个市（``Bordeaux`` 标成 ``adm1``），不算它波尔多自己就掉了。
CITY_TYPES: Final = frozenset({"city", "prefecture", "capital", "adm1"})

#: 不设省级、直接就是城市的属地（含港澳，用国家坐标一条记录）。
TERRITORIES: Final = {"HK": "香港", "MO": "澳门", "SG": "新加坡", "MC": "摩纳哥", "VA": "梵蒂冈"}

#: 直辖市：源数据里是 ``type=municipality`` 的省，不是城市记录。
MUNICIPALITIES: Final = {
    "Beijing": "北京",
    "Shanghai": "上海",
    "Tianjin": "天津",
    "Chongqing": "重庆",
}

#: 源数据里台湾的省名就叫 ``Taiwan``，我们的省名表里没有这一条。
_EXTRA_PROVINCES: Final = {"Taiwan": "台湾", "Hong Kong SAR": "香港", "Macau SAR": "澳门"}

#: 城市级锚点的最低人口。低于它的多半是镇 / 村，源数据里另外那些国家也不缺。
MIN_CITY_POPULATION: Final = 15000

#: 人口超过邻近者这么多倍，就认为小的是「大城市的误标区」，丢弃。
DOMINANCE_RATIO: Final = 3
DOMINANCE_RADIUS_KM: Final = 10.0

#: 爬不到归属时，退而求其次找最近的城市，最远不超过这个距离。
FALLBACK_MAX_KM: Final = 300.0

EARTH_RADIUS_KM: Final = 6371.0

#: 坐标为 ``(0, 0)`` 的行一律丢掉：那个点在几内亚湾里，世上并不存在，而相机定不到 GPS 时
#: 填的 0 恰好会命中它们，于是所有坏照片都显示成同一个城市。只判两个**同时**为零（纬度 0
#: 是赤道、经度 0 是本初子午线，都是真实位置）。
NULL_ISLAND: Final = (0.0, 0.0)


def _is_null_island(latitude: float, longitude: float) -> bool:
    return (latitude, longitude) == NULL_ISLAND


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _fetch(url: str, cache: Path) -> bytes:
    """取一个资源；``--offline`` 时只从 ``cache`` 读。"""
    if cache.exists():
        return cache.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "Gallery/0.1 (build script)"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        payload = bytes(response.read())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(payload)
    return payload


def _load_sources(cache_dir: Path, *, offline: bool) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """取两个源文件并解出城市表与国家表。"""
    if offline:
        missing = [n for n in (CITIES_CSV, COUNTRIES_JSON) if not (cache_dir / n).exists()]
        if missing:
            raise SystemExit(
                f"--offline 需要先备好这些文件：{', '.join(missing)}（在 {cache_dir}）"
            )

    def get(name: str) -> bytes:
        if offline:
            return (cache_dir / name).read_bytes()
        return _fetch(f"{BASE_URL}/{name}", cache_dir / name)

    cities_text = gzip.decompress(get(CITIES_CSV)).decode("utf-8")
    cities = list(csv.DictReader(io.StringIO(cities_text)))
    countries = {
        c["iso2"]: c for c in json.loads(gzip.decompress(get(COUNTRIES_JSON)).decode("utf-8"))
    }
    return cities, countries


class _Builder:
    """把源数据压成一张「城市名 + 省 + 国 + 坐标」的平表。"""

    def __init__(self, cities: list[dict[str, str]], countries: dict[str, Any]) -> None:
        self._rows = cities
        self._countries = countries
        self._by_id = {r["id"]: r for r in cities}
        self._provinces = {**CHINA_PROVINCES_ZH, **_EXTRA_PROVINCES}
        # 港澳在「中国」下另有一份，两个地方都要处理，否则香港会掉进广东省
        self._sar = {n for n in ("Hong Kong SAR", "Macau SAR") if _state_exists(countries, "CN", n)}

    # ---- 取值helper --------------------------------------------------------------

    @staticmethod
    def _coord(row: dict[str, str]) -> tuple[float, float] | None:
        try:
            return float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _population(row: dict[str, str]) -> int:
        raw = row.get("population") or ""
        return int(raw) if raw.isdigit() else 0

    @staticmethod
    def _name(row: dict[str, str]) -> str:
        return row.get("native") or row["name"]

    def _climb(self, row: dict[str, str]) -> dict[str, str]:
        """沿 ``parent_id`` 一路往上，直到是一条「城市级」记录。

        爬不上去就返回原记录 —— 调用方据此改用「找最近的城市」回退。
        **这条路只有中国走得通**：``parent_id`` 在源数据里只有中国填了（66%）。
        """
        seen: set[str] = set()
        current = row
        while (
            current["type"] not in ("city", "prefecture", "capital")
            and current.get("parent_id")
            and current["parent_id"] in self._by_id
            and current["id"] not in seen
        ):
            seen.add(current["id"])
            current = self._by_id[current["parent_id"]]
        return current

    # ---- 境外城市锚点 ------------------------------------------------------------

    def _anchors(self) -> list[tuple[dict[str, str], float, float]]:
        """够格当城市锚点的记录（境外 + 中国的城市级记录，供回退用）。"""
        picked: list[tuple[dict[str, str], float, float]] = []
        for row in self._rows:
            if row["country_code"] in TERRITORIES or row["type"] not in CITY_TYPES:
                continue
            if (
                row["type"] not in ("capital", "prefecture")
                and self._population(row) < MIN_CITY_POPULATION
            ):
                continue
            coord = self._coord(row)
            if coord is not None:
                picked.append((row, *coord))
        # 消歧：附近有人口大 3 倍的城市，说明这条是「大城市的误标区」（补丁，见模块文档）。
        return [
            (row, la, lo)
            for row, la, lo in picked
            if not any(
                _haversine_km(la, lo, la2, lo2) <= DOMINANCE_RADIUS_KM
                and self._population(other) > self._population(row) * DOMINANCE_RATIO
                for other, la2, lo2 in picked
            )
        ]

    def _nearest_city(self, la: float, lo: float, anchors: list[Any]) -> dict[str, str] | None:
        best: tuple[float, dict[str, str]] | None = None
        for row, la2, lo2 in anchors:
            distance = _haversine_km(la, lo, la2, lo2)
            if distance <= FALLBACK_MAX_KM and (best is None or distance < best[0]):
                best = (distance, row)
        return None if best is None else best[1]

    # ---- 组装 --------------------------------------------------------------------

    def build(self) -> list[tuple[str, str, str, float, float, int]]:
        out: list[tuple[str, str, str, float, float, int]] = []
        anchors = self._anchors()

        # ① 直辖市：不设省层，市名就是直辖市名。辖区内的**每个点**都标成市名，而不是只在
        #    省质心放一个锚点 —— 重庆横跨 470 公里，只有一个锚点的话城区会漏掉。
        municipalities = {
            s["name"]: MUNICIPALITIES[s["name"]]
            for s in self._countries["CN"]["states"]
            if s["type"] == "municipality" and s["name"] in MUNICIPALITIES
        }

        # ② 港澳：一条记录，用国家坐标。澳门在源数据里 states 为空，但国家表有坐标。
        for code in ("HK", "MO"):
            country = self._countries[code]
            out.append(
                (
                    "",
                    TERRITORIES[code],
                    "中国",
                    float(country["latitude"]),
                    float(country["longitude"]),
                    0,
                )
            )

        # ③ 中国（含直辖市辖区内的点）与台湾
        for row in self._rows:
            code = row["country_code"]
            if code not in ("CN", "TW") or row["state_name"] in self._sar:
                continue
            coord = self._coord(row)
            if coord is None:
                continue
            la, lo = coord
            if row["state_name"] in municipalities:
                out.append(
                    ("", municipalities[row["state_name"]], "中国", la, lo, self._population(row))
                )
                continue
            region = "台湾" if code == "TW" else self._provinces.get(row["state_name"], "")
            city = self._name(row)
            if row["type"] not in ("city", "prefecture", "capital"):
                climbed = self._climb(row)
                if climbed is not row:
                    city = self._name(climbed)
                else:
                    # 爬不上去（源数据里 92 个中国区缺 parent_id）：退回找最近的城市。
                    nearest = self._nearest_city(la, lo, anchors)
                    if nearest is not None:
                        city = self._name(nearest)
            out.append((region, city, "中国", la, lo, self._population(row)))

        # ④ 境外
        for row, la, lo in anchors:
            if row["country_code"] in ("CN", "TW"):
                continue
            out.append(
                (row["state_name"], row["name"], row["country_name"], la, lo, self._population(row))
            )
        for code, name in TERRITORIES.items():
            if code in ("HK", "MO"):
                continue
            country = self._countries[code]
            out.append(
                (
                    "",
                    name,
                    country["name"],
                    float(country["latitude"]),
                    float(country["longitude"]),
                    0,
                )
            )

        return _dedupe(out)


def _state_exists(countries: dict[str, Any], code: str, state_name: str) -> bool:
    return any(s["name"] == state_name for s in (countries[code].get("states") or []))


def _dedupe(
    rows: list[tuple[str, str, str, float, float, int]],
) -> list[tuple[str, str, str, float, float, int]]:
    """同名同坐标只留一条，并丢掉坐标为 ``(0, 0)`` 的坏行（见 :data:`NULL_ISLAND`）。

    源数据里有 328 组同名同城的重复记录。
    """
    seen: set[tuple[str, str, float, float]] = set()
    out = []
    dropped_null_island = 0
    for row in rows:
        if _is_null_island(row[3], row[4]):
            dropped_null_island += 1
            continue
        key = (row[0], row[1], round(row[3], 3), round(row[4], 3))
        if key not in seen:
            seen.add(key)
            out.append(row)
    if dropped_null_island:
        print(f"丢弃坐标为 (0,0) 的坏行 {dropped_null_island} 条（见 NULL_ISLAND）")
    return out


def render(rows: list[tuple[str, str, str, float, float, int]]) -> str:
    head = (
        "# Gallery 自带城市库 · 数据源 dr5hn/countries-states-cities-database（ODbL-1.0）\n"
        "# 由 scripts/build_places_db.py 生成，**请勿手改**；要改改那个脚本再重新生成。\n"
        "# 列：" + "\t".join(COLUMNS) + "\n"
    )
    body = "".join(
        f"{region}\t{city}\t{country}\t{la:.4f}\t{lo:.4f}\t{population}\n"
        for region, city, country, la, lo, population in rows
    )
    return head + body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="只用已下载的源文件，不联网")
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "gallery-build",
        help="源文件缓存目录",
    )
    args = parser.parse_args()

    cities, countries = _load_sources(args.cache, offline=args.offline)
    rows = _Builder(cities, countries).build()
    text = render(rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(gzip.compress(text.encode("utf-8"), 9))

    raw = len(text.encode("utf-8"))
    packed = OUTPUT.stat().st_size
    name = OUTPUT.relative_to(_PROJECT_ROOT)
    print(f"{name}：{len(rows)} 条，原始 {raw / 1024:.0f} KB，gzip {packed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
