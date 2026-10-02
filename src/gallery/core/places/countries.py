"""地名展示用名称：**中国的地点用中文，其余一律用 GeoNames 原名**。

这是一条规则，不是一张翻译表（ADR 0011）。曾经这里放着 190 个国家的译名，
城市名也从 alternate names 里随手取汉字，拼出来是「法国 · Nouvelle-Aquitaine · 波尔多」
这种一层一个语种的东西；而用户在自己的照片上要的恰恰是认得出自己拍过的地方。

于是判断只剩一条：国家是不是 ``CN``。国家、省州、城市三层共用它 ——
``France · Nouvelle-Aquitaine · Bordeaux`` 与 ``United States · New York · New York City``
保持原样，``中国 · 浙江省 · 杭州`` 才译。没有权威译名就不硬翻，
这是省州层早就遵守的规矩，现在国家层也守同一条。

**文件里自带的城市 / 国家名（EXIF、IPTC、XMP）不经过这里**：我们照抄，
绝不翻译、绝不回写（见 ADR 0011 第 4 节）。本模块只管我们反查出来的结论。
"""

from __future__ import annotations

from typing import Final

#: 地点用中文显示的国家。**中国的名字本来就是中文**，翻成 ``China`` 反倒多余。
CHINA_COUNTRY_CODE: Final = "CN"

#: ISO 3166-1 alpha-2 → 中文名。
#:
#: 曾经是一张 190 国的表，现在只剩中国 —— 其余条目不是「暂时没用」，
#: 而是**不该有**：那些译名没有权威来源，是我们自己造的，用户在自己的照片上
#: 认不出自己拍过的地方。要加回来先找得到依据（ADR 0011 第 2 节）。
COUNTRY_NAMES_ZH: Final[dict[str, str]] = {
    "CN": "中国",
}


#: 中国的省级行政区：GeoNames 的英文名 → 中文。
#: GeoNames 的行政区数据只有英文（``Zhejiang``、``Inner Mongolia``），
#: 对中文用户来说把「省」显示成拼音没有意义，所以这一份静态映射随代码走。
#: 键是 GeoNames 的原样写法（注意 ``Shanxi`` 是山西、``Shaanxi`` 是陕西）。
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


def uses_chinese_names(country_code: str) -> bool:
    """这个国家的地点是否用中文显示。

    **三层共用这一条判断**（国家、省州、城市），否则又会拼出「一层一个语种」的话：
    国家中文、省州法文、城市中文（ADR 0011）。调用点在解析城市数据时
    （见 :mod:`gallery.core.places.geonames`），那里已经拿到了国家代码。
    """
    return country_code.strip().upper() == CHINA_COUNTRY_CODE


def admin1_label(country_code: str, name: str) -> str:
    """行政区（省 / 州）名 → 展示用名称。

    只有中国的省级行政区查这张表；其它国家保持 GeoNames 的原名
    （``Nouvelle-Aquitaine``、``California``），我们没有权威译名，硬翻只会更糟。
    """
    if uses_chinese_names(country_code):
        return CHINA_PROVINCES_ZH.get(name.strip(), name)
    return name


def country_label(code: str, fallback: str = "") -> str:
    """国家代码 → 展示用名称：**只有中国翻成中文，其余一律用 ``fallback``**。

    ``fallback`` 是 GeoNames 给的原名（``France``、``Japan``、``United States``）——
    那本来就是用户认得的名字，不需要我们再加工一次（ADR 0011）。
    连 ``fallback`` 都没有时返回代码本身，总之不返回空串：
    界面上一行空白比显示 ``FR`` 更糟。
    """
    normalized = code.strip().upper()
    translated = COUNTRY_NAMES_ZH.get(normalized)
    if translated is not None:
        return translated
    return fallback.strip() or normalized


def place_label(city: str | None, region: str | None, country: str | None) -> str:
    """三段地点 → 一句展示文案：``中国 · 浙江省 · 杭州``（缺的层级自动省略）。

    **索引里存的就是这三列**，元数据面板从库里读出来之后要用同一个函数拼成一句话。
    两处各拼一次，早晚会漂成「面板写着杭州、分组写着 Bordeaux」——
    同一个反查结论（ADR 0010），只该有一种说法。

    这里**不做任何翻译**：三段名字用什么语言，在写入索引之前就定好了
    （见 :func:`admin1_label` 与 :func:`country_label`）。
    """
    parts: list[str] = []
    for part in (country, region, city):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts)


__all__ = [
    "CHINA_COUNTRY_CODE",
    "CHINA_PROVINCES_ZH",
    "COUNTRY_NAMES_ZH",
    "admin1_label",
    "country_label",
    "place_label",
    "uses_chinese_names",
]
