"""库目录扫描。设计要点：**流式**（生成器，先显示前 200 张）、**可取消**（只收 ``should_cancel``
回调，core 不认识线程）、**不炸**（没权限的目录、掉线的外接盘都记进 ``ScanReport`` 后继续）。
符号链接一律跳过。三个遍历函数与 :func:`is_visible_path` / :func:`describe_entry` **可见性规则
完全一致**：隐藏项、配置目录、符号链接都不算 —— 规则只有一份，两边各写一遍迟早会漂。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from gallery.core.cancellation import never_cancel
from gallery.core.collation import collation_key
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.i18n import t, t_plural
from gallery.core.library.photos import (
    FolderRef,
    GridEntry,
    PhotoRef,
    is_supported_media,
)
from gallery.core.paths import resolved


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
            parts.append(t_plural("library.scanner.folders_unreadable", len(self.skipped_dirs)))
        if self.unreadable_files:
            parts.append(t_plural("library.scanner.files_unreadable", len(self.unreadable_files)))
        if not parts:
            return ""
        return t("library.scanner.separator").join(parts) + t("library.scanner.issue_suffix")


def iter_photos(
    root: Path,
    *,
    recursive: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    report: ScanReport | None = None,
) -> Iterator[PhotoRef]:
    """遍历 ``root`` 下受支持的照片与视频（视频按扩展名认，同一个 ``PhotoRef``）。

    隐藏项（以 ``.`` 开头，含配置目录）一律跳过；产出顺序按名称排序，保证同一目录树每次结果一致。
    :raises LibraryNotAccessibleError: 根目录不存在、不是目录或没有读取权限。
    """
    root = Path(root)
    _require_directory(root)

    cancelled = should_cancel or never_cancel
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
            if _is_hidden_name(name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        subdirectories.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not is_supported_media(Path(name)):
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
    """列出**一个目录**的内容：子文件夹在前，照片与视频在后，各自按名称排序。

    相册模式逐层展开用它，平铺模式仍用 :func:`iter_photos` 一次递归到底；文件夹在前是刻意的。
    :raises LibraryNotAccessibleError: 目录不存在或没有读取权限。
    """
    directory = Path(directory)
    _require_directory(directory)
    cancelled = should_cancel or never_cancel
    listed = _list_dir(directory, report)
    if listed is None:
        return

    folders: list[FolderRef] = []
    photos: list[PhotoRef] = []
    for entry in sorted(listed, key=_sort_key):
        if cancelled():
            return
        name = entry.name
        if _is_hidden_name(name):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                stat = entry.stat(follow_symlinks=False)
                folders.append(FolderRef(path=Path(entry.path), mtime_ns=stat.st_mtime_ns))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if not is_supported_media(Path(name)):
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


def has_album(root: Path) -> bool:
    """``root`` 底下**有没有**相册。

    **只看根目录那一层就够了**：:func:`iter_albums` 的定义里，根目录下任何一个可见子目录自己
    就是一个相册（哪怕子树里一张照片都没有，那也是空相册）。于是「有没有相册」与「根目录下有
    没有可见子目录」是同一个问题，一次 ``scandir`` 答完。可见性规则与 :func:`iter_albums` 一致。
    """
    entries = _list_dir(Path(root), None)
    if entries is None:
        return False
    for entry in entries:
        if _is_hidden_name(entry.name):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                return True
        except OSError:
            continue  # 列得出来却读不动：当作不是相册，别的条目接着看
    return False


def iter_albums(
    root: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    report: ScanReport | None = None,
) -> Iterator[FolderRef]:
    """递归列出 ``root`` 下的**每一个**文件夹（相册），按相对路径排序（ADR 0019）。

    产出顺序是**路径序**（``京都`` → ``京都/大阪`` → ``京都/宇治`` → ``奈良``），按每一段逐级比较，
    不是字符串整体排序；``recursive`` 配置在这里**不生效**，逐层浏览任何时候都能进到子相册。
    :raises LibraryNotAccessibleError: 根目录不存在、不是目录或没有读取权限。
    """
    root = Path(root)
    _require_directory(root)
    cancelled = should_cancel or never_cancel
    found: list[FolderRef] = []
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
            if _is_hidden_name(name):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _note_dir(report, Path(entry.path), exc)
                continue
            subdirectories.append(Path(entry.path))
            found.append(FolderRef(path=Path(entry.path), mtime_ns=stat.st_mtime_ns))
        stack.extend(reversed(subdirectories))
    yield from sorted(found, key=_album_sort_key)


def _album_sort_key(folder: FolderRef) -> tuple[str, ...]:
    """相册的路径序：按**每一段**比，于是父相册后面紧跟着它自己的子树。"""
    return tuple(collation_key(part) for part in folder.path.parts)


def is_visible_path(root: Path, path: Path) -> bool:
    """这个路径在**形状上**是不是扫描看得见的那种。

    隐藏项（以 ``.`` 开头，含配置目录与 ``._`` 资源分叉）不算；**必须在库内**，且**不跟符号链接**
    （先解析成真正指向的地方再判，链到库外自动被拒）。**扩展名那条不在这里**：目录没扩展名，判不了，
    由调用方各自判（见 :func:`~gallery.core.library.photos.is_supported_media`）。
    """
    try:
        relative = resolved(path).relative_to(resolved(root))
    except (OSError, ValueError):
        return False
    return not any(_is_hidden_name(part) for part in relative.parts)


def describe_entry(root: Path, path: Path) -> GridEntry | None:
    """把一个路径变成网格里的一条（``PhotoRef`` 或 ``FolderRef``）。

    供实时监听使用：事件只给路径，大小与修改时间现 stat 一次。这里**不读像素、不读 EXIF**，
    产出与扫描完全同构，因此插进网格的东西和扫出来的没有区别。
    :return: 扫描看不见它、已经不在了、或者压根不是受支持的媒体时返回 ``None``。
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
    if not is_supported_media(target):
        return None
    return PhotoRef(path=target, size=info.st_size, mtime_ns=info.st_mtime_ns)


def _require_directory(path: Path) -> None:
    """不是目录（或压根不存在）就抛。全仓库这一句话只有这一处，别的调用点引用它。"""
    if not Path(path).is_dir():
        raise LibraryNotAccessibleError(
            t("library.scanner.folder_unavailable", path=path),
            path=Path(path),
        )


def _is_hidden_name(name: str) -> bool:
    """这个名字扫描看不见：``.gitignore``、``._`` 资源分叉、``.Trash``、配置目录。

    **配置目录已经被这条盖住了**（名字本来就以点开头，见
    :data:`gallery.core.constants.CONFIG_DIR_NAME`），再写 ``or name == CONFIG_DIR_NAME`` 看着像多
    一道保险，其实是死的。「跳过什么只有一份」是本模块的立身之本，所以这条规则也只在这里写一次。
    """
    return name.startswith(".")


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
    return collation_key(entry.name)


__all__ = [
    "ScanReport",
    "describe_entry",
    "has_album",
    "is_visible_path",
    "iter_albums",
    "iter_entries",
    "iter_photos",
]
