"""按拼音给名字排先后。

``str.casefold()`` 只比较 Unicode 码点：汉字因此按部首与笔画排，与用户按拼音翻名字的习惯
不一致。这里给出一个**只用于比较**的键：汉字转成不带声调的拼音，拉丁字母照旧。

键里额外带上原名字只为同音字之间稳定，键本身不保证可读。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

from pypinyin import Style, lazy_pinyin

#: 需要转成拼音的字符范围：基本区、扩展 A–H、兼容区。
_CJK: Final = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]")


@lru_cache(maxsize=8192)
def collation_key(text: str) -> str:
    """名字的排序键：按拼音（不带声调）比较，同音字再按原字排。

    纯拉丁的名字走 ``casefold`` 快路径 —— 它们的码点顺序本来就是用户期待的顺序。
    """
    folded = text.casefold()
    if _CJK.search(folded) is None:
        return folded
    reading = "".join(lazy_pinyin(folded, style=Style.NORMAL))
    return f"{reading}\x00{folded}"


__all__ = ["collation_key"]
