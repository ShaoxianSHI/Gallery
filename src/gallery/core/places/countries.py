"""地名展示用名称：**中国的地点用中文，其余一律用 GeoNames 原名**。

这是一条规则，不是一张翻译表（ADR 0011）。曾经这里放着 190 个国家的译名，
城市名也从 alternate names 里随手取汉字，拼出来是「法国 · New Aquitaine · 波尔多」
这种一层一个语种的东西；而用户在自己的照片上要的恰恰是认得出自己拍过的地方。

于是判断只剩一条：国家代码在不在 :data:`COUNTRY_NAMES_ZH` 里。
``France · New Aquitaine · Bordeaux`` 与 ``United States · New York · New York City``
保持原样，``中国 · 浙江省 · 杭州`` 才译。没有权威译名就不硬翻，
这是省州层早就遵守的规矩，现在国家层也守同一条。

中文名覆盖中国大陆与港澳台，四者国名都是「中国」，差别在下面两层：

==================  ==================  ===============
国名                 省级                 城市
==================  ==================  ===============
中国                 浙江省               杭州
中国                 **无此层**（港澳本   香港 / 澳门
                    来就没有「省」）
中国                 台湾                 高雄 / 台北
==================  ==================  ===============

**文件里自带的城市 / 国家名（EXIF、IPTC、XMP）不经过这里**：我们照抄，
绝不翻译、绝不回写（见 ADR 0011 第 4 节）。本模块只管我们反查出来的结论。
"""

from __future__ import annotations

from typing import Final

#: ISO 3166-1 alpha-2 → 界面上显示的国名。**这个表就是「用不用中文」的唯一依据**：
#: 在表里就用中文，不在表里就用 GeoNames 原名。
#:
#: 曾经是一张 190 国的表，现在只剩这四个 —— 其余条目不是「暂时没用」，
#: 而是**不该有**：那些译名没有权威来源，是我们自己造的，用户在自己的照片上
#: 认不出自己拍过的地方。要加回来先找得到依据（ADR 0011 第 2 节）。
#:
#: 港澳台与大陆的国名都是「中国」：它们在 GeoNames 里是四个独立的国家代码，
#: 但在本软件里只算一个国家，按国家分组时也会落进同一组。
COUNTRY_NAMES_ZH: Final[dict[str, str]] = {
    "CN": "中国",
    "HK": "中国",
    "MO": "中国",
    "TW": "中国",
}

#: 中国大陆：省级查 :data:`CHINA_PROVINCES_ZH`。
CHINA_COUNTRY_CODE: Final = "CN"

#: 台湾：GeoNames 给的省级是**历史旧名**（``Takao``、``Fukien``、``Taipei``，
#: 分别是高雄、福佬、台北的旧称），照搬出来是「中国 · Takao · 高雄」。
#: 那一层一律显示「台湾」。
TAIWAN_COUNTRY_CODE: Final = "TW"
TAIWAN_PROVINCE_ZH: Final = "台湾"

#: 没有省级这一层的地方：香港、澳门。
#:
#: GeoNames 在这两地给的是**区级**名字（香港 17 区、澳门 7 堂区），
#: 夹在国名与城市之间只会让人以为那是个省 —— 直接省掉这一层，
#: 结果就是「中国 · 香港」「中国 · 澳门」。
NO_ADMIN1_COUNTRY_CODES: Final[frozenset[str]] = frozenset({"HK", "MO"})


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
    """这个国家的地点是否用中文显示（中国大陆、香港、澳门、台湾）。

    **国名与城市名共用这一条判断**，否则又会拼出「一层一个语种」的话：
    国家中文、省州法文、城市中文（ADR 0011）。省级另作处理，见 :func:`admin1_label`。
    调用点在解析城市数据时（见 :mod:`gallery.core.places.geonames`），那里已经拿到了国家代码。
    """
    return country_code.strip().upper() in COUNTRY_NAMES_ZH


def admin1_label(country_code: str, name: str) -> str:
    """行政区（省 / 州）名 → 展示用名称，**空串表示这一层不存在**。

    - **大陆**：查 :data:`CHINA_PROVINCES_ZH`，查不到就保持 GeoNames 原名；
    - **港澳**：返回空串。GeoNames 给的是区级名字（「荃湾区」「大堂区」），
      夹在国名与城市之间只会让人以为那是个省；
    - **台湾**：一律返回「台湾」。GeoNames 那几个是历史旧名（``Takao``、``Fukien``），
      照搬出来是「中国 · Takao · 高雄」；
    - **其它国家**：保持 GeoNames 的原名（``New Aquitaine``、``California``），
      我们没有权威译名，硬翻只会更糟。
    """
    code = country_code.strip().upper()
    if code == CHINA_COUNTRY_CODE:
        return CHINA_PROVINCES_ZH.get(name.strip(), name)
    if code in NO_ADMIN1_COUNTRY_CODES:
        return ""
    if code == TAIWAN_COUNTRY_CODE:
        return TAIWAN_PROVINCE_ZH
    return name


def country_label(code: str, fallback: str = "") -> str:
    """国家代码 → 展示用名称：**表里有的翻成中文，其余一律用 ``fallback``**。

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
    "NO_ADMIN1_COUNTRY_CODES",
    "TAIWAN_COUNTRY_CODE",
    "TAIWAN_PROVINCE_ZH",
    "admin1_label",
    "country_label",
    "place_label",
    "uses_chinese_names",
]
