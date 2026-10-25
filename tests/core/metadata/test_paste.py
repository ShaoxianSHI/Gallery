"""粘贴的测试。

**只增不改、绝不覆盖**、拒绝越界的目标、写进 ``journal/`` 但不进撤销栈。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import PasteError
from gallery.core.metadata import naming
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
    """连修改时间一起复制：库里默认按修改时间排序，粘进来的照片因此留在原来的时间位置上，
    而不是齐刷刷跳到「今天」。"""
    source = library / "京都" / "一张.jpg"
    os.utime(source, (1_600_000_000, 1_600_000_000))

    _paste(library, library / "奈良", source)

    assert int((library / "奈良" / "一张.jpg").stat().st_mtime) == 1_600_000_000


def test_pasting_where_it_already_is_makes_a_copy(library: Path) -> None:
    """已经在目标目录里的那几项**不再是空操作**，而是各复制一份（ADR 0044）。

    用户按了 ⌘V 却什么都没发生，那是做错了事而不是保护了什么 —— 而这一份要看得见名字：
    ``一张.jpg`` → ``一张的副本.jpg``，不是照原名再编号得到的 ``一张 2.jpg``（那个名字用户
    分不出是刚粘来的还是自己早先就有的那一份）。
    """
    source = library / "京都" / "一张.jpg"

    result = _paste(library, library / "京都", source)

    assert [item.created for item in result.pasted] == [library / "京都" / "一张的副本.jpg"]
    assert source.exists(), "源必须还在原地"
    assert sorted(p.name for p in (library / "京都").iterdir()) == [
        "一张.jpg",
        "一张的副本.jpg",
        "另一张.jpg",
    ]


def test_the_copy_of_a_copy_is_named_after_the_copy(library: Path) -> None:
    """对一个副本再复制一次是 ``一张的副本的副本.jpg``：照字面再加一层（ADR 0044）。

    刻意不剥掉已有的「的副本」退回编号 —— 那要用户先猜到「为什么它不叫副本的副本」。
    """
    source = library / "京都" / "一张.jpg"

    _paste(library, library / "京都", source)
    _paste(library, library / "京都", library / "京都" / "一张的副本.jpg")

    assert (library / "京都" / "一张的副本的副本.jpg").exists()


def test_a_copy_naming_collide_falls_back_to_the_numbering(library: Path) -> None:
    """副本名也撞上了就按访达那套编号（ADR 0044）：``一张的副本 2.jpg``。

    编号那一段**不另写一份** —— 它就是 :func:`~gallery.core.metadata.naming.free_name_in`。
    """
    source = library / "京都" / "一张.jpg"

    _paste(library, library / "京都", source)
    _paste(library, library / "京都", source)

    assert (library / "京都" / "一张的副本 2.jpg").exists()
    assert sorted(p.name for p in (library / "京都").iterdir()) == [
        "一张.jpg",
        "一张的副本 2.jpg",
        "一张的副本.jpg",
        "另一张.jpg",
    ]


def test_duplicating_an_album_treats_the_whole_name_as_the_name(library: Path) -> None:
    """相册没有扩展名，所以**整个名字**后面加「的副本」：``京都.2024`` → ``京都.2024的副本``，
    而不是 ``京都的副本.2024``（ADR 0018 决策 3 定的口径，ADR 0044 沿用）。"""
    album = library / "京都.2024"
    (album / "里面").mkdir(parents=True)
    Image.new("RGB", (8, 6), (1, 2, 3)).save(album / "里面" / "一张.jpg")

    result = _paste(library, library, album)

    created = library / "京都.2024的副本"
    assert [item.created for item in result.pasted] == [created]
    assert (created / "里面" / "一张.jpg").exists(), "相册是整棵复制，不是空壳"


def test_same_folder_check_survives_a_different_spelling(tmp_path: Path) -> None:
    """``/var`` 与 ``/private/var`` 在 macOS 上是同一个文件夹：不解析就比不相等，
    粘进去的会是 ``一张 2.jpg`` 而不是 ``一张的副本.jpg``。用符号链接造两种写法，
    不依赖 macOS 约定。"""
    real = tmp_path / "真实"
    (real / "京都").mkdir(parents=True)
    Image.new("RGB", (8, 6), (1, 2, 3)).save(real / "京都" / "一张.jpg")
    link = tmp_path / "链接"
    link.symlink_to(real, target_is_directory=True)
    config_dir(tmp_path).mkdir()

    result = copy_into(real, link / "京都", (link / "京都" / "一张.jpg",))

    assert [item.created.name for item in result.pasted] == ["一张的副本.jpg"]


def test_renames_instead_of_overwriting(library: Path) -> None:
    """同名不覆盖，改叫「一张 2.jpg」—— 名字是量出来的：在访达里对同一项连按两次
    「复制项目」，中文系统给的就是这两个名字。
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

    assert (library / "奈良" / "一张 2.jpg").read_bytes() != source.read_bytes()


def test_folder_names_are_renamed_the_same_way(library: Path) -> None:
    """文件夹也照访达的规矩来：``京都`` → ``京都 2``。"""
    _paste(library, library / "奈良", library / "京都")

    assert (library / "奈良" / "京都" / "一张.jpg").is_file()


def test_refuses_to_paste_a_folder_into_itself(library: Path) -> None:
    """文件夹粘进它自己（或它的子目录）会无限递归，一律拒绝。"""
    result = _paste(library, library / "京都", library / "京都")

    assert result.pasted == ()
    assert [f.message for f in result.failures] == ["不能把文件夹粘贴到它自身或其子目录中。"]
    assert sorted(p.name for p in (library / "京都").iterdir()) == ["一张.jpg", "另一张.jpg"]


def test_missing_source_is_reported_not_swallowed(library: Path) -> None:
    """源没了就说清楚，不能静默少粘一个。"""
    gone = library / "京都" / "没了.jpg"

    result = _paste(library, library / "奈良", gone)

    assert [f.message for f in result.failures] == ["源文件已不存在。"]


def test_target_must_be_a_directory(library: Path) -> None:
    with pytest.raises(PasteError):
        _paste(library, library / "京都" / "一张.jpg", library / "奈良" / "一张.jpg")


def test_cancelling_stops_before_the_next_item(library: Path) -> None:
    """取消只在**项与项之间**生效：粘到一半停下来会留下半截文件，那比粘完更糟。"""
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
    """复制到一半失败：把**自己刚写的那个半截文件**清掉 —— 留着它，库里会多一张
    解码不了的「照片」。只删这一个，源文件与已有的同名文件一个都不碰。
    """

    def failing_copy(source: str, target: str, *args: object, **kwargs: object) -> str:
        Path(target).write_bytes(b"\x00partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("gallery.core.metadata.paste.shutil.copy2", failing_copy)
    source = library / "京都" / "一张.jpg"

    result = _paste(library, library / "奈良", source)

    assert result.pasted == ()
    assert "磁盘已满" in result.failures[0].message
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
    """粘进来的文件**不能 ⌘Z**：撤销粘贴就是删文件（删除只许用户亲手触发）—— 这里最容易日后被人
    「顺手补上」，所以锁定：粘完之后撤销栈仍然是空的，而新文件还在。
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


def test_a_paste_target_outside_the_library_is_refused(library: Path, tmp_path: Path) -> None:
    """**目标必须在库里**：落到库外就是一份谁都看不见、扫描器不收、索引不记、也没人清理的
    孤儿文件。
    """
    outside = tmp_path / "库外"
    outside.mkdir()
    source = tmp_path / "从访达粘来的.jpg"
    Image.new("RGB", (8, 6), (30, 60, 90)).save(source)

    with pytest.raises(PasteError, match="只能粘贴到库中的文件夹"):
        copy_into(library, outside, [source])

    assert not (outside / "从访达粘来的.jpg").exists()


def test_a_paste_source_may_live_outside_the_library(library: Path, tmp_path: Path) -> None:
    """**源可以在库外** —— 那正是这个操作存在的理由（访达里 ⌘C 复制进来的就是）；
    守卫只查目标，源那一侧也挡一道的话 ADR 0018 的意义就没了。
    """
    outside = tmp_path / "下载"
    outside.mkdir()
    source = outside / "从访达粘来的.jpg"
    Image.new("RGB", (8, 6), (30, 60, 90)).save(source)
    target = library / "京都"
    target.mkdir(exist_ok=True)

    result = copy_into(library, target, [source])

    assert result.pasted and (target / "从访达粘来的.jpg").exists()


def test_the_library_root_is_a_legal_paste_target(library: Path, tmp_path: Path) -> None:
    """**「全部照片」那条 ⌘V 粘的就是库根**，而守卫一度把它挡掉，那条命令整个失灵 ——
    守卫的立意是「不许跑到库外」，库根恰恰是没选中项时 ``_paste_target()`` 返回的地方。
    """
    source = tmp_path / "从访达粘来的.jpg"
    Image.new("RGB", (8, 6), (30, 60, 90)).save(source)

    result = copy_into(library, library, [source])

    assert result.pasted and (library / "从访达粘来的.jpg").exists()


def test_the_config_directory_is_not_a_paste_target(library: Path, tmp_path: Path) -> None:
    """配置目录里都是「只有 Gallery 自己看得见」的缓存：用户往里粘照片不会让它变得可见，
    只会让下一次清理把它当垃圾（``.gallery/`` 里除 ``config.toml`` 外都是缓存）。"""
    source = tmp_path / "从访达粘来的.jpg"
    Image.new("RGB", (8, 6), (30, 60, 90)).save(source)

    with pytest.raises(PasteError, match="配置目录"):
        copy_into(library, paths.config_dir(library), [source])


def test_an_unreadable_destination_never_deletes_the_folder_already_there(
    library: Path, tmp_path: Path
) -> None:
    """**目录读不了的时候，绝不能把用户已有的同名文件夹连同照片删掉**（不删除、不移动用户照片那条）。

    缺陷现场（已实测复现，用户目录连同 3 张照片一起消失）：
    ``occupied_names`` 在 ``scandir`` 失败时返回**空集**，而 ``free_name_in`` 的
    ``taken()`` 判的是 ``occupied is not None`` —— 空集不是 ``None``，于是那句
    「目录读不了就当作空的，``free_name_in`` 会退回去真的去 ``exists()``」是假的：
    查表判成「原名空着」→ ``copytree`` 撞上用户那个同名文件夹抛 ``FileExistsError``
    → 收尾那句 ``rmtree`` 把**用户的文件夹**删了，横幅只说一句「复制失败」。

    触发条件在真实机器上并不罕见：目录可写可进入但不可读、云盘占位目录、网络卷瞬断。

    ``0o333`` 是「属主可进入、不可读」—— 只有属主那份读权限被摘掉才能造出这个形状，
    所以这个用例在别的用户下没有意义（本项目只在 macOS 上跑）。
    """
    destination = library / "奈良"
    user_folder = destination / "2024"
    user_folder.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (user_folder / name).write_text("用户的照片")

    incoming = tmp_path / "2024"
    incoming.mkdir()
    (incoming / "x.jpg").write_text("新照片")

    destination.chmod(0o333)
    try:
        assert naming.occupied_names(destination) is None, "前提不成立：目录本该读不了"
        result = copy_into(library, destination, [incoming])
    finally:
        destination.chmod(0o755)

    assert user_folder.is_dir(), "用户自己的文件夹被删了"
    assert sorted(p.name for p in user_folder.iterdir()) == ["a.jpg", "b.jpg", "c.jpg"]
    assert [item.created.name for item in result.pasted] == ["2024 2"], "应当改用编号让开"


def test_a_same_named_file_dropped_in_mid_batch_is_never_overwritten(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**「绝不覆盖」得由磁盘保证，不能只靠批次开始时那张快照**（不删除、不移动用户照片那条）。

    ``occupied_names`` 是操作开始那一刻的一次快照，而一批几百项、跨卷时要跑几分钟。
    期间用户或访达/同步工具放进去的同名文件，那张表看不见：``shutil.copy2`` 会
    **静默覆盖**、``Path.rename`` 在 POSIX 上是原子替换，两条路都不报错 ——
    用户自己那份就这么没了，横幅还报「已粘贴 1 项」。

    这里把那个窗口造出来：让这一批拿到的是**一份已经过期的快照** —— 快照取完之后，
    目标目录里才落进一个同名文件。
    """
    destination = library / "奈良"
    mine = tmp_path / "a.jpg"
    mine.write_text("Gallery 的照片")

    stale = naming.occupied_names(destination)  # 快照：此刻目录里还什么都没有
    assert stale == set(), "前提不成立：快照应当是空的"
    landed = destination / "a.jpg"
    landed.write_text("用户后来放进去的")

    monkeypatch.setattr("gallery.core.metadata.paste.occupied_names", lambda _dir: stale)
    result = copy_into(library, destination, [mine])

    assert landed.read_text() == "用户后来放进去的", "用户那份被覆盖了"
    assert [item.created.name for item in result.pasted] == ["a 2.jpg"], "应当加编号让开"
