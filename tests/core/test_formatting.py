"""``gallery.core.formatting`` 的测试：界面与元数据面板共用的人读格式化。"""

from __future__ import annotations

from datetime import datetime

import pytest

from gallery.core.formatting import format_bytes, format_timestamp


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (5 * 1024**4, "5.0 TB"),
    ],
)
def test_format_bytes(count: int, expected: str) -> None:
    assert format_bytes(count) == expected


def test_format_bytes_rejects_negative() -> None:
    assert format_bytes(-1) == "未知大小"


def test_format_timestamp_is_local_time() -> None:
    stamp = datetime(2026, 10, 1, 12, 38).timestamp()

    assert format_timestamp(int(stamp * 1_000_000_000)) == "2026-10-01 12:38"
