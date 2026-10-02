"""地名展示用名称：国家代码 → 中文名，以及三段地点的拼装。

GeoNames 的 ``countryInfo.txt`` 只有英文国名，而这是个中文界面的软件。
城市名保持 GeoNames 原名（要中文城市名得额外下载 100 MB+ 的 alternateNames，
不划算），国家名则用这份随代码走的静态映射 —— 它只有几 KB，且几乎不变。

表里没有的代码一律回退到 GeoNames 给的英文名，绝不显示空白或代码本身。
"""

from __future__ import annotations

from typing import Final

#: ISO 3166-1 alpha-2 → 中文名。覆盖常见拍照地，未收录的回退英文名。
COUNTRY_NAMES_ZH: Final[dict[str, str]] = {
    "AD": "安道尔",
    "AE": "阿联酋",
    "AF": "阿富汗",
    "AG": "安提瓜和巴布达",
    "AL": "阿尔巴尼亚",
    "AM": "亚美尼亚",
    "AO": "安哥拉",
    "AR": "阿根廷",
    "AT": "奥地利",
    "AU": "澳大利亚",
    "AZ": "阿塞拜疆",
    "BA": "波黑",
    "BB": "巴巴多斯",
    "BD": "孟加拉国",
    "BE": "比利时",
    "BF": "布基纳法索",
    "BG": "保加利亚",
    "BH": "巴林",
    "BI": "布隆迪",
    "BJ": "贝宁",
    "BN": "文莱",
    "BO": "玻利维亚",
    "BR": "巴西",
    "BS": "巴哈马",
    "BT": "不丹",
    "BW": "博茨瓦纳",
    "BY": "白俄罗斯",
    "BZ": "伯利兹",
    "CA": "加拿大",
    "CD": "刚果（金）",
    "CF": "中非",
    "CG": "刚果（布）",
    "CH": "瑞士",
    "CI": "科特迪瓦",
    "CL": "智利",
    "CM": "喀麦隆",
    "CN": "中国",
    "CO": "哥伦比亚",
    "CR": "哥斯达黎加",
    "CU": "古巴",
    "CV": "佛得角",
    "CY": "塞浦路斯",
    "CZ": "捷克",
    "DE": "德国",
    "DJ": "吉布提",
    "DK": "丹麦",
    "DO": "多米尼加",
    "DZ": "阿尔及利亚",
    "EC": "厄瓜多尔",
    "EE": "爱沙尼亚",
    "EG": "埃及",
    "ER": "厄立特里亚",
    "ES": "西班牙",
    "ET": "埃塞俄比亚",
    "FI": "芬兰",
    "FJ": "斐济",
    "FM": "密克罗尼西亚",
    "FO": "法罗群岛",
    "FR": "法国",
    "GA": "加蓬",
    "GB": "英国",
    "GD": "格林纳达",
    "GE": "格鲁吉亚",
    "GH": "加纳",
    "GI": "直布罗陀",
    "GL": "格陵兰",
    "GM": "冈比亚",
    "GN": "几内亚",
    "GR": "希腊",
    "GT": "危地马拉",
    "GU": "关岛",
    "GY": "圭亚那",
    "HK": "中国香港",
    "HN": "洪都拉斯",
    "HR": "克罗地亚",
    "HT": "海地",
    "HU": "匈牙利",
    "ID": "印度尼西亚",
    "IE": "爱尔兰",
    "IL": "以色列",
    "IM": "马恩岛",
    "IN": "印度",
    "IQ": "伊拉克",
    "IR": "伊朗",
    "IS": "冰岛",
    "IT": "意大利",
    "JM": "牙买加",
    "JO": "约旦",
    "JP": "日本",
    "KE": "肯尼亚",
    "KG": "吉尔吉斯斯坦",
    "KH": "柬埔寨",
    "KP": "朝鲜",
    "KR": "韩国",
    "KW": "科威特",
    "KZ": "哈萨克斯坦",
    "LA": "老挝",
    "LB": "黎巴嫩",
    "LI": "列支敦士登",
    "LK": "斯里兰卡",
    "LR": "利比里亚",
    "LS": "莱索托",
    "LT": "立陶宛",
    "LU": "卢森堡",
    "LV": "拉脱维亚",
    "LY": "利比亚",
    "MA": "摩洛哥",
    "MC": "摩纳哥",
    "MD": "摩尔多瓦",
    "ME": "黑山",
    "MG": "马达加斯加",
    "MK": "北马其顿",
    "ML": "马里",
    "MM": "缅甸",
    "MN": "蒙古",
    "MO": "中国澳门",
    "MT": "马耳他",
    "MU": "毛里求斯",
    "MV": "马尔代夫",
    "MW": "马拉维",
    "MX": "墨西哥",
    "MY": "马来西亚",
    "MZ": "莫桑比克",
    "NA": "纳米比亚",
    "NC": "新喀里多尼亚",
    "NE": "尼日尔",
    "NG": "尼日利亚",
    "NI": "尼加拉瓜",
    "NL": "荷兰",
    "NO": "挪威",
    "NP": "尼泊尔",
    "NZ": "新西兰",
    "OM": "阿曼",
    "PA": "巴拿马",
    "PE": "秘鲁",
    "PF": "法属波利尼西亚",
    "PG": "巴布亚新几内亚",
    "PH": "菲律宾",
    "PK": "巴基斯坦",
    "PL": "波兰",
    "PR": "波多黎各",
    "PS": "巴勒斯坦",
    "PT": "葡萄牙",
    "PY": "巴拉圭",
    "QA": "卡塔尔",
    "RE": "留尼汪",
    "RO": "罗马尼亚",
    "RS": "塞尔维亚",
    "RU": "俄罗斯",
    "RW": "卢旺达",
    "SA": "沙特阿拉伯",
    "SC": "塞舌尔",
    "SD": "苏丹",
    "SE": "瑞典",
    "SG": "新加坡",
    "SI": "斯洛文尼亚",
    "SK": "斯洛伐克",
    "SL": "塞拉利昂",
    "SM": "圣马力诺",
    "SN": "塞内加尔",
    "SO": "索马里",
    "SR": "苏里南",
    "SS": "南苏丹",
    "SV": "萨尔瓦多",
    "SY": "叙利亚",
    "SZ": "斯威士兰",
    "TD": "乍得",
    "TG": "多哥",
    "TH": "泰国",
    "TJ": "塔吉克斯坦",
    "TM": "土库曼斯坦",
    "TN": "突尼斯",
    "TO": "汤加",
    "TR": "土耳其",
    "TT": "特立尼达和多巴哥",
    "TW": "中国台湾",
    "TZ": "坦桑尼亚",
    "UA": "乌克兰",
    "UG": "乌干达",
    "US": "美国",
    "UY": "乌拉圭",
    "UZ": "乌兹别克斯坦",
    "VA": "梵蒂冈",
    "VE": "委内瑞拉",
    "VG": "英属维尔京群岛",
    "VN": "越南",
    "YE": "也门",
    "ZA": "南非",
    "ZM": "赞比亚",
    "ZW": "津巴布韦",
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


def admin1_label(country_code: str, name: str) -> str:
    """行政区（省 / 州）名 → 展示用名称。

    目前只翻译中国的省级行政区；其它国家保持 GeoNames 的原名，
    我们没有权威译名，硬翻只会更糟。
    """
    if country_code.strip().upper() == "CN":
        return CHINA_PROVINCES_ZH.get(name.strip(), name)
    return name


def country_label(code: str, fallback: str = "") -> str:
    """国家代码 → 展示用名称；表里没有就用 ``fallback``（GeoNames 的英文名）。

    两者都没有时返回代码本身，总之不返回空串 —— 界面上一行空白比显示 ``FR`` 更糟。
    """
    normalized = code.strip().upper()
    return COUNTRY_NAMES_ZH.get(normalized) or fallback.strip() or normalized


def place_label(city: str | None, region: str | None, country: str | None) -> str:
    """三段地点 → 一句展示文案：``中国 · 浙江省 · 杭州``（缺的层级自动省略）。

    **索引里存的就是这三列**，元数据面板从库里读出来之后要用同一个函数拼成一句话。
    两处各拼一次，早晚会漂成「面板写着杭州、分组写着 Bordeaux」——
    同一个反查结论（ADR 0010），只该有一种说法。
    """
    parts: list[str] = []
    for part in (country, region, city):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts)


__all__ = [
    "CHINA_PROVINCES_ZH",
    "COUNTRY_NAMES_ZH",
    "admin1_label",
    "country_label",
    "place_label",
]
