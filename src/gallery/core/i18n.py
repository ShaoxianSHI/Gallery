"""用户可见文案的查表：键 + 参数 → 当前语言的一句话。

文案本身在 :mod:`gallery.core.locales` 里（ADR 0106）。这里只选语言、查表、填参数。
**中文是源语言**：缺键依次回退到它、再到键本身 —— 渲染时不抛异常，完整性由测试锁定。
"""

from __future__ import annotations

from gallery.core.locales import CATALOGS, DEFAULT_LANGUAGE, Message

#: 当前语言。**只在启动时设一次**（ADR 0106 决策 6）：渲染点遍布 core 与 ui，逐个传参不现实。
_current: str = DEFAULT_LANGUAGE


class _KeepMissing(dict[str, object]):
    """缺参数时保留 ``{name}`` 原样，而不是抛 ``KeyError``：界面上少填一个字比一条崩溃好查。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def available_languages() -> tuple[str, ...]:
    """目录里现有的语言代码，源语言排第一。"""
    return tuple(sorted(CATALOGS, key=lambda code: code != DEFAULT_LANGUAGE))


def current_language() -> str:
    """此刻渲染用的语言。"""
    return _current


def set_language(code: str) -> str:
    """切到 ``code``；不认识就留在源语言。返回实际生效的语言代码。"""
    global _current
    _current = code if code in CATALOGS else DEFAULT_LANGUAGE
    return _current


def t(key: str, /, **params: object) -> str:
    """查一条普通文案。缺键回退到源语言，再缺就返回键本身。"""
    message = _lookup(key)
    if message is None:
        return key
    text = message if isinstance(message, str) else message[1]
    return text.format_map(_KeepMissing(params))


def t_plural(key: str, count: int, /, **params: object) -> str:
    """查一条带单复数的文案：目录里是（一份，多份）两元组时按 ``count`` 选一档。"""
    message = _lookup(key)
    if message is None:
        return key
    text = message if isinstance(message, str) else message[0 if count == 1 else 1]
    return text.format_map(_KeepMissing({**params, "count": count}))


def _lookup(key: str) -> Message | None:
    for code in (_current, DEFAULT_LANGUAGE):
        message = CATALOGS.get(code, {}).get(key)
        if message is not None:
            return message
    return None


__all__ = ["available_languages", "current_language", "set_language", "t", "t_plural"]
