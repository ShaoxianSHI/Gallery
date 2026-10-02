"""三段地点的拼装，以及中国省级行政区的中文名。

**这里曾经放着 190 个国家的译名表**，城市名也是从备用名里随手取汉字的 —— 拼出来是
「法国 · Nouvelle-Aquitaine · 波尔多」这种一层一个语种的东西。ADR 0011 把规则收敛成
「国内用中文、境外用当地语言」，ADR 0012 把整套判断挪进了城市库的**生成期**，于是
运行时不再需要任何翻译，只剩下面这个拼装函数。

:data:`CHINA_PROVINCES_ZH` 仍然留在这里：它是项目自有的省级中文名表，
``scripts/build_places_db.py`` 生成城市库时要读它 —— **城市库里的省名就是它写进去的**，
所以两边必须用同一份，``tests/core/places/test_countries.py`` 会核对。
"""

from __future__ import annotations

from typing import Final

#: 中国的省级行政区：英文名 → 中文。
#:
#: GeoNames 的行政区数据只有英文（``Zhejiang``、``Inner Mongolia``），
#: 对中文用户来说把「省」显示成拼音没有意义，所以这一份静态映射随代码走。
#: 键是源数据里的原样写法（注意 ``Shanxi`` 是山西、``Shaanxi`` 是陕西）。
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

    **索引里存的就是这三列**，元数据面板从库里读出来之后要用同一个函数拼成一句话。
    两处各拼一次，早晚会漂成「面板写着杭州、分组写着 Bordeaux」——
    同一个反查结论（ADR 0010），只该有一种说法。

    直辖市、香港、澳门没有省级那一层，显示成 ``中国 · 上海``；
    同名的层级（比如某座城市的「市」与「区」同名）只出现一次，重复反而更难读。
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
