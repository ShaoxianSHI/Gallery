"""新建相册：在某一层里 ``mkdir`` 一个空文件夹（界面上是「新建相册」⇧⌘N，见 ADR 0022）。

**只增不改**：建出来的是全新的空文件夹，用户的照片一个字节都没动；目标已有同名时按访达的规矩
改叫「新建相册 2」，绝不覆盖。**不进撤销栈** —— 撤销一次新建就是删文件夹，而删除只允许用户在
界面上亲手触发（红线 1）；本模块仍然记账，紧接着给新相册起的那个名字是一次普通改名，可 ⌘Z。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.errors import AlbumError
from gallery.core.library.photos import FolderRef
from gallery.core.metadata.failures import os_reason
from gallery.core.metadata.journal import (
    ACTION_CREATE,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import clean_name, free_name_in

#: 新建出来的相册默认叫什么。**与菜单项同名**：用户一看就知道刚才是自己干的。
DEFAULT_ALBUM_NAME: Final = "新建相册"


@dataclass(frozen=True, slots=True)
class CreatedAlbum:
    """新建出来的那个相册。"""

    #: 刚落地的那个文件夹（引用已经按磁盘上的真实状态读过一遍）。
    album: FolderRef
    #: 是否留下了日志记录。文件夹已建好但记账失败时为 ``False`` —— 不影响用户手里的东西。
    journaled: bool = True

    @property
    def path(self) -> Path:
        return self.album.path

    @property
    def name(self) -> str:
        return self.album.name


def create_album(root: Path, parent: Path, name: str = DEFAULT_ALBUM_NAME) -> CreatedAlbum:
    """在 ``parent`` 里建一个叫 ``name`` 的空相册（同名自动改叫「新建相册 2」）。

    :raises AlbumError: 目标不是文件夹、不在库内、名字非法，或系统拒绝了这次创建。
    """
    root = Path(root)
    destination = Path(parent)
    _guard(root, destination)
    wanted = clean_name(name, error=AlbumError)
    chosen = free_name_in(destination, wanted)
    if chosen is None:
        raise AlbumError(f"「{wanted}」这个名字太长，再加编号也放不下了。", path=destination)
    created = destination / chosen
    try:
        # 一次系统调用，不搬数据 —— 界面上不必为它开后台线程（粘贴一次几百 MB 才需要）。
        created.mkdir()
    except FileExistsError as error:
        # 有人刚好在同一刻建出了同名目录：如实说不行，不替用户决定谁该消失。
        raise AlbumError(
            f"这里已经有一个叫「{chosen}」的东西了。",
            path=created,
            detail=repr(error),
        ) from error
    except OSError as error:
        raise AlbumError(
            f"没能新建相册：{os_reason(error, is_dir=True)}",
            path=created,
            detail=repr(error),
        ) from error
    # 记账失败**不推翻**已经建好的文件夹：用户手里已经多了一个目录，如实返回 False
    # 让界面说明「没记上日志」就够了。
    journaled = append_record(
        root,
        WriteRecord(
            action=ACTION_CREATE,
            source=destination,
            target=created,
            result=RESULT_OK,
            is_dir=True,
        ),
    )
    return CreatedAlbum(album=FolderRef.from_path(created), journaled=journaled)


def _guard(root: Path, destination: Path) -> None:
    """确认这一层可以往里建东西（界面上只传当前层，这里是第二道保险）。"""
    if not destination.is_dir():
        raise AlbumError(f"「{destination.name}」不是一个文件夹。", path=destination)
    if not paths.is_within(root, destination):
        raise AlbumError(f"只能在库里的文件夹中新建相册：{destination}。", path=destination)
    if destination == paths.config_dir(root):
        raise AlbumError("这是 Gallery 自己的配置目录，不能往里建相册。", path=destination)


__all__ = ["DEFAULT_ALBUM_NAME", "CreatedAlbum", "create_album"]
