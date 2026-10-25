"""文案目录的类型与合并：领域模块 → 一张键表。

每种语言的 ``__init__`` 用 :func:`merge` 把领域模块拼起来。**键必须带自己的领域前缀**，
于是「同一句话在两类语境里各有一份」只能靠两个不同的键表达，不会互相覆盖。
"""

from __future__ import annotations

from collections.abc import Mapping

#: 一条文案：普通句子，或（一份，多份）两档。
type Message = str | tuple[str, str]
#: 一个领域模块里的全部文案：键一律以领域名开头。
type DomainMessages = Mapping[str, Message]


def merge(domains: Mapping[str, DomainMessages]) -> dict[str, Message]:
    """把领域模块拼成一张表；键不以领域名开头就直接报错。"""
    merged: dict[str, Message] = {}
    for domain, messages in domains.items():
        for key, value in messages.items():
            if not key.startswith(f"{domain}."):
                raise ValueError(f"文案键 {key} 不属于领域 {domain}")
            merged[key] = value
    return merged
