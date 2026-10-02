"""``gallery.core.places.geonames`` 的测试：离线反查，不碰网络。

下载用注入的取数函数验证，数据文件用 ``tmp_path`` 现场造 —— 测试既不联网，
也不依赖 GeoNames 真实数据的规模。
"""

from __future__ import annotations

import io
import urllib.error
import zipfile
from pathlib import Path

import pytest

from gallery.core.errors import GeoDataError
from gallery.core.places.geonames import (
    ADMIN1_FILE,
    CITIES_FILE,
    COUNTRIES_FILE,
    Place,
    PlaceIndex,
    download_data,
    geo_data_dir,
    pick_chinese_name,
)


#: 造一条 cities15000.txt 的行（19 列，只填用得上的那几个）。
def city_line(
    name: str,
    latitude: float,
    longitude: float,
    country: str,
    admin1: str,
    population: int = 100000,
    alternates: str = "",
    feature_code: str = "PPLA",
) -> str:
    fields = [
        "12345",
        name,
        name,
        alternates,
        str(latitude),
        str(longitude),
        "P",
        feature_code,
        country,
        "",
        admin1,
        "01",
        "001",
        "0001",
        str(population),
        "",
        "10",
        "Europe/Paris",
        "2024-01-01",
    ]
    return "\t".join(fields)


BORDEAUX = city_line("Bordeaux", 44.8412, -0.5800, "FR", "75", 260000, alternates="Bordeaux,波尔多")
HANGZHOU = city_line(
    "Hangzhou",
    30.2936,
    120.1614,
    "CN",
    "02",
    11936010,
    alternates="Hangzhou,Hangchow,杭州,杭州市,中国杭州",
)
BILBAO = city_line("Bilbao", 43.2627, -2.9253, "ES", "59", 350000)
TOULOUSE = city_line("Toulouse", 43.6045, 1.4442, "FR", "76", 500000)

ADMIN1_TEXT = "\n".join(
    [
        "FR.75\tNew Aquitaine\tNew Aquitaine\t1",
        "FR.76\tOccitanie\tOccitanie\t2",
        "ES.59\tBasque Country\tBasque Country\t3",
        "CN.02\tZhejiang\tZhejiang\t4",
    ]
)

COUNTRIES_TEXT = "\n".join(
    [
        "# 注释行应当被跳过",
        "FR\tFRA\t250\tFR\tFrance\tParis\t547030\t65000000\tEU\t.fr\tEUR\tEuro\t33\t#####\t\ta\t1",
        "ES\tESP\t724\tES\tSpain\tMadrid\t504782\t46000000\tEU\t.es\tEUR\tEuro\t34\t#####\t\ta\t2",
        "CN\tCHN\t156\tCH\tChina\tBeijing\t9596960\t1400000000\tAS\t.cn\tCNY\tYuan\t86\t######\t\ta\t3",
    ]
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "geo"
    directory.mkdir()
    (directory / CITIES_FILE).write_text(
        "\n".join([BORDEAUX, BILBAO, TOULOUSE, HANGZHOU]), encoding="utf-8"
    )
    (directory / ADMIN1_FILE).write_text(ADMIN1_TEXT, encoding="utf-8")
    (directory / COUNTRIES_FILE).write_text(COUNTRIES_TEXT, encoding="utf-8")
    return directory


def test_data_present(data_dir: Path, tmp_path: Path) -> None:
    assert PlaceIndex.data_present(data_dir) is True
    assert PlaceIndex.data_present(tmp_path / "没有") is False


def test_load_returns_none_without_data(tmp_path: Path) -> None:
    assert PlaceIndex.load(tmp_path / "没有") is None


def test_load_returns_none_when_data_is_broken(data_dir: Path) -> None:
    """数据是缓存：读坏了就当没有，下次重新下载即可，不该抛异常。"""
    (data_dir / CITIES_FILE).write_bytes(b"\xff\xfe\x00\x01")

    assert PlaceIndex.load(data_dir) is None


def test_nearest_returns_city_with_admin_hierarchy(data_dir: Path) -> None:
    index = PlaceIndex.load(data_dir)
    assert index is not None

    place = index.nearest(44.8378, -0.5792)  # 那张真实照片的坐标

    assert place is not None
    assert place.city == "Bordeaux"
    # 非中国：一律保持 GeoNames 原名。alternate names 里的「波尔多」是别人写的译名，
    # 用上去就是替用户把他的相册翻译了（ADR 0011）。
    assert place.local_city is None
    assert place.admin1 == "New Aquitaine"
    assert place.country == "France"
    assert place.distance_km < 5
    assert place.label() == "France · New Aquitaine · Bordeaux"  # 国家 → 省 → 市


def test_nearest_picks_the_closest_city(data_dir: Path) -> None:
    index = PlaceIndex.load(data_dir)
    assert index is not None

    place = index.nearest(43.3, -2.9)

    assert place is not None
    assert place.city == "Bilbao"
    assert place.country == "Spain"


def test_nearest_gives_up_when_far_away(data_dir: Path) -> None:
    """最近的记录在几百公里外就不给结论：说了反而更误导。"""
    index = PlaceIndex.load(data_dir)
    assert index is not None

    assert index.nearest(-33.87, 151.21) is None  # 悉尼


def test_chinese_city_and_province(data_dir: Path) -> None:
    """中国的省市显示中文：城市来自 alternate names，省份来自静态映射。"""
    index = PlaceIndex.load(data_dir)
    assert index is not None

    place = index.nearest(30.27, 120.15)  # 杭州

    assert place is not None
    assert place.city == "Hangzhou"
    assert place.local_city == "杭州"
    assert place.admin1 == "浙江省"  # Zhejiang → 浙江省
    assert place.country == "中国"
    assert place.label() == "中国 · 浙江省 · 杭州"


def test_label_is_ordered_from_country_to_city() -> None:
    assert Place("Bordeaux", "New Aquitaine", "France", 1.0).label() == (
        "France · New Aquitaine · Bordeaux"
    )
    assert Place("Hangzhou", "浙江省", "中国", 1.0, "杭州").label() == "中国 · 浙江省 · 杭州"


def test_label_skips_missing_parts_and_duplicates() -> None:
    assert Place("Bordeaux", None, "France", 1.0).label() == "France · Bordeaux"
    assert Place("Monaco", "Monaco", "Monaco", 0.5).label() == "Monaco"
    assert Place("Nowhere", None, "中国", 1.0, "无名").label() == "中国 · 无名"


@pytest.mark.parametrize(
    ("alternates", "expected"),
    [
        ("Hangzhou,Hangchow,杭州,杭州市", "杭州"),  # 取最短的，别带上「市」
        ("Shanghai,上海,上海市,中国上海,沪", "上海"),  # 排除单字简称与国家前缀
        ("Tokyo,东京,東京", "东京"),  # 同长度取先出现的
        ("Lhasa,ラサ市,拉萨,拉萨市,拉薩", "拉萨"),  # 假名不算汉字
        ("Paris,巴黎", "巴黎"),  # 挑得出，但法国城市不会用（见下面的 country 门）
        ("Bordeaux,Burdigala", None),  # 没有汉字就没有
        ("Somewhere,中", None),  # 单字不要
    ],
)
def test_pick_chinese_name(alternates: str, expected: str | None) -> None:
    """这是个纯字符串函数；**用不用得上由调用方的国家判断决定**，见下面那条。"""
    assert pick_chinese_name(alternates) == expected


def test_geo_data_dir_is_under_app_cache(tmp_path: Path) -> None:
    assert geo_data_dir(base=tmp_path) == tmp_path / "Gallery" / "cache" / "geo"


# ---- 下载 ----------------------------------------------------------------------


def cities_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(CITIES_FILE, "\n".join([BORDEAUX, TOULOUSE]))
    return buffer.getvalue()


def fake_fetch(payloads: dict[str, bytes]) -> object:
    def fetch(url: str) -> bytes:
        for suffix, payload in payloads.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"没准备的地址：{url}")

    return fetch


def test_download_writes_all_three_files(tmp_path: Path) -> None:
    directory = tmp_path / "geo"
    payloads = {
        "cities15000.zip": cities_archive(),
        ADMIN1_FILE: ADMIN1_TEXT.encode(),
        COUNTRIES_FILE: COUNTRIES_TEXT.encode(),
    }

    download_data(directory, fetch=fake_fetch(payloads))  # type: ignore[arg-type]

    assert PlaceIndex.data_present(directory) is True
    index = PlaceIndex.load(directory)
    assert index is not None and index.city_count == 2


def test_download_reports_network_failure(tmp_path: Path) -> None:
    def failing(url: str) -> bytes:
        raise urllib.error.URLError("网络不可达")

    with pytest.raises(GeoDataError) as excinfo:
        download_data(tmp_path / "geo", fetch=failing)

    assert "下载城市数据失败" in excinfo.value.message
    assert "网络" in excinfo.value.message


def test_download_reports_broken_archive(tmp_path: Path) -> None:
    payloads = {
        "cities15000.zip": b"not a zip at all",
        ADMIN1_FILE: ADMIN1_TEXT.encode(),
        COUNTRIES_FILE: COUNTRIES_TEXT.encode(),
    }

    with pytest.raises(GeoDataError):
        download_data(tmp_path / "geo", fetch=fake_fetch(payloads))  # type: ignore[arg-type]


def test_download_reports_unwritable_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gallery.core.places.geonames as module

    def failing_write(path: Path, text: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(module, "write_text_atomic", failing_write)
    payloads = {
        "cities15000.zip": cities_archive(),
        ADMIN1_FILE: ADMIN1_TEXT.encode(),
        COUNTRIES_FILE: COUNTRIES_TEXT.encode(),
    }

    with pytest.raises(GeoDataError) as excinfo:
        download_data(tmp_path / "geo", fetch=fake_fetch(payloads))  # type: ignore[arg-type]

    assert "无法写入城市数据缓存" in excinfo.value.message


class TestSameplaceGranularity:
    """同一座城市在数据里有好几条记录，直接取最近的那条会得到区名。"""

    #: 上海（省级市，PPLA）与它内部的区级条目「黄浦」（PPL）。
    SHANGHAI = city_line(
        "Shanghai",
        31.2222,
        121.4581,
        "CN",
        "23",
        24874500,
        alternates="Shanghai,上海",
        feature_code="PPLA",
    )
    HUANGPU = city_line(
        "Huangpu",
        31.2300,
        121.4700,
        "CN",
        "23",
        656200,
        alternates="Huangpu,黄浦",
        feature_code="PPL",
    )
    #: 纽约（PPL）与其内部分区（PPLX）。
    NEW_YORK = city_line(
        "New York City", 40.7143, -74.0060, "US", "NY", 8804190, alternates="New York City,纽约"
    )
    FINANCIAL = city_line(
        "Financial District", 40.7079, -74.0088, "US", "NY", 60976, feature_code="PPLX"
    )
    #: 对岸的另一座城市：行政级别更高但不在「同一地点」的半径内。
    JERSEY_CITY = city_line(
        "Jersey City", 40.7282, -74.0776, "US", "NJ", 292449, feature_code="PPLA2"
    )
    #: 八十公里外的另一座城市，级别比上海低：近的一律优先，不能被远处的级别压过。
    SUZHOU = city_line(
        "Suzhou",
        31.3000,
        120.6000,
        "CN",
        "04",
        1647642,
        alternates="Suzhou,苏州",
        feature_code="PPL",
    )

    @pytest.fixture
    def index(self, tmp_path: Path) -> PlaceIndex:
        directory = tmp_path / "geo"
        directory.mkdir()
        (directory / CITIES_FILE).write_text(
            "\n".join(
                [
                    self.SHANGHAI,
                    self.HUANGPU,
                    self.NEW_YORK,
                    self.FINANCIAL,
                    self.JERSEY_CITY,
                    self.SUZHOU,
                ]
            ),
            encoding="utf-8",
        )
        (directory / ADMIN1_FILE).write_text(
            "CN.23\tShanghai\tShanghai\t1\nUS.NY\tNew York\tNew York\t2\n"
            "US.NJ\tNew Jersey\tNew Jersey\t3\nCN.04\tJiangsu\tJiangsu\t4",
            encoding="utf-8",
        )
        (directory / COUNTRIES_FILE).write_text(
            "CN\tCHN\t156\tCH\tChina\tBeijing\t1\t1\tAS\t.cn\tCNY\tYuan\t86\t#\t\ta\t1\n"
            "US\tUSA\t840\tUS\tUnited States\tWashington\t1\t1\tNA\t.us\tUSD\tDollar\t1\t#\t\ta\t2",
            encoding="utf-8",
        )
        loaded = PlaceIndex.load(directory)
        assert loaded is not None
        return loaded

    def test_city_wins_over_its_own_district(self, index: PlaceIndex) -> None:
        """区政府驻地在 1 公里内、市政府在 3 公里外，仍要显示城市。"""
        place = index.nearest(31.2300, 121.4700)

        assert place is not None
        assert place.city == "Shanghai"
        assert place.label() == "中国 · 上海市 · 上海"

    def test_section_of_a_city_is_not_shown_when_the_city_is_close(self, index: PlaceIndex) -> None:
        place = index.nearest(40.7079, -74.0088)  # 曼哈顿下城

        assert place is not None
        assert place.city == "New York City"
        assert place.admin1 == "New York"
        # 数据里给纽约写了汉字名「纽约」，但那是中国的写法，美国这边用不上（ADR 0011）。
        assert place.local_city is None
        assert place.label() == "United States · New York · New York City"

    def test_a_different_city_is_not_substituted_for_a_nearer_one(self, index: PlaceIndex) -> None:
        """泽西市行政级别更高但隔着一条河，不能顶替离得更近的纽约。"""
        place = index.nearest(40.7143, -74.0060)

        assert place is not None
        assert place.city == "New York City"

    def test_a_nearer_lower_ranked_city_is_not_replaced_by_a_far_high_ranked_one(
        self, index: PlaceIndex
    ) -> None:
        """苏州（PPL）就在脚下，上海（PPLA）在八十公里外：近的优先，级别不能跨城市压人。"""
        place = index.nearest(31.3000, 120.6000)

        assert place is not None
        assert place.city == "Suzhou"
        assert place.label() == "中国 · 江苏省 · 苏州"


class TestDomesticPlaces:
    """港澳台的照片也显示中文，但层级各按自己的样子（ADR 0011）。

    记录抄自真实的 GeoNames 数据：港澳在数据里的「省级」是**区**（``Sha Tin``、
    ``Nossa Senhora de Fátima``），台湾的省级是**历史旧名**（``Takao`` = 高雄旧称）。
    """

    SHA_TIN = city_line(
        "Sha Tin", 22.3823, 114.1891, "HK", "NST", 645000, alternates="Sha Tin,Shatin,沙田"
    )
    YUEN_LONG = city_line(
        "Yuen Long",
        22.4445,
        114.0222,
        "HK",
        "NYL",
        345000,
        alternates="Yuen Long,Yuenlong,元朗,元朗墟",
    )
    MACAU = city_line(
        "Macau", 22.1987, 113.5439, "MO", "02", 343000, alternates="Macau,Macao,Macau SAR,澳門,澳门"
    )
    KAOHSIUNG = city_line(
        "Kaohsiung",
        22.6153,
        120.3014,
        "TW",
        "02",
        2750000,
        alternates="Kaohsiung,Takao,高雄,高雄市",
    )
    TAIPEI = city_line(
        "Taipei", 25.0375, 121.5735, "TW", "03", 2600000, alternates="Taipei,Taipei City,臺北,台北"
    )

    @pytest.fixture
    def index(self, tmp_path: Path) -> PlaceIndex:
        directory = tmp_path / "geo"
        directory.mkdir()
        (directory / CITIES_FILE).write_text(
            "\n".join([self.SHA_TIN, self.YUEN_LONG, self.MACAU, self.KAOHSIUNG, self.TAIPEI]),
            encoding="utf-8",
        )
        (directory / ADMIN1_FILE).write_text(
            "HK.NST\tSha Tin\nHK.NYL\tYuen Long\nMO.02\tIlhas\nTW.02\tTakao\nTW.03\tTaipei\n",
            encoding="utf-8",
        )
        (directory / COUNTRIES_FILE).write_text(
            "HK\tHKG\t344\tHK\tHong Kong\tVictoria\nMO\tMAC\t446\tMO\tMacao\tVictoria\n"
            "TW\tTWN\t158\tTW\tTaiwan\tTaipei\n",
            encoding="utf-8",
        )
        loaded = PlaceIndex.load(directory)
        assert loaded is not None
        return loaded

    def test_hong_kong_skips_the_province_layer(self, index: PlaceIndex) -> None:
        """香港没有「省」：GeoNames 给的 Sha Tin 是**区**，挂上去就成了「中国 · 沙田区 · 沙田」。"""
        place = index.nearest(22.3823, 114.1891)

        assert place is not None
        assert place.admin1 is None
        assert place.country == "中国"
        assert place.local_city == "沙田"
        assert place.label() == "中国 · 沙田"

    def test_macao_skips_the_province_layer(self, index: PlaceIndex) -> None:
        """澳门给的是堂区（``Nossa Senhora de Fátima``），同样不是省。"""
        place = index.nearest(22.1987, 113.5439)

        assert place is not None
        assert place.admin1 is None
        assert place.country == "中国"
        assert place.label() == "中国 · 澳門"  # 繁体：数据里它排在「澳门」前面

    def test_taiwan_shows_one_province_name_not_a_historical_one(self, index: PlaceIndex) -> None:
        """高雄的省级在数据里是 ``Takao``（旧称）—— 照搬出来是「中国 · Takao · 高雄」。"""
        place = index.nearest(22.6153, 120.3014)

        assert place is not None
        assert place.admin1 == "台湾"
        assert place.country == "中国"
        assert place.label() == "中国 · 台湾 · 高雄"

    def test_taipei_picks_one_of_the_two_chinese_spellings(self, index: PlaceIndex) -> None:
        """台北 / 臺北 都是汉字名，取哪个都对 —— 不做简繁转换（繁体一样认得出）。"""
        place = index.nearest(25.0375, 121.5735)

        assert place is not None
        assert place.admin1 == "台湾"
        assert place.local_city in {"臺北", "台北"}
        assert place.label() == f"中国 · 台湾 · {place.display_city}"

    def test_all_four_share_one_country_name(self, index: PlaceIndex) -> None:
        """按国家分组时，大陆、香港、澳门、台湾落进同一组：它们在 GeoNames 里
        是四个国家代码，在本软件里只算一个国家。"""
        countries = {
            index.nearest(22.3823, 114.1891),
            index.nearest(22.1987, 113.5439),
            index.nearest(22.6153, 120.3014),
        }
        assert {place.country for place in countries if place is not None} == {"中国"}
