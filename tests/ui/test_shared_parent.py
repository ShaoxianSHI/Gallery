"""「这组照片在不在同一个相册里」那个判断：**扫到第二个不一样的目录就收工**。

缺陷现场：原先是两趟全扫 —— 先 ``{photo.path.parent for photo in photos}`` 收一遍，
再 ``{parent.resolve() for parent in parents}`` 解析一遍。而
:meth:`WriteOpsMixin._paste_target` 在**分组视图**下传的是整组的照片、每次动作状态刷新
都走一遍（⌘V 的可用性判据）—— 三万张散在一百个目录里实测 31.4 ms/次。

这组用例盯两件事：

- 遇到第二个**解析后**不一样的目录就返回（不必把剩下的看完）
- **不能只看原始路径**：两个写法不一样但指向同一个地方的父目录（``a/./b``、符号链接）
  仍然算「在同一个相册里」。只比原始路径会把那种情形判反 —— 而那会让 ⌘V 在
  用户眼里「有时候能用有时候不能用」。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.library.photos import PhotoRef
from gallery.ui.write_ops import shared_parent


def _photos(*directories: Path, per_directory: int = 1) -> list[PhotoRef]:
    return [
        PhotoRef(path=directory / f"照片{index}.jpg", size=1, mtime_ns=1)
        for directory in directories
        for index in range(per_directory)
    ]


def test_two_different_directories_stop_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二个不一样的目录一出现就返回 ``None`` —— 剩下的照片不必再看。

    断的是**形状**（问了几次），不是时间：一百个目录和三万张照片时，原实现要把整组走完。
    """
    from gallery.ui import write_ops

    asked: list[Path] = []
    real = paths.resolved

    def counting(target: Path) -> Path:
        asked.append(target)
        return real(target)

    monkeypatch.setattr(write_ops.paths, "resolved", counting)

    directories = [tmp_path / f"相册{index}" for index in range(100)]
    for directory in directories:
        directory.mkdir()
    result = shared_parent(_photos(*directories, per_directory=300))

    assert result is None, "散在一百个目录里却说有共同相册"
    assert len(asked) <= 2, f"扫到第二个不一样的目录就该收工，却问了 {len(asked)} 次"


def test_one_directory_needs_no_resolving_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全都在同一个目录时**一次解析都不用做** —— 直接把那个原始父目录返回去。"""
    from gallery.ui import write_ops

    asked: list[Path] = []
    real = paths.resolved
    monkeypatch.setattr(
        write_ops.paths, "resolved", lambda target: (asked.append(target), real(target))[1]
    )

    inside = tmp_path / "同一个相册"
    inside.mkdir()
    result = shared_parent(_photos(inside, per_directory=500))

    assert result == inside, "同一个目录却没返回那个目录"
    assert asked == [], f"原始路径全都一样，还去解析了 {len(asked)} 次"


def test_two_spellings_of_one_directory_still_count_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**两种写法的同一个目录仍然算「同一个相册」。**

    这是这道判断最容易判错的一处，也是不能只比原始路径的原因：``a/b`` 与 ``a/./b``
    字符串不一样，解析之后是同一个地方。只比原始路径的话，用户在 ⌘V 上会看到
    「有时候能粘有时候不能」，而目录树里明明只有那一个相册。
    """
    from gallery.ui import write_ops

    asked: list[Path] = []
    real = paths.resolved
    monkeypatch.setattr(
        write_ops.paths, "resolved", lambda target: (asked.append(target), real(target))[1]
    )

    plain = tmp_path / "相册"
    plain.mkdir()
    photos = [PhotoRef(path=plain / "一张.jpg", size=1, mtime_ns=1)]
    photos.append(PhotoRef(path=plain / "." / "另一张.jpg", size=1, mtime_ns=1))
    # 「a/./b」经 Path 规范化后未必还留着那一段，所以直接用真 symlink 再验一次
    link = tmp_path / "链接"
    try:
        os.symlink(plain, link, target_is_directory=True)
    except OSError:
        pass
    else:
        photos.append(PhotoRef(path=link / "第三张.jpg", size=1, mtime_ns=1))

    result = shared_parent(photos)

    assert result == paths.resolved(plain), (
        f"两种写法 / 符号链接指向同一个相册，却判成没有共同相册（得到 {result}）"
    )
    assert asked, "这一条本该走到解析那一步（原始路径确实不一样）"
