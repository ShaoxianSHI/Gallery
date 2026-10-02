"""``gallery.core.places.countries`` 的测试：**只把中国译成中文，其余保持原名**（ADR 0011）。

末尾还有 :func:`place_label` —— 索引里那三列拼成一句话的规则，面板与反查结果
共用同一个函数（ADR 0010），两处说法不许漂。
"""

from __future__ import annotations

import pytest

from gallery.core.places.countries import (
    CHINA_COUNTRY_CODE,
    CHINA_PROVINCES_ZH,
    COUNTRY_NAMES_ZH,
    admin1_label,
    country_label,
    place_label,
    uses_chinese_names,
)


@pytest.mark.parametrize("code", ["CN", "cn", " cn "])
def test_china_is_shown_in_chinese(code: str) -> None:
    assert country_label(code) == "中国"
    assert country_label(code, "China") == "中国"  # 数据文件里写什么都不影响


@pytest.mark.parametrize(
    ("code", "geonames_name"),
    [
        ("FR", "France"),
        ("JP", "Japan"),
        ("US", "United States"),
        ("ES", "Spain"),
        ("HK", "Hong Kong"),
        ("TW", "Taiwan"),
    ],
)
def test_non_china_countries_keep_geonames_names(code: str, geonames_name: str) -> None:
    """没有权威译名就不硬翻：法国的 France 就该写 France。

    连中国香港 / 台湾也不特殊处理 —— 它们不是 ``CN``，规则只认国家代码。
    """
    assert country_label(code, geonames_name) == geonames_name


def test_unknown_code_falls_back_to_geonames_english_name() -> None:
    assert country_label("ZZ", "Neverland") == "Neverland"


def test_unknown_code_without_fallback_returns_the_code() -> None:
    """界面上一行空白比显示 ZZ 更糟，所以最差也要回退到代码本身。"""
    assert country_label("ZZ") == "ZZ"
    assert country_label("ZZ", "   ") == "ZZ"


def test_only_china_uses_chinese_names() -> None:
    """国家、省州、城市三层共用的那一条判断，别的国家一律不是。"""
    assert uses_chinese_names("CN") is True
    assert uses_chinese_names(" cn ") is True
    for code in ("FR", "US", "JP", "HK", "TW", "ZZ", ""):
        assert uses_chinese_names(code) is False, code


def test_country_table_holds_only_china() -> None:
    """译名表曾经有 190 国，现在只留中国 —— 剩下的那些译名没有权威来源。

    这条测试拦住的是「顺手又加回去」：要加别的国家，先问依据在哪（ADR 0011）。
    """
    assert set(COUNTRY_NAMES_ZH) == {CHINA_COUNTRY_CODE}
    for code, name in COUNTRY_NAMES_ZH.items():
        assert len(code) == 2 and code.isupper(), code
        assert name.strip() == name and name, code


def test_chinese_provinces_are_translated() -> None:
    assert admin1_label("CN", "Zhejiang") == "浙江省"
    assert admin1_label("CN", "Inner Mongolia") == "内蒙古自治区"
    # 山西与陕西的拼写只差一个 a，别搞混
    assert admin1_label("CN", "Shanxi") == "山西省"
    assert admin1_label("CN", "Shaanxi") == "陕西省"


def test_provinces_cover_all_mainland_divisions() -> None:
    """22 省 + 5 自治区 + 4 直辖市。"""
    assert len(CHINA_PROVINCES_ZH) == 31
    assert all(name.endswith(("省", "市", "自治区")) for name in CHINA_PROVINCES_ZH.values())


def test_other_countries_keep_geonames_names() -> None:
    """没有权威译名就不硬翻：法国的大区保持 GeoNames 的写法。"""
    assert admin1_label("FR", "Nouvelle-Aquitaine") == "Nouvelle-Aquitaine"
    assert admin1_label("US", "California") == "California"


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
