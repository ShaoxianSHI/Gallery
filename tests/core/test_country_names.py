"""``gallery.core.country_names`` 的测试：国家代码到中文名，且永不返回空串。"""

from __future__ import annotations

import pytest

from gallery.core.country_names import COUNTRY_NAMES_ZH, country_label


@pytest.mark.parametrize(
    ("code", "expected"),
    [("FR", "法国"), ("JP", "日本"), ("US", "美国"), ("CN", "中国"), ("ES", "西班牙")],
)
def test_known_codes_map_to_chinese(code: str, expected: str) -> None:
    assert country_label(code) == expected


def test_unknown_code_falls_back_to_geonames_english_name() -> None:
    assert country_label("ZZ", "Neverland") == "Neverland"


def test_unknown_code_without_fallback_returns_the_code() -> None:
    """界面上一行空白比显示 ZZ 更糟，所以最差也要回退到代码本身。"""
    assert country_label("ZZ") == "ZZ"
    assert country_label("ZZ", "   ") == "ZZ"


def test_lookup_is_forgiving_about_case_and_spaces() -> None:
    assert country_label(" fr ") == "法国"


def test_table_is_well_formed() -> None:
    assert len(COUNTRY_NAMES_ZH) >= 100
    for code, name in COUNTRY_NAMES_ZH.items():
        assert len(code) == 2 and code.isupper(), code
        assert name.strip() == name and name, code
