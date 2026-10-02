"""重命名照片与相册。

这是本项目里少数几个**会改动用户文件**的操作（见 ADR 0004），所以规矩最重：

- **只改名，不搬家**：目标永远是源所在的同一个目录，永不跨越文件系统。
- **绝不覆盖**：目标已存在就报错，让用户改个名字，而不是替用户决定谁该消失
  （POSIX 的 ``rename`` 会静默覆盖，这一点必须由我们自己挡）。
- **照片的扩展名不可改**：Gallery 按扩展名判断支持哪些格式，改了扩展名等于让一张
  照片从库里凭空消失。
- **不能以「.」开头**：扫描时以点开头的条目会被跳过（见 ``scanner.iter_entries``），
  改完名字照片就从 Gallery 里「不见了」，而它明明还在硬盘上。
- **每一步都记账**：成功、失败、撤销都写进配置目录下的 ``journal/``（见 ``core.journal``）。
  撤销就是照着最后一条记录改回去；「该撤哪一条」由 ``metadata.undo`` 那一摞共用的栈决定
  （改名与移到废纸篓共用同一个 ⌘Z，见 ADR 0008）。

本模块不弹窗、不问「要不要继续」：被拒绝就抛 :class:`~gallery.core.errors.RenameError`，
由界面决定怎么告诉用户。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gallery.core import paths
from gallery.core.errors import RenameError
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.journal import (
    ACTION_RENAME,
    ACTION_UNDO,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import clean_name


@dataclass(frozen=True, slots=True)
class RenameResult:
    """一次重命名的结果。"""

    #: 改名之后的新引用（名字没变时就是原引用）。
    entry: GridEntry
    #: 改之前的引用；名字没变时与 :attr:`entry` 相同。
    previous: GridEntry
    #: 是否真的动了磁盘。名字与原来一致时为 ``False``，此时什么都没写。
    renamed: bool
    #: 是否留下了可回滚的日志记录。改名成功但记账失败时为 ``False``，
    #: 界面要据此告诉用户「这次没法用 ⌘Z 撤销」。
    journaled: bool = True


def validate_name(name: str, entry: GridEntry) -> str:
    """把用户输入变成一个可以直接用的文件名，不合法就抛异常。

    与条目类型无关的那一半（空、以点开头、带分隔符、超长）在
    :func:`~gallery.core.metadata.naming.clean_name` 里，**只有一份实现** —— 新建相册
    （ADR 0022）也判这同一套规矩。这里只补上照片特有的一条：

    - 没写扩展名的**补回原来的扩展名**（访达的习惯）；
    - 写了别的扩展名的**直接拒绝** —— 改了扩展名，这个文件就不再是 Gallery 认得的照片。
    """
    cleaned = clean_name(name, error=RenameError)
    if isinstance(entry, PhotoRef):
        cleaned = _keep_suffix(entry, cleaned)
    return cleaned


def rename_entry(root: Path, entry: GridEntry, new_name: str) -> RenameResult:
    """把一个条目改名，结果写进日志。

    :raises RenameError: 名字不合法、目标已被占用、越权操作，或系统拒绝了这次改名。
    """
    root = Path(root)
    if isinstance(entry, GroupRef):
        # 分组不是磁盘上的东西，改名没有对象。界面已经拦了一道，这里是第二道：
        # 漏了也必须是句说人话的错，而不是在别处炸出一个 AttributeError。
        cover = entry.cover
        raise RenameError(
            f"「{entry.label}」是一个分组，不是磁盘上的文件，改不了名。"
            "双击进入分组后选中里面的照片再改名。",
            path=cover.path if cover is not None else None,
        )
    source = Path(entry.path)
    target = source.with_name(validate_name(new_name, entry))
    if target == source:
        # 名字没变就什么都不做 —— 也不写日志，免得把「没改」记成一次改动。
        return RenameResult(entry=entry, previous=entry, renamed=False)
    _guard(root, source, target)
    if target.exists():
        raise RenameError(
            f"这里已经有一个叫「{target.name}」的东西了，换个名字。",
            path=target,
        )
    is_dir = isinstance(entry, FolderRef)
    try:
        # 同一目录内的改名是原子的：要么名字换成了，要么什么都没发生。
        source.rename(target)
    except OSError as exc:
        append_record(
            root,
            WriteRecord(
                action=ACTION_RENAME,
                source=source,
                target=target,
                result=RESULT_FAILED,
                message=_os_reason(exc),
                is_dir=is_dir,
            ),
        )
        raise RenameError(
            f"改名失败：{_os_reason(exc)}",
            path=source,
            detail=repr(exc),
        ) from exc
    logged = append_record(
        root,
        WriteRecord(
            action=ACTION_RENAME,
            source=source,
            target=target,
            result=RESULT_OK,
            is_dir=is_dir,
        ),
    )
    # logged 为 False 时改动照样落地，只是没有可回滚的记录 —— 由界面如实告知。
    return RenameResult(
        entry=_relocated(entry, target),
        previous=entry,
        renamed=True,
        journaled=logged,
    )


def undo_rename_record(root: Path, record: WriteRecord) -> RenameResult:
    """把一条改名记录改回去。

    「哪一条该撤」由 :mod:`gallery.core.metadata.undo` 照着日志重放决定，本模块只管
    干活 —— 撤销栈不再藏在各自的模块里，改名与删除才能共用同一个 ⌘Z。

    :raises RenameError: 记录在，但文件已经不在原处（或原处被别的文件占了）。
    """
    root = Path(root)
    source, target = record.target, record.source  # 反过来改回去
    if not source.exists():
        raise RenameError(
            f"撤销不了：找不到「{source.name}」，它可能已经被移动或删除了。",
            path=source,
        )
    if target.exists():
        raise RenameError(
            f"撤销不了：「{target.name}」已经被别的文件占用了。",
            path=target,
        )
    # 引用要在改名**之前**取：改完之后旧路径已经不存在了。
    previous = _reference(source, is_dir=record.is_dir)
    try:
        source.rename(target)
    except OSError as exc:
        raise RenameError(
            f"撤销失败：{_os_reason(exc)}",
            path=source,
            detail=repr(exc),
        ) from exc
    append_record(
        root,
        WriteRecord(
            action=ACTION_UNDO,
            source=source,
            target=target,
            result=RESULT_OK,
            is_dir=record.is_dir,
        ),
    )
    return RenameResult(
        entry=_reference(target, is_dir=record.is_dir),
        previous=previous,
        renamed=True,
        journaled=True,
    )


def _guard(root: Path, source: Path, target: Path) -> None:
    """确认这次改名落在允许的范围内。

    扫描器本来就不会把点开头的目录摆进网格，这里仍然挡一道：这是唯一会动用户
    文件的入口，值得对「库根」「配置目录」多一层保险。
    """
    root = Path(root)
    if source.resolve() == root.resolve():
        raise RenameError("这是库本身，改名会让 Gallery 找不到照片。", path=source)
    if not paths.is_within(root, source) or not paths.is_within(root, target):
        raise RenameError(f"只能重命名库里的东西：{source}。", path=source)
    if source == paths.config_dir(root) or target == paths.config_dir(root):
        raise RenameError("这是 Gallery 自己的配置目录，不能改名。", path=source)


def _keep_suffix(entry: PhotoRef, cleaned: str) -> str:
    """照片改名不许换扩展名；只写主干时把原扩展名补回去。"""
    suffix = Path(cleaned).suffix
    if not suffix:
        return cleaned + entry.suffix
    if suffix.lower() != entry.suffix:
        raise RenameError(
            f"不能改扩展名：{entry.suffix} 换成 {suffix} 之后，"
            "这个文件就不再是 Gallery 认得的照片了。",
            path=entry.path,
        )
    return cleaned


def _relocated(entry: PhotoRef | FolderRef, target: Path) -> GridEntry:
    """把引用改指到新路径，大小与修改时间沿用原值（改名不改内容）。"""
    if isinstance(entry, FolderRef):
        return FolderRef(path=target, mtime_ns=entry.mtime_ns)
    return PhotoRef(path=target, size=entry.size, mtime_ns=entry.mtime_ns)


def _reference(path: Path, *, is_dir: bool) -> GridEntry:
    """按当前磁盘状态重新读一个引用。"""
    if is_dir:
        return FolderRef.from_path(path)
    return PhotoRef.from_path(path)


def _os_reason(exc: OSError) -> str:
    """把系统错误翻译成用户看得懂的一句话。"""
    reason = exc.strerror or exc.__class__.__name__
    if isinstance(exc, PermissionError):
        return f"没有权限（{reason}）。请检查这个文件是否被其他程序占用或只读。"
    return f"{reason}。它可能正在被其他程序使用。"


__all__ = ["RenameResult", "rename_entry", "undo_rename_record", "validate_name"]
