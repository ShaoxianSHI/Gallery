"""``tests/core/metadata/`` 共用的 fixture。

三份写操作测试各自抄过一模一样的一份，收在这里才只有一处。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.library.photos import PhotoRef


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """一个已经准备好配置目录的库：两个相册，每个里两张照片。

    配置目录走 ``paths.config_dir`` —— 目录名只有一个来源，测试里也不许硬编码。"""
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
    """让记账写不进去（模拟只读磁盘 / 权限问题）：拦的是 ``journal`` 目录的 ``mkdir``。

    **写盘前必先写 journal**：记不上账不能说「没改成」，只能说「撤销不了」——
    五个写操作都得能这么退化。"""
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths.journal_dir(library):
            raise OSError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse, raising=True)
    return library
