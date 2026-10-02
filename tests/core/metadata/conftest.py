"""``tests/core/metadata/`` 共用的东西。

原本每个写操作测试各自抄了一份一模一样的 ``library`` fixture（三份，字面相同、
只有 docstring 不同），还各自抄了一份「等后台任务收工」的 ``_wait``。收在这里，
改动才有一处。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.library.photos import PhotoRef


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """一个已经准备好配置目录的库：两个相册，每个里两张照片。

    配置目录是用 :func:`gallery.core.paths.config_dir` 建的 —— 那个名字只有一个来源
    （``core.constants.CONFIG_DIR_NAME``），测试里也不该硬编码 ``.gallery`` 字面量。
    """
    root = tmp_path / "照片"
    (root / "京都").mkdir(parents=True)
    (root / "奈良").mkdir()
    for name in ("一张.jpg", "另一张.jpg"):
        Image.new("RGB", (8, 6), (200, 120, 40)).save(root / "京都" / name)
    paths.config_dir(root).mkdir()
    return root


@pytest.fixture
def photo(library: Path) -> PhotoRef:
    """``京都/一张.jpg``。"""
    return PhotoRef.from_path(library / "京都" / "一张.jpg")


@pytest.fixture
def unwritable_journal(library: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """让记账写不进去（模拟只读的磁盘 / 权限问题）。

    拦的是 ``journal`` 目录的 ``mkdir``：那正是 :func:`journal.append_record` 写的
    第一步。**这是红线 4 守着的那条路** —— 记不上账不能把已经改好的文件说成没改成，
    只能如实告诉用户「撤销不了了」。五个写操作都得能这么退化，所以做成 fixture。
    """
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths.journal_dir(library):
            raise OSError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse, raising=True)
    return library
