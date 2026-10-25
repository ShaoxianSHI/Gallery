"""跨卷移动（EXDEV）：**整个写子系统里唯一会删掉用户原件的一条路**。

同卷内只是一次 ``rename``；跨卷时它报 EXDEV，于是走「先拷贝、成功之后再删源」那条路。"""

from __future__ import annotations

import errno
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.journal import read_records
from gallery.core.metadata.transfer import move_into
from gallery.core.metadata.trash import trash_entries

PAYLOAD = ("这些字节必须一个不少地活到目标去" * 64).encode("utf-8")


@pytest.fixture
def cross_volume(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """让 ``rename`` 一律报「跨卷了」，从而逼出拷贝那条分支。

    :param copy_fails: 拷贝也要失败（模拟目标盘满了），用来验「源没被动过」。"""

    def force_exdev(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link", str(self))

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "rename", force_exdev, raising=True)

    def arrange(copy_fails: bool) -> None:
        if copy_fails:
            monkeypatch.setattr(shutil, "move", refuse)

    return arrange


def _seed(library: Path, name: str = "一张.jpg") -> Path:
    path = library / "京都" / name
    path.write_bytes(PAYLOAD)
    return path


def test_cross_volume_trash_copies_then_removes_only_after_success(
    library: Path, cross_volume: Callable[[bool], None]
) -> None:
    """跨卷移进废纸篓：目标是**一模一样的字节**，源这时才消失。"""
    cross_volume(False)
    source = _seed(library)

    result = trash_entries(library, [PhotoRef.from_path(source)])

    assert result.journaled is True
    assert result.failures == ()
    assert len(result.moved) == 1
    assert not source.exists(), "成功之后源才消失"

    # 落点只能从 journal 拿：``TrashResult.moved`` 装的是**原条目**，不是废纸篓里的位置。
    (record,) = read_records(library)
    assert record.succeeded
    assert record.target != source
    assert record.target.read_bytes() == PAYLOAD


def test_a_failed_cross_volume_trash_leaves_the_original_untouched(
    library: Path, cross_volume: Callable[[bool], None]
) -> None:
    """**拷贝失败时源文件分毫未动** —— 那一半是这份代码存在的全部理由。

    单项失败不抛异常（批量时是常态），收进 ``failures``，journal 记一条 ``failed``。"""
    cross_volume(True)
    source = _seed(library)

    result = trash_entries(library, [PhotoRef.from_path(source)])

    assert result.moved == ()
    assert [failure.message for failure in result.failures] == [
        "无法移到废纸篓：磁盘已满，请释放空间后重试。"
    ]
    assert source.read_bytes() == PAYLOAD, "用户的原件一个字节都不能少"
    (record,) = read_records(library)
    assert not record.succeeded
    assert record.target == source, "没移成，journal 不该说它到了别处"


def test_cross_volume_move_to_album_copies_then_removes(
    library: Path, cross_volume: Callable[[bool], None]
) -> None:
    """跨卷「移动到相册」同样是先拷贝成功、再删源，且 journal 记的是新位置。"""
    cross_volume(False)
    source = _seed(library)

    result = move_into(library, library / "奈良", [source])

    assert result.journaled is True
    assert result.failures == ()
    target = library / "奈良" / "一张.jpg"
    assert target.read_bytes() == PAYLOAD
    assert not source.exists()
    (record,) = read_records(library)
    assert record.target == target


def test_a_failed_cross_volume_transfer_keeps_the_original(
    library: Path, cross_volume: Callable[[bool], None]
) -> None:
    """失败的一条被逐条收进 ``failures``（整次操作不抛），且源必须还在原处。"""
    cross_volume(True)
    source = _seed(library)

    result = move_into(library, library / "奈良", [source])

    assert result.moved == ()
    assert [failure.message for failure in result.failures] == ["磁盘已满，请释放空间后重试。"]
    assert source.read_bytes() == PAYLOAD
    assert not (library / "奈良" / "一张.jpg").exists()
