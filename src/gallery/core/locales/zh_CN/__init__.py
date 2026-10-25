"""中文：源语言。

这里的值就是界面原先那句话，迁移时**标点、空格、「」、⌘ 一个字都不改**（ADR 0106 决策 2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import DomainMessages, Message, merge
from gallery.core.locales.zh_CN import banner

DOMAINS: Final[Mapping[str, DomainMessages]] = {"banner": banner.MESSAGES}
MESSAGES: Final[Mapping[str, Message]] = merge(DOMAINS)

__all__ = ["DOMAINS", "MESSAGES"]
