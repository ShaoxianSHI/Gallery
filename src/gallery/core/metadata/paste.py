"""把文件**复制**进某个目录（界面上是「粘贴」⌘V，ADR 0018；「复制到相册」是同一个动作，
「复制」⌘D 是把每一项复制到它自己所在的那一层，ADR 0044）。
**只增不改**：粘进来的是新文件，源文件一个字节不动，于是**没有「撤销」这一说** —— 撤销粘贴就是
删文件，而删除只允许用户亲手触发（硬性约束）。**绝不覆盖**：目标已有同名时按访达的规矩改个名再粘
（``一张.jpg`` → ``一张 2.jpg``）；已经在目标目录里的那几项各复制一份；复制到一半失败时只删**本模块
自己刚写了一半的那个文件**，不留给用户。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from gallery.core import paths
from gallery.core.cancellation import never_cancel
from gallery.core.errors import PasteError
from gallery.core.i18n import t
from gallery.core.metadata.failures import Failure, os_reason, same_directory
from gallery.core.metadata.journal import (
    ACTION_PASTE,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import duplicate_base, free_target, occupied_names


@dataclass(frozen=True, slots=True)
class PastedFile:
    """粘进去的一项：源在哪儿、变成了哪个新文件。"""

    #: 剪贴板里那一份（**没有被移动、没有被改动**）。
    source: Path
    #: 目标目录里新出现的那个。
    created: Path

    @property
    def renamed(self) -> bool:
        """是不是为了不覆盖已有的同名而改了名。"""
        return self.source.name != self.created.name


@dataclass(frozen=True, slots=True)
class PasteResult:
    """一次粘贴的结果。界面据此拼横幅。"""

    #: 真的粘进去的（可能为空：全部失败）。
    pasted: tuple[PastedFile, ...] = ()
    #: 想粘没粘成的。
    failures: tuple[Failure, ...] = ()
    #: 是否都记下了账。记账失败**不撤销已经粘好的文件**（见 ``journal`` 模块文档）。
    journaled: bool = True

    @property
    def did_anything(self) -> bool:
        return bool(self.pasted)


def wanted_name(source: Path, destination: Path) -> str:
    """这一项在 ``destination`` 里该叫什么。

    **已经在那一层的，给「的副本」而不是照原名再走一遍编号** —— 照原名会得到 ``一张 2.jpg``，
    用户分不出它是新粘来的还是自己早先就有的那一份。「同一目录」这一判据与
    :mod:`~gallery.core.metadata.transfer` 共用这一个函数，否则两处会漂移。
    """
    if same_directory(source.parent, destination):
        return duplicate_base(source.name, whole=source.is_dir())
    return source.name


def copy_into(
    root: Path,
    target: Path,
    sources: Sequence[Path],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> PasteResult:
    """把 ``sources`` 复制进 ``target``，返回结果。

    :param root: 库根。记账写在它下面（``journal/``），与另外两个写操作同一处。
    :param should_cancel: 每粘一项之前问一次；只在**项与项之间**取消，粘到一半会留下半截文件。
    :raises PasteError: ``target`` 不是目录，或者它落在库外。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise PasteError(t("metadata.paste.not_folder", name=destination.name))
    _guard(root, destination)

    cancelled = should_cancel or never_cancel
    batch = uuid4().hex[:12]
    pasted: list[PastedFile] = []
    failures: list[Failure] = []
    journaled = True
    # **目录只扫一次**：逐项 ``exists()`` 加编号在这一层是「路径数 × 已有同名数」次系统调用。
    # 扫不动时是 ``None``（「不知道」），那一层退回逐项探测，宁可慢也不猜。
    taken = occupied_names(destination)

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure.at(source, message=t("metadata.paste.source_gone")))
            continue
        if source.is_dir() and paths.is_within(source, destination):
            failures.append(Failure.at(source, message=t("metadata.paste.into_self")))
            continue
        if cancelled():
            break
        outcome = copy_one_into(root, destination, source, batch=batch, taken=taken)
        journaled = journaled and outcome.journaled
        if outcome.item is not None:
            pasted.append(outcome.item)
        if outcome.failure is not None:
            failures.append(outcome.failure)
    return PasteResult(
        pasted=tuple(pasted),
        failures=tuple(failures),
        journaled=journaled,
    )


def duplicate(
    root: Path,
    sources: Sequence[Path],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> PasteResult:
    """把每一项**复制到它自己所在的那一层**（界面上是「复制」⌘D，ADR 0044）。

    与 :func:`copy_into` 的区别只在于目标目录：这里每一项的目标是它自己的父目录，于是**一次调用里
    可能有好几个目标目录**。所以按父目录分组，每组走一次 :func:`copy_into` —— 每个目录仍只扫一次。
    **取消不在这一层问**：每组进去后的第一项之前 :func:`copy_into` 自己就会问一次。
    """
    groups: dict[Path, list[Path]] = {}
    for raw_source in sources:
        source = Path(raw_source)
        groups.setdefault(source.parent, []).append(source)

    pasted: list[PastedFile] = []
    failures: list[Failure] = []
    journaled = True
    for destination, group in groups.items():
        outcome = copy_into(root, destination, group, should_cancel=should_cancel)
        pasted.extend(outcome.pasted)
        failures.extend(outcome.failures)
        journaled = journaled and outcome.journaled
    return PasteResult(
        pasted=tuple(pasted),
        failures=tuple(failures),
        journaled=journaled,
    )


@dataclass(frozen=True, slots=True)
class CopyOutcome:
    """复制一项的结果：成了、没成，还是记账没成（``failure`` 与 ``journaled`` 分开摆）。"""

    item: PastedFile | None = None
    failure: Failure | None = None
    journaled: bool = True


def copy_one_into(
    root: Path,
    destination: Path,
    source: Path,
    *,
    batch: str,
    taken: set[str] | None,
) -> CopyOutcome:
    """把**一项**复制进 ``destination`` —— 「复制」这件事在本项目里的**唯一实现**。
    :func:`copy_into`（⌘V 与「复制到相册」）与 :func:`gallery.core.metadata.transfer.move_into` 里
    「移到本来就在的那个相册 → 复制一份」那一支都走它，否则会出现第二份 ``copy2`` / ``copytree`` /
    半截清理，而那三样每一件都碰着「删除只允许用户亲手触发」这条硬性约束。
    :param taken: 这一批共用的一份「已经被占掉的名字」；**目录读不了时是 ``None``**。
    """
    is_dir = source.is_dir()
    created = free_target(destination, wanted_name(source, destination), taken, whole=is_dir)
    if created is None:
        return CopyOutcome(failure=Failure.at(source, message=t("metadata.paste.name_too_long")))
    try:
        if is_dir:
            # copytree 目标已存在会直接报错（它不做覆盖），半途失败也会自己收尾。
            shutil.copytree(source, created, copy_function=shutil.copy2)
        else:
            # copy2 连修改时间一起复制：库里默认按修改时间排序，粘进来的照片
            # 因此还留在它原来的时间位置上，而不是齐刷刷跳到今天。
            shutil.copy2(source, created)
    except FileExistsError:
        # 目标已经存在，**而那不是我们写了一半的东西，是用户自己放进去的** —— 绝不能清理它：
        # 清理那句 ``rmtree`` 删掉的是用户的文件夹连同里面所有照片（删除只允许用户亲手触发）。
        message = t("metadata.paste.exists")
        return CopyOutcome(
            failure=Failure.at(source, message=message),
            journaled=append_record(
                root,
                WriteRecord(
                    action=ACTION_PASTE,
                    source=source,
                    target=created,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=is_dir,
                    batch=batch,
                ),
            ),
        )
    except OSError as error:
        _remove_partial(created, is_dir)
        message = os_reason(error, action_key="copy")
        return CopyOutcome(
            failure=Failure.at(source, message=message),
            journaled=append_record(
                root,
                WriteRecord(
                    action=ACTION_PASTE,
                    source=source,
                    target=created,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=is_dir,
                    batch=batch,
                ),
            ),
        )
    return CopyOutcome(
        item=PastedFile(source=source, created=created),
        journaled=append_record(
            root,
            WriteRecord(
                action=ACTION_PASTE,
                source=source,
                target=created,
                result=RESULT_OK,
                is_dir=is_dir,
                batch=batch,
            ),
        ),
    )


def _remove_partial(created: Path, is_dir: bool) -> None:
    """把**本模块刚写了一半**的那个目标清掉 —— 只删这一条，别的文件一个都不碰。

    **只对已经确定是我们创建的目标调用**（``FileExistsError`` 那一条不归它管：那是用户
    自己放进去的，copytree 一个字节都还没写）。
    """
    try:
        if is_dir:
            shutil.rmtree(created, ignore_errors=True)
        else:
            created.unlink(missing_ok=True)
    except OSError:
        pass  # 清不掉就清不掉，如实留着总比假装删了强（横幅会说明没粘成）


def _guard(root: Path, destination: Path) -> None:
    """粘贴的守卫：**只查目标**。与 :func:`~gallery.core.metadata.transfer._guard` 正好相反：源
    **可以**在库外（访达里 ⌘C 复制进来的就是，ADR 0018）；目标必须在库内，落到库外则是孤儿文件。
    配置目录仍然挡掉。**库根本身是合法的目标**：要挡的是「跑到库外」，不是「落在库里」。
    「不覆盖」不归这里管 —— 那是 :mod:`~gallery.core.metadata.naming` 的活。
    """
    root = Path(root)
    try:
        paths.guard_within_root(root, destination, allow_root=True)
    except paths.GuardError as exc:
        raise PasteError(_paste_reason(destination, exc), path=destination) from exc


#: 拒绝粘贴的那几种原因 → 给用户的那句话。见 :data:`rename._GUARD_MESSAGES`。
#: **「库本身」这一条今天到不了**（库根本身是合法的粘贴目标），理由同
#: :data:`transfer._GUARD_MESSAGES`：留着是为了这张表是全的。
_GUARD_MESSAGES: Final = {
    paths.GuardReason.OUTSIDE_ROOT: "metadata.paste.guard.outside_root",
    paths.GuardReason.IS_ROOT: "metadata.paste.guard.is_root",
    paths.GuardReason.IS_CONFIG_DIR: "metadata.paste.guard.is_config_dir",
}


def _paste_reason(path: Path, error: paths.GuardError) -> str:
    """按拒绝的**原因**给一句中文 —— 理由见 :func:`rename._rename_reason`。"""
    return t(_GUARD_MESSAGES[error.reason], path=path)


__all__ = [
    "CopyOutcome",
    "Failure",
    "PasteResult",
    "PastedFile",
    "copy_into",
    "copy_one_into",
    "duplicate",
    "wanted_name",
]
