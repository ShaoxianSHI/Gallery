"""按坐标离线反查行政区划：坐标 → 最近的城市。

数据来自 GeoNames 的公开文件（CC BY 4.0），**自己解析、自己查最近城市**：
不引入反查库（要么拖进 scipy 约 40 MB，要么是 33 MB 的数据包），也
**绝不上传用户坐标** —— 这是与在线反查服务的本质区别，见 ADR 0003。

三个文件首次需要时下载，缓存在应用级缓存目录。它是缓存：删掉会重新下载，
下载失败只降级为「只显示坐标」，不影响其它功能。
"""

from __future__ import annotations

import io
import logging
import math
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.atomic import write_text_atomic
from gallery.core.errors import GeoDataError
from gallery.core.places.countries import admin1_label, country_label

logger = logging.getLogger(__name__)

#: GeoNames 公开数据的下载地址。只有公开数据在流动，用户坐标永远不出本机。
GEO_DATA_BASE_URL: Final = "https://download.geonames.org/export/dump/"

#: 城市库（人口 15000 以上，约 3.2 MB 压缩包，解压后约 10 MB）。
CITIES_ARCHIVE: Final = "cities15000.zip"
CITIES_FILE: Final = "cities15000.txt"

#: 行政一级区划代码 → 名称。
ADMIN1_FILE: Final = "admin1CodesASCII.txt"

#: 国家代码 → 英文名。
COUNTRIES_FILE: Final = "countryInfo.txt"

#: 下载超时（秒）。
DOWNLOAD_TIMEOUT_SECONDS: Final = 60.0

#: 最近城市超过这个距离就不给结论：说「离这里 800 公里的城市」比不说更糟。
MAX_DISTANCE_KM: Final = 300.0

#: 行政级别：同一座城市在数据里往往有好几条记录。
#: PPLC 首都、PPLA* 政府驻地、PPL 一般居民点、PPLX 城市内分区。
_PLACE_RANK: Final[dict[str, int]] = {
    "PPLC": 4,
    "PPLA": 3,
    "PPLA2": 3,
    "PPLA3": 3,
    "PPLA4": 3,
    "PPL": 2,
    "PPLX": 1,
}

#: 在这个半径内，优先取行政级别更高的记录 —— 这是「同一地点的不同粒度」的距离。
#: 实测：上海市中心最近的是区级条目「黄浦」，巴黎最近的是「Paris 04」，
#: 东京是「Yoyogi」，纽约是「Financial District」，都不是用户想看到的答案。
#: 半径不能放大：放到 15 公里时纽约会查成对岸行政级别更高的泽西市，
#: 那是另一座城市，不是同一个地方的粗粒度说法。
SAME_PLACE_RADIUS_KM: Final = 5.0

#: 下载用的取数函数签名：URL → 字节。
Fetcher = Callable[[str], bytes]

#: 粗网格的格子边长（度）。约 555 公里。
#:
#: 一次反查原本要把 3.4 万座城市全扫一遍（11ms），分组时一次要给几千张照片查，
#: 那样光反查就要一分钟。粗网格把候选缩到查询点周围 ±2 格（≥ 1100 公里），
#: 而 :data:`MAX_DISTANCE_KM` 只有 300 公里 —— **任何会被采纳的答案必然落在窗口里**，
#: 所以结果与全扫逐字一致，只是快了两个数量级。
GRID_CELL_DEGREES: Final = 5.0

#: 窗口半径（格数）。
GRID_RADIUS: Final = 2


@dataclass(frozen=True, slots=True)
class Place:
    """一次反查的结果。"""

    #: GeoNames 的拉丁名（``Hangzhou``），拿不到中文名时用它。
    city: str
    admin1: str | None
    country: str
    distance_km: float
    #: 中文名（``杭州``）。GeoNames 的 alternate names 里有就优先用它。
    local_city: str | None = None

    @property
    def display_city(self) -> str:
        return self.local_city or self.city

    def label(self) -> str:
        """展示用文案，由大到小：``中国 · 浙江省 · 杭州``（缺的层级自动省略）。"""
        parts: list[str] = []
        for part in (self.country, self.admin1, self.display_city):
            if part and part not in parts:
                parts.append(part)
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class _City:
    name: str
    local_name: str | None
    feature_code: str
    country_code: str
    admin1_code: str
    latitude_rad: float
    longitude_rad: float
    latitude: float
    longitude: float


class PlaceIndex:
    """城市索引。加载一次，之后可以反复按坐标查最近城市。"""

    def __init__(
        self,
        cities: Iterable[_City],
        admin1: dict[str, str],
        countries: dict[str, str],
        *,
        max_distance_km: float = MAX_DISTANCE_KM,
    ) -> None:
        self._cities = tuple(cities)
        self._admin1 = admin1
        self._countries = countries
        self._max_distance_km = max_distance_km
        self._grid = _build_grid(self._cities)

    @property
    def city_count(self) -> int:
        return len(self._cities)

    @classmethod
    def data_present(cls, directory: Path) -> bool:
        """三个数据文件是否都已在缓存里。"""
        directory = Path(directory)
        return all(
            (directory / name).is_file() and (directory / name).stat().st_size > 0
            for name in (CITIES_FILE, ADMIN1_FILE, COUNTRIES_FILE)
        )

    @classmethod
    def load(cls, directory: Path) -> PlaceIndex | None:
        """读缓存里的数据；缺失或不完整时返回 ``None``（调用方据此提示下载）。"""
        directory = Path(directory)
        if not cls.data_present(directory):
            return None
        try:
            cities = list(_parse_cities(directory / CITIES_FILE))
            admin1 = _parse_admin1(directory / ADMIN1_FILE)
            countries = _parse_countries(directory / COUNTRIES_FILE)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            # 数据是缓存：读坏了就当没有，下次重新下载即可。
            logger.warning("城市数据不可用，将按缺失处理：%s", exc)
            return None
        if not cities:
            return None
        return cls(cities, admin1, countries)

    def _candidates(self, latitude: float, longitude: float) -> Iterable[_City]:
        """查询点附近粗网格里的那些城市（见 :data:`GRID_CELL_DEGREES`）。"""
        cell_latitude = math.floor(latitude / GRID_CELL_DEGREES)
        cell_longitude = math.floor(longitude / GRID_CELL_DEGREES)
        picked: list[_City] = []
        for offset_latitude in range(-GRID_RADIUS, GRID_RADIUS + 1):
            for offset_longitude in range(-GRID_RADIUS, GRID_RADIUS + 1):
                bucket = self._grid.get(
                    (cell_latitude + offset_latitude, cell_longitude + offset_longitude)
                )
                if bucket:
                    picked.extend(self._cities[index] for index in bucket)
        return picked

    def nearest(self, latitude: float, longitude: float) -> Place | None:
        """返回最近的城市；超出 ``max_distance_km`` 或数据库为空时返回 ``None``。

        同一座城市在数据里有好几条记录（市政府、区、街道），直接取最近的那条
        会得到「黄浦」「Paris 04」这类答案。所以在最近记录的 3 公里内优先取
        行政级别最高的那条，同级再比距离 —— 再远就是另一座城市了，不能替换。
        """
        if not self._cities:
            return None
        latitude_rad = math.radians(latitude)
        longitude_rad = math.radians(longitude)
        scored: list[tuple[float, int, _City]] = []
        nearest_distance = float("inf")
        for city in self._candidates(latitude, longitude):
            distance = _haversine_km(latitude_rad, longitude_rad, city)
            nearest_distance = min(nearest_distance, distance)
            scored.append((distance, _PLACE_RANK.get(city.feature_code, 0), city))
        if nearest_distance > self._max_distance_km:
            return None
        nearby = [item for item in scored if item[0] <= nearest_distance + SAME_PLACE_RADIUS_KM]
        best_distance, _, best = min(nearby, key=lambda item: (-item[1], item[0]))
        return Place(
            city=best.name,
            admin1=admin1_label(
                best.country_code, self._admin1.get(f"{best.country_code}.{best.admin1_code}", "")
            )
            or None,
            country=country_label(best.country_code, self._countries.get(best.country_code, "")),
            distance_km=best_distance,
            local_city=best.local_name,
        )


def geo_data_dir(*, base: Path | None = None) -> Path:
    """城市数据缓存目录（应用级，与具体库无关）。"""
    return paths.app_cache_root(base=base) / "geo"


def download_data(directory: Path, *, fetch: Fetcher | None = None) -> None:
    """下载并落盘三个数据文件。

    :param fetch: 取数函数，测试注入假实现（测试不得依赖网络）。
    :raises GeoDataError: 网络失败、压缩包损坏或写盘失败。
    """
    directory = Path(directory)
    fetcher = fetch if fetch is not None else _fetch_url
    try:
        cities_text = _extract_cities(fetcher(GEO_DATA_BASE_URL + CITIES_ARCHIVE))
        admin1_text = fetcher(GEO_DATA_BASE_URL + ADMIN1_FILE).decode("utf-8", "replace")
        countries_text = fetcher(GEO_DATA_BASE_URL + COUNTRIES_FILE).decode("utf-8", "replace")
    except GeoDataError:
        raise
    except (urllib.error.URLError, OSError, zipfile.BadZipFile, KeyError) as exc:
        raise GeoDataError(
            f"下载城市数据失败：{exc}。请检查网络后重试。",
            path=directory,
            detail=str(exc),
        ) from exc

    try:
        write_text_atomic(directory / CITIES_FILE, cities_text)
        write_text_atomic(directory / ADMIN1_FILE, admin1_text)
        write_text_atomic(directory / COUNTRIES_FILE, countries_text)
    except OSError as exc:
        raise GeoDataError(
            f"无法写入城市数据缓存：{directory}（{exc.strerror or exc}）。",
            path=directory,
            detail=str(exc),
        ) from exc


def _fetch_url(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Gallery/0.1 (+https://github.com/ShaoxianSHI/Gallery)"},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return bytes(response.read())


def _extract_cities(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read(CITIES_FILE).decode("utf-8", "replace")


def _parse_cities(path: Path) -> list[_City]:
    cities: list[_City] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 11:
            continue
        try:
            latitude = float(fields[4])
            longitude = float(fields[5])
        except ValueError:
            continue
        cities.append(
            _City(
                name=fields[1],
                local_name=pick_chinese_name(fields[3]),
                feature_code=fields[7].strip().upper(),
                country_code=fields[8].strip().upper(),
                admin1_code=fields[10].strip(),
                latitude=latitude,
                longitude=longitude,
                latitude_rad=math.radians(latitude),
                longitude_rad=math.radians(longitude),
            )
        )
    return cities


#: 纯汉字（不含日文假名、韩文等）。
_ALL_CJK = re.compile(r"^[\u4e00-\u9fff]+$")


def pick_chinese_name(alternates: str) -> str | None:
    """从 GeoNames 的 alternate names 里挑一个中文名。

    这一列没有语言标签，只能按字形判断，规则是：

    - 整串都是汉字（假名、韩文、拉丁字母一律排除）
    - 至少两个字（``沪`` 这种单字简称太随意）
    - 不带「中国 / 中華」前缀（``中国上海``）
    - 取最短的：``杭州`` 优先于 ``杭州市``；同样长度时取先出现的
      （``东京`` 与 ``東京`` 都出现时取前一个）

    实测对真实数据的挑选结果：巴黎、东京、北京、杭州、拉萨、悉尼、开罗、波尔多。
    """
    candidates: list[str] = []
    for raw in alternates.split(","):
        name = raw.strip()
        if len(name) < 2 or not _ALL_CJK.match(name):
            continue
        if "中国" in name or "中華" in name:
            continue
        candidates.append(name)
    if not candidates:
        return None
    return min(candidates, key=len)


def _parse_admin1(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0].strip():
            mapping[fields[0].strip()] = fields[1].strip()
    return mapping


def _parse_countries(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 5 and fields[0].strip():
            mapping[fields[0].strip().upper()] = fields[4].strip()
    return mapping


def _build_grid(cities: Sequence[_City]) -> dict[tuple[int, int], list[int]]:
    """经纬度 → 城市下标。一座城市只落在一个格子里，查的时候看周围几格。"""
    grid: dict[tuple[int, int], list[int]] = {}
    for index, city in enumerate(cities):
        key = (
            math.floor(city.latitude / GRID_CELL_DEGREES),
            math.floor(city.longitude / GRID_CELL_DEGREES),
        )
        grid.setdefault(key, []).append(index)
    return grid


def _haversine_km(latitude_rad: float, longitude_rad: float, city: _City) -> float:
    delta_latitude = city.latitude_rad - latitude_rad
    delta_longitude = city.longitude_rad - longitude_rad
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_rad) * math.cos(city.latitude_rad) * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(haversine)))


__all__ = [
    "ADMIN1_FILE",
    "GRID_CELL_DEGREES",
    "CITIES_ARCHIVE",
    "CITIES_FILE",
    "COUNTRIES_FILE",
    "DOWNLOAD_TIMEOUT_SECONDS",
    "GEO_DATA_BASE_URL",
    "MAX_DISTANCE_KM",
    "SAME_PLACE_RADIUS_KM",
    "Place",
    "PlaceIndex",
    "download_data",
    "geo_data_dir",
    "pick_chinese_name",
]
