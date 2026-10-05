"""「移动到相册」的测试（ADR 0019）：在 ``tmp_path`` 里进行。

**动的是位置不是内容**、**绝不覆盖**（同名改叫「一张 2.jpg」）、**边界**、**一次 ⌘Z 撤一整批**。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import TransferError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.journal import (
    ACTION_MOVE,
    ACTION_RENAME,
    RESULT_FAILED,
    read_records,
)
from gallery.core.metadata.rename import rename_entry
from gallery.core.metadata.transfer import move_into
from gallery.core.metadata.undo import next_undo_label, undo_last_write


def _move(root: Path, target: Path, *sources: Path):
    return move_into(root, target, sources)


def _rename(root: Path, entry: Path, new_name: str) -> None:
    """走真正的改名入口，这样才会写下一条 ``action=rename`` 的记录：直接 ``Path.rename()``
    不记账，而撤销栈是照着日志重放出来的 —— 没记账的改动在这摞 ⌘Z 里根本不存在。
    """
    rename_entry(root, PhotoRef(path=entry, size=entry.stat().st_size, mtime_ns=0), new_name)


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def test_move_relocates_without_touching_the_bytes(library: Path) -> None:
    """像素与修改时间都不变，但**原处不再有它** —— 这才是「移动」与「复制」的分界。"""
    source = library / "京都" / "一张.jpg"
    before = source.read_bytes()
    mtime = source.stat().st_mtime_ns

    result = _move(library, library / "奈良", source)

    created = library / "奈良" / "一张.jpg"
    assert [item.created for item in result.moved] == [created]
    assert created.read_bytes() == before
    assert created.stat().st_mtime_ns == mtime
    assert not source.exists(), "原处必须已经没有它了（移动，不是复制）"


def test_move_never_overwrites_a_same_named_file(library: Path) -> None:
    """目标已有同名时改叫「一张 2.jpg」，**绝不覆盖**（访达的规矩，与粘贴同一份实现）。"""
    taken = library / "奈良" / "一张.jpg"
    Image.new("RGB", (8, 6), (1, 2, 3)).save(taken)
    before = taken.read_bytes()

    result = _move(library, library / "奈良", library / "京都" / "一张.jpg")

    created = library / "奈良" / "一张 2.jpg"
    assert [item.created for item in result.moved] == [created]
    assert result.moved[0].renamed
    # 目标里原来那张一个字节都没变（比字节不比像素：JPEG 存回去会掉色）。
    assert taken.read_bytes() == before


def test_move_moves_the_whole_batch(library: Path) -> None:
    """多选就是整批一起挪，一项失败不打断其余的。"""
    sources = [library / "京都" / name for name in ("一张.jpg", "另一张.jpg")]

    result = _move(library, library / "奈良", *sources)

    assert len(result.moved) == 2
    assert _names(library / "奈良") == ["一张.jpg", "另一张.jpg"]
    assert _names(library / "京都") == []


def test_move_skips_what_is_already_there(library: Path) -> None:
    """本来就在那个相册里的原样跳过，不凭空多出一份「一张 2.jpg」。"""
    target = library / "京都"
    Image.new("RGB", (8, 6), (9, 9, 9)).save(target / "占位.jpg")

    result = _move(library, target, target / "一张.jpg", target / "占位.jpg")

    assert result.moved == ()
    assert [item.source.name for item in result.skipped] == ["一张.jpg", "占位.jpg"]
    assert result.skipped[0].message == "它已经在「京都」里了。"
    assert _names(target) == ["一张.jpg", "占位.jpg", "另一张.jpg"]


def test_move_resolves_symlinks_before_comparing(library: Path) -> None:
    """「已经在那儿」要按**磁盘上的真实位置**比（macOS 上 ``/var`` 链到 ``/private/var``）：
    不解析就会判成「不在同一个目录」，于是把照片原地又「移动」一次，多出一份「一张 2.jpg」。
    """
    source = library / "京都" / "一张.jpg"
    linked = library / "别名"
    os.symlink(library / "京都", linked)

    result = _move(library, linked, source)

    assert result.moved == ()
    assert len(result.skipped) == 1


def test_move_refuses_to_put_an_album_inside_itself(library: Path) -> None:
    """相册不能挪进它自己的子相册 —— 那会无限递归。"""
    (library / "京都" / "子相册").mkdir()

    result = _move(library, library / "京都" / "子相册", library / "京都")

    assert result.moved == ()
    assert result.failures[0].message == "不能把相册移进它自己或它自己的子相册。"
    assert (library / "京都").is_dir()


def test_move_reports_a_missing_source(library: Path) -> None:
    """源已经不在了要如实说，不能静默少挪一个。"""
    result = _move(library, library / "奈良", library / "京都" / "没有这张.jpg")

    assert result.moved == ()
    assert result.failures[0].message == "它已经不在原来的位置了，可能刚刚被别的东西移走了。"


def test_move_rejects_a_target_that_is_not_a_directory(library: Path) -> None:
    """目标压根不是相册时抛异常 —— 那是调用方的错，不是「这一项没成」。"""
    with pytest.raises(TransferError):
        _move(library, library / "京都" / "一张.jpg", library / "京都" / "另一张.jpg")


def test_move_stops_at_the_next_item_when_cancelled(library: Path) -> None:
    """取消只在**项与项之间**生效：移一半停下来会让用户说不清哪些被移了。"""
    sources = [library / "京都" / name for name in ("一张.jpg", "另一张.jpg")]

    result = move_into(library, library / "奈良", sources, should_cancel=lambda: True)

    assert result.moved == ()
    assert _names(library / "京都") == ["一张.jpg", "另一张.jpg"]


def test_move_journals_every_item(library: Path) -> None:
    """每一项都记一条 ``action=move``，从哪儿来、到哪儿去都写清楚。"""
    _move(library, library / "奈良", library / "京都" / "一张.jpg")

    records = read_records(library)

    assert len(records) == 1
    record = records[0]
    assert record.action == ACTION_MOVE
    assert record.succeeded
    assert record.source == library / "京都" / "一张.jpg"
    assert record.target == library / "奈良" / "一张.jpg"
    assert record.batch, "一次操作的多项要共用一个 batch，否则一次 ⌘Z 撤不回来"


def test_undo_moves_everything_back(library: Path) -> None:
    """⌘Z 把它挪回原来的相册 —— 撤销一次移动仍然是一次移动，不是删除。"""
    sources = [library / "京都" / name for name in ("一张.jpg", "另一张.jpg")]
    _move(library, library / "奈良", *sources)

    assert next_undo_label(library) == "移动到相册"
    result = undo_last_write(library)

    assert result is not None
    assert result.action == ACTION_MOVE
    assert not result.failures
    assert _names(library / "京都") == ["一张.jpg", "另一张.jpg"]
    assert _names(library / "奈良") == []


def test_undo_is_gone_after_it_ran(library: Path) -> None:
    """撤过一次就不再留着：⌘Z 撤不掉同一个动作两遍。"""
    _move(library, library / "奈良", library / "京都" / "一张.jpg")

    assert undo_last_write(library) is not None
    assert undo_last_write(library) is None


def test_undo_refuses_when_the_original_place_is_taken(library: Path) -> None:
    """原位置被占了就不动它（红线 1）—— 替用户腾位置等于删掉他放进去的东西，
    与取回废纸篓同一个取向：宁可撤不回来，也不替用户做删除的决定。
    """
    source = library / "京都" / "一张.jpg"
    _move(library, library / "奈良", source)
    Image.new("RGB", (8, 6), (5, 5, 5)).save(source)

    result = undo_last_write(library)

    assert result is not None
    assert result.failures
    assert "这个位置已经有别的文件了" in result.failures[0].message
    assert (library / "奈良" / "一张.jpg").exists(), "撤不掉的就该留在原地"
    assert (library / "京都" / "一张.jpg").exists(), "用户在原位置放的东西不能被动"


def test_undo_records_a_failure_too(library: Path) -> None:
    """撤不回来也要**如实记账**，否则日志会说一套、磁盘是另一套。"""
    _move(library, library / "奈良", library / "京都" / "一张.jpg")
    Image.new("RGB", (8, 6), (5, 5, 5)).save(library / "京都" / "一张.jpg")

    undo_last_write(library)

    failures = [r for r in read_records(library) if not r.succeeded]
    assert len(failures) == 1
    assert failures[0].result == RESULT_FAILED
    assert "这个位置已经有别的文件了" in (failures[0].message or "")


def test_undo_stack_is_shared_with_rename(library: Path) -> None:
    """移动与改名、移到废纸篓共用**同一摞** ⌘Z 栈，栈顶是最近一次。"""
    _move(library, library / "奈良", library / "京都" / "一张.jpg")
    _rename(library, library / "京都" / "另一张.jpg", "改个名.jpg")

    assert next_undo_label(library) == "重命名"
    assert undo_last_write(library) is not None
    assert (library / "京都" / "另一张.jpg").exists()
    assert next_undo_label(library) == "移动到相册"
    assert undo_last_write(library) is not None
    assert (library / "京都" / "一张.jpg").exists()


def test_a_move_is_not_confused_with_a_rename_record(library: Path) -> None:
    """日志里两种 action 挨着时，撤销各自找各自的（别按字符串前缀配对）。"""
    _rename(library, library / "京都" / "另一张.jpg", "改个名.jpg")
    _move(library, library / "奈良", library / "京都" / "一张.jpg")

    actions = [r.action for r in read_records(library) if r.succeeded]
    assert actions == [ACTION_RENAME, ACTION_MOVE]

    result = undo_last_write(library)
    assert result is not None and result.action == ACTION_MOVE
    assert (library / "京都" / "一张.jpg").exists()
    assert (library / "京都" / "改个名.jpg").exists()


def test_move_reports_failure_for_an_unwritable_target(library: Path) -> None:
    """目标写不进去要如实报中文原因，而不是抛一个栈给用户看。"""
    # **只读相册必须在库里**：放库外的话 ``transfer._guard`` 会先一步挡下来，走不到这里。
    locked = library / "只读相册"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _move(library, locked, library / "京都" / "一张.jpg")
    finally:
        os.chmod(locked, stat.S_IRWXU)

    assert result.moved == ()
    assert result.failures
    assert "没有权限" in result.failures[0].message
    assert (library / "京都" / "一张.jpg").exists(), "没挪成就必须还在原地"


def test_a_target_outside_the_library_is_refused(library: Path, tmp_path: Path) -> None:
    """**移到库外一律拒绝** —— 那是「把用户的文件搬出 Gallery 管得着的地方」（红线 1）。

    界面上够不到，但缺了这道闸，别的入口日后一旦露出来，照片就被搬走且撤不回来。"""
    outside = tmp_path / "库外"
    outside.mkdir()

    with pytest.raises(TransferError, match="只能移到库里的相册"):
        _move(library, outside, library / "京都" / "一张.jpg")

    assert (library / "京都" / "一张.jpg").exists(), "挡下来了就得还在原地"


def test_the_library_root_is_a_legal_target(library: Path) -> None:
    """**库根本身不挡**：守卫要挡的是「跑到库外」，不是「落在库里」。

    与 ``rename._guard`` / ``trash._guard`` 不同 —— 那两个查的是**源**，对目标不成立。"""
    result = _move(library, library, library / "京都" / "一张.jpg")

    assert result.moved and (library / "一张.jpg").exists()


def test_the_config_directory_is_not_a_target_album(library: Path) -> None:
    """配置目录里放东西 = 用户照片目录里多出一份非「只有缓存才有」的数据（红线 5）。"""
    with pytest.raises(TransferError, match="配置目录"):
        _move(library, paths.config_dir(library), library / "京都" / "一张.jpg")
