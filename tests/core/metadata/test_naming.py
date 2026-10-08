"""起名规矩的测试（改名、粘贴、移动到相册共用这一份）。

规则只有一条但很要紧：**绝不覆盖用户已有的文件** —— 这里就是它的单点测试。"""

from __future__ import annotations

from pathlib import Path

from gallery.core.metadata.naming import MAX_NAME_BYTES, free_name_in, split_suffix


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"x")
    return path


def test_split_suffix() -> None:
    assert split_suffix("一张.jpg") == ("一张", ".jpg")
    assert split_suffix("京都") == ("京都", "")
    assert split_suffix(".DS_Store") == (".DS_Store", ""), "以点开头的隐藏文件不拆后缀"
    assert split_suffix("归档.tar.gz") == ("归档.tar", ".gz")


def test_free_name_returns_the_original_when_the_place_is_empty(tmp_path: Path) -> None:
    assert free_name_in(tmp_path, "一张.jpg") == "一张.jpg"


def test_free_name_counts_up_from_two(tmp_path: Path) -> None:
    """编号从 2 起 —— 原名是第一个，「名字 2」才是第二个（与访达一致）。"""
    _touch(tmp_path, "一张.jpg")

    assert free_name_in(tmp_path, "一张.jpg") == "一张 2.jpg"

    _touch(tmp_path, "一张 2.jpg")
    assert free_name_in(tmp_path, "一张.jpg") == "一张 3.jpg"


def test_free_name_works_for_folders_too(tmp_path: Path) -> None:
    """文件夹同理：``京都`` → ``京都 2``。"""
    (tmp_path / "京都").mkdir()

    assert free_name_in(tmp_path, "京都") == "京都 2"


def test_free_name_gives_up_rather_than_truncating(tmp_path: Path) -> None:
    """名字长到加编号也放不下时如实说不行：绝不截断用户的文件名（宁可不做，也不要改坏原名）。"""
    long_stem = "很长的名字" * 40  # 远超 255 字节
    _touch(tmp_path, f"{long_stem}.jpg")

    assert free_name_in(tmp_path, f"{long_stem}.jpg") is None


def test_free_name_counts_bytes_not_characters(tmp_path: Path) -> None:
    """上限是**字节**（APFS / HFS+ 都是 255 字节，与字符数无关）。"""
    # 每个汉字 3 字节，86 个字 = 258 字节，已经超了。
    stem = "字" * 86
    assert len(stem.encode()) > MAX_NAME_BYTES
    _touch(tmp_path, f"{stem}.jpg")

    assert free_name_in(tmp_path, f"{stem}.jpg") is None
    assert free_name_in(tmp_path, "普通名字.jpg") == "普通名字.jpg"
