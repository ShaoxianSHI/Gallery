"""文案目录：每种语言一包，领域模块住在包里。

键空间是**一个**：语言之间必须一一对应，由 ``tests/core/locales/test_catalogs.py`` 逐领域比较。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales import en, zh_CN
from gallery.core.locales._catalog import DomainMessages, Message

#: 源语言：它的值就是界面原先那句话，迁移时逐字不动。
DEFAULT_LANGUAGE: Final = "zh_CN"

#: 语言代码 → 它自己的名字。**不翻译**：语言选择器里两种语言并排写自己的名字。
LANGUAGE_NAMES: Final[Mapping[str, str]] = {"zh_CN": "简体中文", "en": "English"}

CATALOGS: Final[Mapping[str, Mapping[str, Message]]] = {
    "zh_CN": zh_CN.MESSAGES,
    "en": en.MESSAGES,
}

__all__ = ["CATALOGS", "DEFAULT_LANGUAGE", "LANGUAGE_NAMES", "DomainMessages", "Message"]
