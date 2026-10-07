"""``LIKE`` 转义：一份实现，两个领域包共用（``library.search`` 与 ``events.store``）。"""

from __future__ import annotations

from gallery.core.sql import ESCAPE, like_pattern


def test_a_percent_is_escaped() -> None:
    assert like_pattern("50%") == f"50{ESCAPE}%"


def test_an_underscore_is_escaped() -> None:
    """``IMG_0001`` 里的 ``_`` 不转义就会顶替任何字符，于是 ``IMGX0001`` 也会命中。"""
    assert like_pattern("IMG_0001") == f"IMG{ESCAPE}_0001"


def test_the_escape_character_itself_is_escaped_first() -> None:
    """反斜杠必须**先**转义，否则后一步会把刚加上的那一个又转一遍。"""
    assert like_pattern("a\\b") == f"a{ESCAPE}{ESCAPE}b"
    assert like_pattern("a\\%") == f"a{ESCAPE}{ESCAPE}{ESCAPE}%"


def test_the_tail_is_appended_without_being_escaped() -> None:
    """``tail`` 是查询自己要的那个通配符，转义掉它就等于查不到。"""
    assert like_pattern("相册 A", "/%") == "相册 A/%"


def test_no_wildcard_in_the_text_stays_as_is() -> None:
    assert like_pattern("海边") == "海边"
