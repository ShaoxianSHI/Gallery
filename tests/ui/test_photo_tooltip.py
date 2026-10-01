"""``gallery.ui.formatting`` 的测试：卡片悬停提示。

文件大小与时间的格式化已经下沉到 ``core``，测试在 ``tests/core/test_formatting.py``。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core.photos import PhotoRef
from gallery.ui.formatting import photo_tooltip


def test_photo_tooltip_contains_the_useful_fields(tmp_path: Path) -> None:
    ref = PhotoRef(path=tmp_path / "2024" / "IMG_0001.HEIC", size=2 * 1024**2, mtime_ns=0)

    tooltip = photo_tooltip(ref)

    assert "IMG_0001.HEIC" in tooltip
    assert "HEIC" in tooltip
    assert "2.0 MB" in tooltip
    assert str(tmp_path / "2024") in tooltip
