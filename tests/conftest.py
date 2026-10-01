"""测试共享工具。

测试数据一律现场构造：仓库里不放真实照片，也不放任何大尺寸二进制（见 AGENTS.md）。
需要真实文件时用 Pillow 当场生成最小的合法图片。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

#: EXIF 中 Orientation 标签的编号。
EXIF_ORIENTATION_TAG = 0x0112


#: 测试用的假废纸篓目录名。以点开头，扫描器因此不会把它当成一个相册（真废纸篓同理）。
FAKE_TRASH_NAME = ".废纸篓"


@pytest.fixture(autouse=True)
def fake_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把「废纸篓」指向临时目录，返回那个目录。

    **故意是 autouse 的**：这条测试要动的是用户的文件，测试一旦漏了这个 fixture，
    就会把临时照片搬进用户真实的 ``~/.Trash`` —— 那里面的东西用户还要还原，
    而这个隐患是静默的（在受限环境里表现为「测试莫名其妙失败」）。
    统一在这里兜住，每个测试就不必自己记得了（需要断言废纸篓内容的照常显式要它）。
    """
    from gallery.core.metadata import trash

    directory = tmp_path / FAKE_TRASH_NAME
    monkeypatch.setattr(trash, "trash_dir_for", lambda _path: directory)
    return directory


@pytest.fixture
def make_photo(tmp_path: Path) -> Callable[..., Path]:
    """返回一个「造照片」的工厂：``make_photo("a.jpg", size=(40, 20))``。

    默认在 ``tmp_path`` 下生成，保证每个测试都在自己的临时目录里工作。
    """
    counter = itertools.count()

    def _make(
        name: str = "photo.jpg",
        *,
        size: tuple[int, int] = (64, 48),
        color: tuple[int, ...] = (200, 120, 40),
        mode: str = "RGB",
        orientation: int | None = None,
        directory: Path | None = None,
        data: bytes | None = None,
    ) -> Path:
        target_dir = tmp_path if directory is None else directory
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / name
        if data is not None:
            path.write_bytes(data)
            return path
        if path.exists():  # 同名重复调用时换一个名字，避免测试互相覆盖
            path = target_dir / f"{path.stem}-{next(counter)}{path.suffix}"
        image = Image.new(mode, size, color)
        try:
            if orientation is None:
                image.save(path)
            else:
                exif = image.getexif()
                exif[EXIF_ORIENTATION_TAG] = orientation
                image.save(path, exif=exif)
        finally:
            image.close()
        return path

    return _make
