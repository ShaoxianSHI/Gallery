"""Google 日历的深链：把「哪一层 + 哪一天」翻译成一个 URL。

**月份是 1 起算的。** 网上大量教程说 Google 日历 URL 里的月份 0-based（``6`` 是七月），
那是错的——本机实测：``/r/month/2026/6/29`` 渲染出来是「2026年6月」，``/r/week/2026/6/29``
渲染出来是「2026年6月28日这周」。写反了会静默地跳到隔壁那个月，而界面上看不出错。

**三层与 Gallery 的三层同名同义**（``year`` / ``month`` / ``week``），因此这里没有
映射表。日期一律由调用方给一个具体的 :class:`datetime.date`：周视图把那一天递过去
即可，Google 自己会算出包含它的那一周——两边的周都从周日起，我们不必再算一遍周数
（``AGENTS.md``：别再引入第三套星期口径）。
"""

from __future__ import annotations

import datetime as dt

#: 深链的站点。与 :func:`~gallery.core.external.pwa.find_chrome_pwa` 认 PWA 时读的那个
#: hostname 是同一个值——**两处必须一致**，否则会开出「在日历应用里打开网页」。
HOST = "calendar.google.com"

#: URL 的前半截。账号序号是可变的：``u/0`` 是第一个登录的 Google 账号。
_BASE_URL = f"https://{HOST}/calendar/u"

#: 日历有的三种带日子的视图。
_DAY_VIEWS = ("week", "month")

#: 全部三层。加一个键就多一种深链，但**要同时想清楚 Google 侧那一层收不收这个参数**。
_VIEWS = ("year", *_DAY_VIEWS)


def deep_link(level: str, day: dt.date, account: int = 0) -> str:
    """「日历停在哪一层、哪一天」对应的 Google 日历 URL。

    :param level: :data:`_VIEWS` 之一。
    :param day: 锚点那一天。年视图不看月日，但仍然要一个（那一年的 1 月 1 日即可）。
    :param account: Google 账号序号，默认第一个。
    :raises ValueError: ``level`` 不认识。

    年视图**只带年份**（``/r/year/2026``）：实测带不带月日都行，而不带的那一份
    渲染出来是干净的年视图。日与周带齐年月日；月视图的日号实测被忽略，但仍然给一个
    落在那个月里的合法值——省得哪天 Google 开始较真时链接指向一个不存在的日子。
    """
    if level not in _VIEWS:
        raise ValueError(f"level 只能是 {_VIEWS} 之一，收到 {level!r}")
    base = f"{_BASE_URL}/{max(int(account), 0)}/r"
    if level == "year":
        return f"{base}/year/{day.year}"
    if level not in _DAY_VIEWS:  # pragma: no cover - 上面已经挡掉了
        raise ValueError(level)
    # **月份原样出去**：`day.month` 本来就是 1 起算的（``datetime`` 的口径），
    # 而 Google 这一边要的也正是 1 起算 —— 这里唯一该做的是别减一。
    return f"{base}/{level}/{day.year}/{day.month}/{day.day}"


__all__ = ["HOST", "deep_link"]
