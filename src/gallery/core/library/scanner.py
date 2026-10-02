"""库目录扫描。

三个设计要点：

- **流式**：返回生成器。界面可以先显示前 200 张，不必等整库扫完才出图。
- **可取消**：只接受一个 ``should_cancel`` 回调，core 因此完全不需要认识线程或事件循环。
- **不炸**：没有权限的目录、掉线的外接盘、读不了的文件都记进 ``ScanReport`` 后继续，
  而不是让整个库打不开——用户更可能想看到「还剩下哪些照片」。

符号链接一律跳过（既不进入链接目录也不收录链接文件）：跟随链接可能造成无限递归，
而 v1 的收益很小。

:func:`is_visible_path` 与 :func:`describe_entry` 回答的是同一个问题的单点版本
（「这一个路径算不算数 / 它是哪一条」）：实时监听拿到的是一条条事件路径，而不是一次
目录遍历，于是「跳过什么」的规则必须只有一份，两边各写一遍迟早会漂。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library.photos import FolderRef, GridEntry, PhotoRef, is_supported_photo


@dataclass(slots=True)
class ScanReport:
    """扫描过程中的非致命问题，供界面汇总提示。

    ``slots`` + 可变字段是刻意的：扫描是流式的，报告要在生成器边跑边填。
    """

    skipped_dirs: list[str] = field(default_factory=list)
    unreadable_files: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.skipped_dirs or self.unreadable_files)

    @property
    def summary(self) -> str:
        """一句话中文摘要，没有问题时返回空串。"""
        parts: list[str] = []
        if self.skipped_dirs:
            parts.append(f"{len(self.skipped_dirs)} 个文件夹无法读取")
        if self.unreadable_files:
            parts.append(f"{len(self.unreadable_files)} 个文件无法读取")
        if not parts:
            return ""
        return "、".join(parts) + "（可能是权限问题，或外接盘已断开）"


def iter_photos(
    root: Path,
    *,
    recursive: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    report: ScanReport | None = None,
) -> Iterator[PhotoRef]:
    """遍历 ``root`` 下受支持的照片。

    隐藏项（以 ``.`` 开头，含 ``.gitignore``、``._`` 资源分叉与配置目录）一律跳过。
    目录与文件的产出顺序按名称排序，保证同一目录树每次扫描结果一致。

    :raises LibraryNotAccessibleError: 根目录不存在、不是目录或没有读取权限。
    """
    root = Path(root)
    if not root.is_dir():
        raise LibraryNotAccessibleError(
            f"打不开这个文件夹：{root}。它可能已被移动、重命名或删除。",
            path=root,
        )

    cancelled = should_cancel if should_cancel is not None else _never_cancel
    stack: list[Path] = [root]
    while stack:
        if cancelled():
            return
        directory = stack.pop()
        entries = _list_dir(directory, report)
        if entries is None:
            continue
        subdirectories: list[Path] = []
        for entry in sorted(entries, key=_sort_key):
            if cancelled():
                return
            name = entry.name
            if name.startswith(".") or name == CONFIG_DIR_NAME:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        subdirectories.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not is_supported_photo(Path(name)):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _note_file(report, entry.path, exc)
                continue
            yield PhotoRef(path=Path(entry.path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        # 栈是后进先出，倒序压入才能让子目录按名称升序展开。
        stack.extend(reversed(subdirectories))


def iter_entries(
    directory: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    report: ScanReport | None = None,
) -> Iterator[FolderRef | PhotoRef]:
    """列出**一个目录**的内容：子文件夹在前，照片在后，各自按名称排序。

    相册模式逐层展开时用它；平铺模式仍然用 :func:`iter_photos` 一次递归到底。
    文件夹在前是刻意的：同一层里既有相册又有照片时，相册应该先被看到。

    :raises LibraryNotAccessibleError: 目录不存在或没有读取权限。
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise LibraryNotAccessibleError(
            f"打不开这个文件夹：{directory}。它可能已被移动、重命名或删除。",
            path=directory,
        )
    cancelled = should_cancel if should_cancel is not None else _never_cancel
    listed = _list_dir(directory, report)
    if listed is None:
        return

    folders: list[FolderRef] = []
    photos: list[PhotoRef] = []
    for entry in sorted(listed, key=_sort_key):
        if cancelled():
            return
        name = entry.name
        if name.startswith(".") or name == CONFIG_DIR_NAME:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                stat = entry.stat(follow_symlinks=False)
                folders.append(FolderRef(path=Path(entry.path), mtime_ns=stat.st_mtime_ns))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if not is_supported_photo(Path(name)):
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            _note_file(report, entry.path, exc)
            continue
        photos.append(PhotoRef(path=Path(entry.path), size=stat.st_size, mtime_ns=stat.st_mtime_ns))

    # 产出阶段也检查取消：语义与 iter_photos 保持一致 —— 取消就该停下。
    for folder in folders:
        if cancelled():
            return
        yield folder
    for photo in photos:
        if cancelled():
            return
        yield photo


def is_visible_path(root: Path, path: Path) -> bool:
    """这个路径在**形状上**是不是扫描看得见的那种。

    隐藏项（以 ``.`` 开头，含配置目录与 ``._`` 资源分叉）与配置目录一律不算，
    另外再加两条：

    - **必须在库内**：逐层浏览与实时监听都不许越出用户指定的文件夹；
    - **不跟符号链接**：先把路径解析成它真正指向的地方再判断，于是链到库外的目录
      自动被拒掉 —— 跟进链接既可能走出库，也可能无限递归。

    **扩展名那条不在这里**：目录没有扩展名，这里判不了，所以「不是照片的文件跳过」
    由调用方各自判（扫描器与监听器都是先问这里，再问
    :func:`~gallery.core.library.photos.is_supported_photo`）。

    路径已经不存在也照样答得出来（``resolve`` 不用 ``strict=True``）：监听恰恰最常
    问到这种路径。
    """
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return all(not part.startswith(".") and part != CONFIG_DIR_NAME for part in relative.parts)


def describe_entry(root: Path, path: Path) -> GridEntry | None:
    """把一个路径变成网格里的一条（``PhotoRef`` 或 ``FolderRef``）。

    供实时监听使用：事件只给出路径，大小与修改时间要现 stat 一次。这里**不读像素、
    不读 EXIF**，产出与扫描完全同构，因此插进网格的东西和扫出来的没有区别。

    :return: 扫描看不见它、已经不在了、或者压根不是照片时返回 ``None``。
    """
    target = Path(path)
    if not is_visible_path(root, target):
        return None
    try:
        info = target.stat(follow_symlinks=False)
    except OSError:
        return None  # 事件送到时文件已经被删掉了
    # 判类型用 stat 的模式位而不是 ``Path.is_dir``：后者没有 ``follow_symlinks=False``
    # 参数（3.13 才加），而这里必须判的是链接**本身**而不是它指向的东西。
    if S_ISDIR(info.st_mode):
        return FolderRef(path=target, mtime_ns=info.st_mtime_ns)
    if not S_ISREG(info.st_mode):
        return None  # 符号链接与其它特殊文件，扫描同样不收
    if not is_supported_photo(target):
        return None
    return PhotoRef(path=target, size=info.st_size, mtime_ns=info.st_mtime_ns)


def _list_dir(directory: Path, report: ScanReport | None) -> list[os.DirEntry[str]] | None:
    try:
        with os.scandir(directory) as scanner:
            return list(scanner)
    except OSError as exc:
        _note_dir(report, directory, exc)
        return None


def _note_dir(report: ScanReport | None, directory: Path, exc: OSError) -> None:
    if report is not None:
        report.skipped_dirs.append(f"{directory}（{exc.strerror or exc}）")


def _note_file(report: ScanReport | None, path: str, exc: OSError) -> None:
    if report is not None:
        report.unreadable_files.append(f"{path}（{exc.strerror or exc}）")


def _sort_key(entry: os.DirEntry[str]) -> str:
    return entry.name.lower()


def _never_cancel() -> bool:
    return False


__all__ = ["ScanReport", "describe_entry", "is_visible_path", "iter_entries", "iter_photos"]
