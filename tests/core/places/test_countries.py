"""``gallery.core.places.countries`` 的测试：**国内译成中文，其余保持原名**（ADR 0011）。

末尾还有 :func:`place_label` —— 索引里那三列拼成一句话的规则，面板与反查结果
共用同一个函数（ADR 0010），两处说法不许漂。
"""

from __future__ import annotations

import pytest

from gallery.core.places.countries import (
    CHINA_COUNTRY_CODE,
    CHINA_PROVINCES_ZH,
    COUNTRY_NAMES_ZH,
    NO_ADMIN1_COUNTRY_CODES,
    TAIWAN_COUNTRY_CODE,
    admin1_label,
    country_label,
    place_label,
    uses_chinese_names,
)

#: 用中文显示的四个国家代码（大陆 + 港澳台）。
CHINESE_NAME_CODES = ("CN", "HK", "MO", "TW")


@pytest.mark.parametrize("code", CHINESE_NAME_CODES)
def test_domestic_places_are_shown_in_chinese(code: str) -> None:
    """大陆与港澳台的国名都是「中国」：它们在 GeoNames 里是四个独立的国家代码。"""
    assert country_label(code) == "中国"
    assert country_label(code, "Hong Kong") == "中国"  # 数据文件里写什么都不影响


@pytest.mark.parametrize(
    ("code", "geonames_name"),
    [
        ("FR", "France"),
        ("JP", "Japan"),
        ("US", "United States"),
        ("ES", "Spain"),
        ("DE", "Germany"),
        ("GB", "United Kingdom"),
    ],
)
def test_other_countries_keep_geonames_names(code: str, geonames_name: str) -> None:
    """没有权威译名就不硬翻：法国的 France 就该写 France。"""
    assert country_label(code, geonames_name) == geonames_name


def test_unknown_code_falls_back_to_geonames_english_name() -> None:
    assert country_label("ZZ", "Neverland") == "Neverland"


def test_unknown_code_without_fallback_returns_the_code() -> None:
    """界面上一行空白比显示 ZZ 更糟，所以最差也要回退到代码本身。"""
    assert country_label("ZZ") == "ZZ"
    assert country_label("ZZ", "   ") == "ZZ"


@pytest.mark.parametrize("code", CHINESE_NAME_CODES)
def test_domestic_places_use_chinese_names(code: str) -> None:
    assert uses_chinese_names(code) is True


def test_abroad_places_do_not_use_chinese_names() -> None:
    """国名与城市名共用这一条判断，境外一律不是。"""
    for code in ("FR", "US", "JP", "ES", "ZZ", ""):
        assert uses_chinese_names(code) is False, code
    assert uses_chinese_names(" cn ") is True  # 大小写与空格照旧宽容


def test_country_table_holds_only_domestic_codes() -> None:
    """译名表曾经有 190 国，现在只剩这四个 —— 剩下的那些译名没有权威来源。

    这条测试拦住的是「顺手又加回去」：要加别的国家，先问依据在哪（ADR 0011）。
    """
    assert set(COUNTRY_NAMES_ZH) == set(CHINESE_NAME_CODES)
    for code, name in COUNTRY_NAMES_ZH.items():
        assert len(code) == 2 and code.isupper(), code
        assert name.strip() == name and name, code


def test_chinese_provinces_are_translated() -> None:
    assert admin1_label(CHINA_COUNTRY_CODE, "Zhejiang") == "浙江省"
    assert admin1_label("CN", "Inner Mongolia") == "内蒙古自治区"
    # 山西与陕西的拼写只差一个 a，别搞混
    assert admin1_label("CN", "Shanxi") == "山西省"
    assert admin1_label("CN", "Shaanxi") == "陕西省"


def test_provinces_cover_all_mainland_divisions() -> None:
    """22 省 + 5 自治区 + 4 直辖市。"""
    assert len(CHINA_PROVINCES_ZH) == 31
    assert all(name.endswith(("省", "市", "自治区")) for name in CHINA_PROVINCES_ZH.values())


def test_other_countries_keep_geonames_admin1_names() -> None:
    """没有权威译名就不硬翻：法国的大区保持 GeoNames 的写法。"""
    assert admin1_label("FR", "New Aquitaine") == "New Aquitaine"
    assert admin1_label("US", "California") == "California"


def test_hong_kong_and_macao_have_no_province_layer() -> None:
    """GeoNames 在这两地给的是**区级**名字，挂上去只会让人以为那是个省。

    空串会被 :meth:`PlaceIndex.nearest` 当成「没有这一层」省掉，
    结果是「中国 · 沙田」而不是「中国 · 沙田区 · 沙田」。
    """
    assert admin1_label("HK", "Sha Tin") == ""
    assert admin1_label("HK", "Yuen Long") == ""
    assert admin1_label("MO", "Nossa Senhora de Fátima") == ""
    assert admin1_label("MO", "") == ""
    assert frozenset({"HK", "MO"}) == NO_ADMIN1_COUNTRY_CODES


@pytest.mark.parametrize("historical", ["Takao", "Fukien", "Taipei", "Taiwan", ""])
def test_taiwan_always_shows_one_province_name(historical: str) -> None:
    """GeoNames 那几个是历史旧名（高雄旧称 Takau、福建旧称 Fukien）。

    照搬出来是「中国 · Takao · 高雄」，所以那一层一律显示「台湾」。
    """
    assert admin1_label(TAIWAN_COUNTRY_CODE, historical) == "台湾"
    assert admin1_label(" tw ", historical) == "台湾"


def test_admin1_label_is_forgiving_about_case_and_spaces() -> None:
    assert admin1_label("CN", " Zhejiang ") == "浙江省"
    assert admin1_label("CN", "表里没有的名字") == "表里没有的名字"


# ---- 三段地点拼成一句话 ---------------------------------------------------------------


def test_place_label_goes_from_big_to_small() -> None:
    assert place_label("杭州", "浙江省", "中国") == "中国 · 浙江省 · 杭州"


def test_place_label_skips_what_is_missing() -> None:
    """缺哪层就省掉哪层，绝不留下空洞的「中国 ·  · 杭州」。"""
    assert place_label("杭州", None, "中国") == "中国 · 杭州"
    assert place_label(None, None, "France") == "France"
    assert place_label("Bordeaux", None, "France") == "France · Bordeaux"


def test_place_label_is_empty_only_when_nothing_is_known() -> None:
    assert place_label(None, None, None) == ""


def test_place_label_does_not_repeat_a_name() -> None:
    """香港这种「市」与「区」同名的层级，重复一遍反而更难读。"""
    assert place_label("香港", "香港", "中国") == "中国 · 香港"
    assert place_label("香港", None, "中国") == "中国 · 香港"
