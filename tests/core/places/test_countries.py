"""``gallery.core.places.countries`` 的测试：省级中文名表，以及三段地点的拼装。

翻译规则本身已经不在运行时了（ADR 0011 / 0012），但 :data:`CHINA_PROVINCES_ZH`
还在 —— ``scripts/build_places_db.py`` 生成城市库时要读它，**城市库里的省名就是它
写进去的**，所以两边必须用同一份，这里就是那道闸。

末尾的 :func:`place_label` 是索引里那三列拼成一句话的规则，面板与反查结果共用同一个
函数（ADR 0010），两处说法不许漂。
"""

from __future__ import annotations

import pytest

from gallery.core.places.countries import CHINA_PROVINCES_ZH, place_label


def test_table_is_well_formed() -> None:
    for code, name in CHINA_PROVINCES_ZH.items():
        assert name.strip() == name and name, code


def test_provinces_cover_all_mainland_divisions() -> None:
    """22 省 + 5 自治区 + 4 直辖市。"""
    assert len(CHINA_PROVINCES_ZH) == 31
    assert all(name.endswith(("省", "市", "自治区")) for name in CHINA_PROVINCES_ZH.values())


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Zhejiang", "浙江省"),
        ("Inner Mongolia", "内蒙古自治区"),
        # 山西与陕西的拼写只差一个 a，别搞混
        ("Shanxi", "山西省"),
        ("Shaanxi", "陕西省"),
        ("Guangxi", "广西壮族自治区"),
    ],
)
def test_known_province_names(key: str, expected: str) -> None:
    assert CHINA_PROVINCES_ZH[key] == expected


# ---- 三段地点拼成一句话 ---------------------------------------------------------------


def test_place_label_goes_from_big_to_small() -> None:
    assert place_label("杭州", "浙江省", "中国") == "中国 · 浙江省 · 杭州"


def test_place_label_skips_what_is_missing() -> None:
    """缺哪层就省掉哪层，绝不留下空洞的「中国 ·  · 杭州」。"""
    assert place_label("杭州", None, "中国") == "中国 · 杭州"
    assert place_label("Bordeaux", None, "France") == "France · Bordeaux"


def test_place_label_is_empty_only_when_nothing_is_known() -> None:
    assert place_label(None, None, None) == ""


def test_place_label_does_not_repeat_a_name() -> None:
    """同名的层级（比如某座城市的「市」与「区」同名）重复一遍反而更难读。"""
    assert place_label("香港", "香港", "中国") == "中国 · 香港"
