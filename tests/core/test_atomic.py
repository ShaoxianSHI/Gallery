"""``gallery.core.atomic`` 的测试：写完必须是完整文件，且不留下临时文件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.atomic import read_text_or_none, write_bytes_atomic, write_text_atomic


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"

    write_text_atomic(target, "内容")

    assert target.read_text(encoding="utf-8") == "内容"


def test_write_overwrites_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    write_text_atomic(target, "第一个版本长一些")
    write_text_atomic(target, "短")

    assert target.read_text(encoding="utf-8") == "短"
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]


def test_write_bytes_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "thumb.jpg"

    write_bytes_atomic(target, b"\x00\x01\x02")

    assert target.read_bytes() == b"\x00\x01\x02"


def test_read_text_or_none(tmp_path: Path) -> None:
    assert read_text_or_none(tmp_path / "缺失.txt") is None
    # 目录不是文件，读不了也要返回 None 而不是炸。
    assert read_text_or_none(tmp_path) is None


def test_failed_write_cleans_up_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写失败时不能把半截临时文件留在目标的同级目录里。"""
    import gallery.core.atomic as atomic

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic.os, "replace", boom)

    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "x.txt", "内容")

    assert list(tmp_path.iterdir()) == []
