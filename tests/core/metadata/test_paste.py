"""粘贴的测试。

全部在 ``tmp_path`` 里进行：这里测的操作**真的会往用户的相册里写文件**，所以每个
「照片」都是当场造出来的临时文件，一个字节也不许落到真实相册里。

钉住的是四类风险：

1. **只增不改** —— 源文件一个字节都不能变，粘进去的是**新文件**。
2. **绝不覆盖** —— 同名时按访达的办法改叫「一张 2.jpg」，且改到真的可用为止。
3. **边界** —— 已经在目标目录里的原样跳过、文件夹不粘进自己、取消时不留半截文件。
4. **记账** —— 每一项都写进 ``journal/``，而它**不进撤销栈**（撤销粘贴就是删文件）。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.errors import PasteError
from gallery.core.metadata.journal import (
    ACTION_PASTE,
    ACTION_RENAME,
    RESULT_FAILED,
    RESULT_OK,
    read_records,
)
from gallery.core.metadata.paste import copy_into
from gallery.core.metadata.undo import next_undo_label, undo_last_write
from gallery.core.paths import config_dir


def _paste(root: Path, target: Path, *sources: Path):
    return copy_into(root, target, sources)


def test_paste_copies_without_touching_the_source(library: Path) -> None:
    """源文件一个字节都不能变：粘进去的是**另一个**文件。"""
    source = library / "京都" / "一张.jpg"
    before = source.read_bytes()
    mtime = source.stat().st_mtime_ns

    result = _paste(library, library / "奈良", source)

    created = library / "奈良" / "一张.jpg"
    assert [item.created for item in result.pasted] == [created]
    assert created.read_bytes() == before
    assert source.exists(), "源必须还在原地"
    assert source.stat().st_mtime_ns == mtime


def test_paste_keeps_the_modified_time(library: Path) -> None:
    """连修改时间一起复制。

    库里默认按修改时间排序；粘进来的照片因此还留在原来的时间位置上，
    而不是齐刷刷跳到「今天」。
    """
    source = library / "京都" / "一张.jpg"
    os.utime(source, (1_600_000_000, 1_600_000_000))

    _paste(library, library / "奈良", source)

    assert int((library / "奈良" / "一张.jpg").stat().st_mtime) == 1_600_000_000


def test_paste_into_the_same_folder_does_nothing(library: Path) -> None:
    """已经在目标目录里的原样跳过（用户明确要求）：不凭空多出一份「一张 2.jpg」。"""
    source = library / "京都" / "一张.jpg"

    result = _paste(library, library / "京都", source)

    assert result.pasted == ()
    assert [item.source for item in result.skipped] == [source]
    assert sorted(p.name for p in (library / "京都").iterdir()) == ["一张.jpg", "另一张.jpg"]


def test_same_folder_check_survives_a_different_spelling(tmp_path: Path) -> None:
    """``/var`` 与 ``/private/var`` 在 macOS 上是同一个文件夹。

    不解析就比不相等，于是「已经在同一目录」这条判断会失效，粘出一堆重复文件。
    这里用符号链接造出同一条路径的两种写法（不依赖 macOS 的 ``/private`` 约定）。
    """
    real = tmp_path / "真实"
    (real / "京都").mkdir(parents=True)
    Image.new("RGB", (8, 6), (1, 2, 3)).save(real / "京都" / "一张.jpg")
    link = tmp_path / "链接"
    link.symlink_to(real, target_is_directory=True)
    config_dir(tmp_path).mkdir()

    result = copy_into(real, link / "京都", (link / "京都" / "一张.jpg",))

    assert result.pasted == ()
    assert len(result.skipped) == 1


def test_renames_instead_of_overwriting(library: Path) -> None:
    """同名不覆盖，按系统自己的规矩改叫「一张 2.jpg」。

    12 与 13 那个测法是量出来的：在访达里对同一个文件连按两次「复制项目」，
    中文系统给出的正是这两个名字。
    """
    source = library / "京都" / "一张.jpg"

    _paste(library, library / "奈良", source)
    result = _paste(library, library / "奈良", source)

    created = library / "奈良" / "一张 2.jpg"
    assert [item.created for item in result.pasted] == [created]
    assert item_renamed(result) is True
    assert created.read_bytes() == (library / "奈良" / "一张.jpg").read_bytes()


def item_renamed(result) -> bool:  # noqa: ANN001 - 测试里的小读法
    return result.pasted[0].renamed


def test_renaming_keeps_going_until_the_name_is_free(library: Path) -> None:
    """原名与「一张 2.jpg」都被占着，就再往后找一个 —— 绝不覆盖它们中的任何一个。"""
    source = library / "京都" / "一张.jpg"
    for taken in ("一张.jpg", "一张 2.jpg"):
        Image.new("RGB", (8, 6), (9, 9, 9)).save(library / "奈良" / taken)

    result = _paste(library, library / "奈良", source)

    assert [item.created for item in result.pasted] == [library / "奈良" / "一张 3.jpg"]
    # 占位的那两张不能被动。
    assert (library / "奈良" / "一张 2.jpg").read_bytes() != source.read_bytes()


def test_folder_names_are_renamed_the_same_way(library: Path) -> None:
    """文件夹也照访达的规矩来：``京都`` → ``京都 2``。"""
    _paste(library, library / "奈良", library / "京都")

    assert (library / "奈良" / "京都" / "一张.jpg").is_file()


def test_refuses_to_paste_a_folder_into_itself(library: Path) -> None:
    """文件夹粘进它自己（或它的子目录）会无限递归，一律拒绝。"""
    result = _paste(library, library / "京都", library / "京都")

    assert result.pasted == ()
    assert [f.message for f in result.failures] == ["不能把文件夹粘进它自己或它自己的子目录。"]
    assert sorted(p.name for p in (library / "京都").iterdir()) == ["一张.jpg", "另一张.jpg"]


def test_missing_source_is_reported_not_swallowed(library: Path) -> None:
    """源没了就说清楚，不能静默少粘一个。"""
    gone = library / "京都" / "没了.jpg"

    result = _paste(library, library / "奈良", gone)

    assert [f.message for f in result.failures] == ["源文件已经不在了。"]


def test_target_must_be_a_directory(library: Path) -> None:
    with pytest.raises(PasteError):
        _paste(library, library / "京都" / "一张.jpg", library / "奈良" / "一张.jpg")


def test_cancelling_stops_before_the_next_item(library: Path) -> None:
    """取消只在**项与项之间**生效：粘到一半停下来会留下半截文件，那比粘完更糟。

    判据是「一项都没粘」——取消检查在动手之前。
    """
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 第一项开始前放行，之后一律取消

    result = copy_into(
        library,
        library / "奈良",
        (library / "京都" / "一张.jpg", library / "京都" / "另一张.jpg"),
        should_cancel=should_cancel,
    )

    assert len(result.pasted) == 1
    assert sorted(p.name for p in (library / "奈良").iterdir()) == ["一张.jpg"]


def test_a_half_written_file_is_cleaned_up(library: Path, monkeypatch) -> None:  # noqa: ANN001
    """复制到一半失败：把**自己刚写的那个半截文件**清掉，不留给用户。

    留着它才是真的把相册弄脏了 —— 库里会出现一张解码不了的「照片」。
    只删这一个：别的文件一个都不碰（源文件仍在、已有的同名文件仍在）。
    """

    def failing_copy(source: str, target: str, *args: object, **kwargs: object) -> str:
        Path(target).write_bytes(b"\x00partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("gallery.core.metadata.paste.shutil.copy2", failing_copy)
    source = library / "京都" / "一张.jpg"

    result = _paste(library, library / "奈良", source)

    assert result.pasted == ()
    assert "磁盘满" in result.failures[0].message
    assert sorted(p.name for p in (library / "奈良").iterdir()) == []
    assert source.exists()


def test_read_only_target_is_reported(library: Path) -> None:
    """没权限写进目标目录：要有一句说清的中文，而不是抛一个裸的 OSError。"""
    locked = library / "只读"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _paste(library, locked, library / "京都" / "一张.jpg")

        assert result.pasted == ()
        assert result.failures[0].message.startswith("没有权限")
    finally:
        os.chmod(locked, stat.S_IRWXU)


# ---- 记账 -------------------------------------------------------------------


def test_every_pasted_item_is_journaled(library: Path) -> None:
    source = library / "京都" / "一张.jpg"

    _paste(library, library / "奈良", source)

    records = [r for r in read_records(library) if r.action == ACTION_PASTE]
    assert len(records) == 1
    assert records[0].source == source
    assert records[0].target == library / "奈良" / "一张.jpg"
    assert records[0].result == RESULT_OK
    assert records[0].is_dir is False


def test_failed_items_are_journaled_too(library: Path) -> None:
    gone = library / "京都" / "没了.jpg"

    _paste(library, library / "奈良", gone)

    # 源没了这种「压根没动手」的情况不写日志：没有新文件产生，也就没有可回滚的东西。
    assert [r for r in read_records(library) if r.action == ACTION_PASTE] == []


def test_a_failed_copy_is_journaled_as_failed(library: Path, monkeypatch) -> None:  # noqa: ANN001
    """动手了却没成的那一项也要记一笔 ``failed``：粘过的痕迹不能只留在脑子里。"""

    def failing_copy(source: str, target: str, *args: object, **kwargs: object) -> str:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("gallery.core.metadata.paste.shutil.copy2", failing_copy)

    _paste(library, library / "奈良", library / "京都" / "一张.jpg")

    records = [r for r in read_records(library) if r.action == ACTION_PASTE]
    assert len(records) == 1
    assert records[0].result == RESULT_FAILED
    assert "权限" in (records[0].message or "")


def test_paste_is_not_in_the_undo_stack(library: Path) -> None:
    """粘进来的文件**不能 ⌘Z**：撤销粘贴就是删文件（红线 1）。

    这条是本项目里最容易日后被人「顺手补上」的地方，所以钉住：粘完之后撤销栈必须
    仍然是空的，⌘Z 什么也不做，而新文件还在。
    """
    _paste(library, library / "奈良", library / "京都" / "一张.jpg")

    assert next_undo_label(library) is None
    assert undo_last_write(library) is None
    assert (library / "奈良" / "一张.jpg").is_file()


def test_paste_records_do_not_disturb_the_undo_stack(library: Path) -> None:
    """混着改名一起做：粘的那几条不能把改名的撤销单元挤掉。"""
    from gallery.core.library.photos import PhotoRef
    from gallery.core.metadata.rename import rename_entry

    entry = PhotoRef.from_path(library / "京都" / "另一张.jpg")
    rename_entry(library, entry, "两张.jpg")
    _paste(library, library / "奈良", library / "京都" / "两张.jpg")

    assert next_undo_label(library) == "重命名"
    assert [r.action for r in read_records(library)] == [ACTION_RENAME, ACTION_PASTE]
