"""新建相册：在某一层里 ``mkdir`` 一个空文件夹（界面上是「新建相册」⇧⌘N，见 ADR 0022）。

访达在文件夹空白处右键有一条「新建文件夹」⇧⌘N，按下去当场多出一个文件夹，光标已经在
等着起名。Gallery 里的相册就是磁盘上的子文件夹，所以这条命令在这里也是同一条：建出来
的也是一个**普通的空文件夹**，用户随后往里丢照片、或者在访达里摆弄它，都不受 Gallery 管。

这是本项目里**第四个**会往用户目录里写东西的操作（前三个是改名、移到废纸篓与粘贴）。
红线同样适用，而它与前三个有一处根本不同，写在这里免得日后有人照着别的模块套错：

- **只增不改**：建出来的是一个**全新的空文件夹**，用户的照片一个字节都没动，用户原有的
  目录结构也没被改动 —— 变的只是「这个目录的内容多了一项」。因此不碰任何照片、不移动
  任何东西，红线 1 那条「绝不删除、移动用户照片」压根不涉及。
- **绝不覆盖**：目标已有同名时不覆盖，而是按**访达自己的规矩**改叫「新建相册 2」
  （``metadata.naming.free_name_in``，与粘贴、移动到相册共用同一份实现）。
- **不进撤销栈**：撤销一次新建就是**删掉一个文件夹**，而删除在这个项目里只允许用户在
  界面上亲手触发（红线 1）。本模块仍然记账（:data:`~gallery.core.metadata.journal.
  ACTION_CREATE`），但 ``metadata.undo`` 只重放它认识的几种动作。
  紧接着用户给新相册起的那个名字**是**一次普通改名 —— 记账、``⌘Z`` 可撤销。
- **只建空文件夹，不往里放任何东西**：不在这里替用户造目录结构，也不搬照片进去 ——
  「后台整理」是红线 1 明确排除的。

模块放在 ``metadata`` 而不是 ``library``：会改动用户文件的那一摞（rename / trash /
paste / transfer）都在这里，红线 1 与红线 4 要能被一眼看见（见 ADR 0006 决策 1）。

本模块不弹窗、不问「要不要继续」：建不成就抛 :class:`~gallery.core.errors.AlbumError`，
由界面决定怎么告诉用户。
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.errors import AlbumError
from gallery.core.library.photos import FolderRef
from gallery.core.metadata.journal import (
    ACTION_CREATE,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import clean_name, free_name_in

#: 新建出来的相册默认叫什么。
#:
#: **与菜单项同名**（「新建相册」），不用「未命名相册」：用户一看就知道刚才是自己干的，
#: 而它多半活不过下一次改名（建完就直接进行内重命名）。
DEFAULT_ALBUM_NAME: Final = "新建相册"


@dataclass(frozen=True, slots=True)
class CreatedAlbum:
    """新建出来的那个相册。"""

    #: 刚落地的那个文件夹（引用已经按磁盘上的真实状态读过一遍）。
    album: FolderRef
    #: 是否留下了日志记录。文件夹已经建好、但记账失败时为 ``False`` ——
    #: 这不影响用户手里的东西，只是没有历史可查。
    journaled: bool = True

    @property
    def path(self) -> Path:
        return self.album.path

    @property
    def name(self) -> str:
        return self.album.name


def create_album(root: Path, parent: Path, name: str = DEFAULT_ALBUM_NAME) -> CreatedAlbum:
    """在 ``parent`` 里建一个叫 ``name`` 的空相册。

    :param root: 库根。记账写在它下面（``journal/``），与另外几个写操作同一处。
    :param parent: 建在哪一层，必须已经存在、且在库内。
    :param name: 想用的名字；默认是「新建相册」。同名会自动改叫「新建相册 2」
        （访达的规矩，绝不覆盖用户已有的目录）。
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
        # 一次系统调用，不搬数据 —— 所以界面上不必为它开后台线程（粘贴一次几百 MB 才需要）。
        created.mkdir()
    except FileExistsError as error:
        # 有人刚好在同一刻建出了同名目录（访达窗口、另一个 Gallery、粘贴到一半的批量）。
        # 这是**如实说不行**，不是替用户决定谁该消失。
        raise AlbumError(
            f"这里已经有一个叫「{chosen}」的东西了。",
            path=created,
            detail=repr(error),
        ) from error
    except OSError as error:
        raise AlbumError(
            f"没能新建相册：{_os_reason(error)}",
            path=created,
            detail=repr(error),
        ) from error
    # 记账失败**不推翻**已经建好的文件夹：用户手里已经多了一个目录，如实返回 False
    # 让界面说明「没记上日志」就够了（与 journal 模块文档同一条取向）。
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
    """确认这一层可以往里建东西。

    界面本来只会把当前这一层传进来（ADR 0022 决策 1），这里仍然是第二道：这是唯一会
    **凭空多出**一个目录的入口，值得对「库外」「配置目录」多一层保险。
    """
    if not destination.is_dir():
        raise AlbumError(f"「{destination.name}」不是一个文件夹。", path=destination)
    if not paths.is_within(root, destination):
        raise AlbumError(f"只能在库里的文件夹中新建相册：{destination}。", path=destination)
    if destination == paths.config_dir(root):
        raise AlbumError("这是 Gallery 自己的配置目录，不能往里建相册。", path=destination)


def _os_reason(exc: OSError) -> str:
    """把系统错误翻译成用户看得懂的一句话（与 ``rename._os_reason`` 同一套说法）。"""
    reason = exc.strerror or exc.__class__.__name__
    if isinstance(exc, PermissionError):
        return f"没有权限（{reason}）。请检查这个文件夹是否只读，或位于没有写权限的位置。"
    if exc.errno == errno.ENOSPC:
        return "磁盘空间不足。"
    return f"{reason}。它可能正在被其他程序使用。"


__all__ = ["DEFAULT_ALBUM_NAME", "CreatedAlbum", "create_album"]
