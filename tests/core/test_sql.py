"""``LIKE`` 转义与「这份库文件还是不是我们打开的那一份」：一份实现，多个领域包共用。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from gallery.core.sql import ESCAPE, file_identity, is_stale_or_locked, like_pattern


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


# ---- 库文件被换掉 / 被占住（ADR 0064）----------------------------------------


def test_file_identity_notices_a_replaced_file(tmp_path: Path) -> None:
    """云盘与「另存为」换的是**另一份同名文件**：路径一样、内容也可能一样，只有身份变了。"""
    target = tmp_path / "x.db"
    target.write_bytes(b"old")
    before = file_identity(target)

    other = tmp_path / "别的.db"
    other.write_bytes(b"new")
    os.replace(other, target)

    assert before is not None
    assert file_identity(target) != before


def test_file_identity_of_a_missing_file_is_none(tmp_path: Path) -> None:
    assert file_identity(tmp_path / "没有这个文件") is None


def test_a_readonly_database_is_worth_reopening() -> None:
    """``attempt to write a readonly database`` 是「换掉的库文件」与「只许读的连接」共用的形状。"""
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA query_only=1")
    try:
        with pytest.raises(sqlite3.Error) as excinfo:
            connection.execute("CREATE TABLE t (a)")
    finally:
        connection.close()

    assert is_stale_or_locked(excinfo.value)


def test_a_plain_sql_mistake_is_not_worth_reopening() -> None:
    """写错 SQL 是程序缺陷，重开一百次也一样 —— 别把它当成能自己好的那一种。"""
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.Error) as excinfo:
            connection.execute("SELECT * FROM 这张表不存在")
    finally:
        connection.close()

    assert not is_stale_or_locked(excinfo.value)
