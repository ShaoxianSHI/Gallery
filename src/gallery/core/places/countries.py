"""三段地点的拼装，以及中国省级行政区的中文名。

ADR 0011 把规则收敛成「国内用中文、境外用当地语言」，ADR 0012 把整套判断挪进了城市库的
**生成期**，于是运行时不再需要翻译，只剩这个拼装函数。:data:`CHINA_PROVINCES_ZH` 留在这里，
``scripts/build_places_db.py`` 生成城市库时要读它，两边必须用同一份
（``tests/core/places/test_countries.py`` 会核对）。
"""

from __future__ import annotations

from typing import Final

#: 中国的省级行政区：英文名 → 中文。源数据（GeoNames）只有英文，对中文用户来说把「省」
#: 显示成拼音没有意义，所以这一份静态映射随代码走。键是源数据里的原样写法（注意 ``Shanxi``
#: 是山西、``Shaanxi`` 是陕西）。
CHINA_PROVINCES_ZH: Final[dict[str, str]] = {
    "Anhui": "安徽省",
    "Beijing": "北京市",
    "Chongqing": "重庆市",
    "Fujian": "福建省",
    "Gansu": "甘肃省",
    "Guangdong": "广东省",
    "Guangxi": "广西壮族自治区",
    "Guizhou": "贵州省",
    "Hainan": "海南省",
    "Hebei": "河北省",
    "Heilongjiang": "黑龙江省",
    "Henan": "河南省",
    "Hubei": "湖北省",
    "Hunan": "湖南省",
    "Inner Mongolia": "内蒙古自治区",
    "Jiangsu": "江苏省",
    "Jiangxi": "江西省",
    "Jilin": "吉林省",
    "Liaoning": "辽宁省",
    "Ningxia": "宁夏回族自治区",
    "Qinghai": "青海省",
    "Shaanxi": "陕西省",
    "Shandong": "山东省",
    "Shanghai": "上海市",
    "Shanxi": "山西省",
    "Sichuan": "四川省",
    "Tianjin": "天津市",
    "Tibet": "西藏自治区",
    "Xinjiang": "新疆维吾尔自治区",
    "Yunnan": "云南省",
    "Zhejiang": "浙江省",
}


def place_label(city: str | None, region: str | None, country: str | None) -> str:
    """三段地点 → 一句展示文案：``中国 · 浙江省 · 杭州``（缺的层级自动省略）。

    索引里存的就是这三列，元数据面板要从库里读出来并拼出**同一句话**，否则早晚漂成「面板写着
    杭州、分组写着 Bordeaux」。直辖市、香港、澳门没有省级那层，显示成 ``中国 · 上海``；同名的
    层级只出现一次，重复反而更难读。
    """
    parts: list[str] = []
    for part in (country, region, city):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts)


__all__ = [
    "CHINA_PROVINCES_ZH",
    "place_label",
]
