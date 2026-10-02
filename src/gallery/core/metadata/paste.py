"""把文件**复制**进某个目录（界面上是「粘贴」⌘V，见 ADR 0018）。

源既可以是剪贴板里的东西（⌘V），也可以是网格里选中的照片（「复制到相册」，
见 ADR 0019）—— 两者是同一个动作：把若干项变成目标目录里的新文件。因此它们共用
:func:`copy_into`，也共用 ``journal`` 里的 ``action=paste`` 那一种记录。

这是第三个会往用户目录里写东西的操作，前两个是改名（ADR 0004）与移到废纸篓
（ADR 0008），但它与它们有一处根本不同，写在这里免得日后有人照着前两个的规矩套错：

- **只增不改**：粘进来的是**新文件**，源文件一个字节都不动（访达的 ⌘C / ⌘V 也是这样，
  源还留在原地）。于是**没有「撤销」这一说** —— 撤销一次粘贴就是删文件，而删除在这个
  项目里只允许用户在界面上亲手触发（红线 1，见 ``AGENTS.md``）。本模块仍然记账
  （:data:`~gallery.core.metadata.journal.ACTION_PASTE`），但那一条**不进撤销栈**
  （``core.metadata.undo`` 只重放它认识的两种动作）。
- **绝不覆盖**：目标已有同名时不覆盖、也不把整批停下来（那样十张里九张能粘也粘不成），
  而是按**系统自己的规矩**改个名再粘：``一张.jpg`` → ``一张 2.jpg`` → ``一张 3.jpg``。
  这条是实测的：在访达里对同一个文件连按两次「复制项目」，中文系统给出的正是
  ``一张 2.jpg`` / ``一张 3.jpg``，文件夹同理（``京都`` → ``京都 2``）。

另有两条边界，都是为了不把用户的东西搅乱：

- **已经在目标目录里的原样跳过**（用户明确要求）：在同一个文件夹里 ⌘C 再 ⌘V，
  不该凭空多出一份「一张 2.jpg」。
- **不把文件夹粘进它自己**：那会无限递归。粘进它自己的子目录同样拒绝。

最后一条只在本模块内部生效、也只删**本模块自己刚写了一半的那个文件**：复制到一半失败时
把那个半截文件清掉，不留给用户。留着它才是真的把用户的相册弄脏了（库里会出现一张
解码不了的「照片」）。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from gallery.core import paths
from gallery.core.cancellation import never_cancel
from gallery.core.errors import PasteError
from gallery.core.metadata.journal import (
    ACTION_PASTE,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_name_in


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
class Skipped:
    """没有粘的一项，以及**为什么**（这句直接给用户看）。"""

    source: Path
    message: str


@dataclass(frozen=True, slots=True)
class Failure:
    """粘失败的一项，以及失败的原因。"""

    source: Path
    message: str


@dataclass(frozen=True, slots=True)
class PasteResult:
    """一次粘贴的结果。三类分开摆，界面据此拼横幅。"""

    #: 真的粘进去的（可能为空：全部跳过或全部失败）。
    pasted: tuple[PastedFile, ...] = ()
    #: 刻意没粘的（本来就在目标目录里、文件夹粘进自己）。
    skipped: tuple[Skipped, ...] = ()
    #: 想粘没粘成的。
    failures: tuple[Failure, ...] = ()
    #: 是否都记下了账。记账失败**不撤销已经粘好的文件**（见 ``journal`` 模块文档）。
    journaled: bool = True

    @property
    def did_anything(self) -> bool:
        return bool(self.pasted)


def copy_into(
    root: Path,
    target: Path,
    sources: Sequence[Path],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> PasteResult:
    """把 ``sources`` 复制进 ``target``，返回结果。

    :param root: 库根。记账写在它下面（``journal/``），与另外两个写操作同一处。
    :param target: 目标目录，必须已经存在且是个目录。
    :param sources: 剪贴板里的那些路径，可以来自库外（访达里 ⌘C 也算）。
    :param should_cancel: 每粘一项之前问一次。只在**项与项之间**取消：粘到一半停下来会
        留下半截文件，那比粘完更糟。
    :raises PasteError: ``target`` 不是目录。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise PasteError(f"「{destination.name}」不是一个文件夹。")

    cancelled = should_cancel or never_cancel
    batch = uuid4().hex[:12]
    pasted: list[PastedFile] = []
    skipped: list[Skipped] = []
    failures: list[Failure] = []
    journaled = True

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure(source=source, message="源文件已经不在了。"))
            continue
        if _same_directory(source.parent, destination):
            skipped.append(Skipped(source=source, message=f"已经在「{destination.name}」里了。"))
            continue
        if source.is_dir() and paths.is_within(source, destination):
            failures.append(
                Failure(source=source, message="不能把文件夹粘进它自己或它自己的子目录。")
            )
            continue
        if cancelled():
            break
        outcome = _copy_one(root, destination, source, batch)
        journaled = journaled and outcome.journaled
        if outcome.item is not None:
            pasted.append(outcome.item)
        if outcome.skipped is not None:
            skipped.append(outcome.skipped)
        if outcome.failure is not None:
            failures.append(outcome.failure)
    return PasteResult(
        pasted=tuple(pasted),
        skipped=tuple(skipped),
        failures=tuple(failures),
        journaled=journaled,
    )


@dataclass(frozen=True, slots=True)
class _Outcome:
    """单项的结果：粘成了、被跳过、还是失败了（外加记账成没成）。"""

    item: PastedFile | None = None
    skipped: Skipped | None = None
    failure: Failure | None = None
    journaled: bool = True


def _copy_one(root: Path, destination: Path, source: Path, batch: str) -> _Outcome:
    """把一项复制进 ``destination``。"""
    name = free_name_in(destination, source.name)
    if name is None:
        return _Outcome(
            failure=Failure(source=source, message="这个名字太长，再加编号也放不下了。")
        )
    created = destination / name
    try:
        if source.is_dir():
            # copytree 目标已存在会直接报错（它不做覆盖），半途失败也会自己收尾。
            shutil.copytree(source, created, copy_function=shutil.copy2)
        else:
            # copy2 连修改时间一起复制：库里默认按修改时间排序，粘进来的照片
            # 因此还留在它原来的时间位置上，而不是齐刷刷跳到今天。
            shutil.copy2(source, created)
    except OSError as error:
        _remove_partial(created, source.is_dir())
        message = _copy_error_message(error)
        return _Outcome(
            failure=Failure(source=source, message=message),
            journaled=append_record(
                root,
                WriteRecord(
                    action=ACTION_PASTE,
                    source=source,
                    target=created,
                    result=RESULT_FAILED,
                    message=message,
                    is_dir=source.is_dir(),
                    batch=batch,
                ),
            ),
        )
    return _Outcome(
        item=PastedFile(source=source, created=created),
        journaled=append_record(
            root,
            WriteRecord(
                action=ACTION_PASTE,
                source=source,
                target=created,
                result=RESULT_OK,
                is_dir=source.is_dir(),
                batch=batch,
            ),
        ),
    )


def _remove_partial(created: Path, is_dir: bool) -> None:
    """把**本模块刚写了一半**的那个目标清掉。

    只删这一条：它是我们自己建的、还没粘完的半截文件。别的文件一个都不碰。
    """
    try:
        if is_dir:
            shutil.rmtree(created, ignore_errors=True)
        else:
            created.unlink(missing_ok=True)
    except OSError:
        pass  # 清不掉就清不掉，如实留着总比假装删了强（横幅会说明没粘成）


def _copy_error_message(error: OSError) -> str:
    """一句说清为什么没粘成的中文。"""
    if isinstance(error, shutil.SameFileError):
        return "源和目标是同一个文件。"
    reason = error.strerror or str(error)
    if "File name too long" in reason or "文件名太长" in reason:
        return "名字太长，这个磁盘放不下。"
    if "No space left" in reason or "磁盘空间不足" in reason:
        return "磁盘空间不足。"
    if isinstance(error, PermissionError):
        return "没有权限写进这个文件夹。"
    return f"复制失败：{reason}。"


def _same_directory(left: Path, right: Path) -> bool:
    """两个路径是不是同一个文件夹（**按磁盘上的真实位置**比，不比字面写法）。

    访达的剪贴板里可能是 ``/private/var/…`` 这样的写法，而界面上拿到的是 ``/var/…``；
    不解析就比不相等，于是「已经在同一目录了」这条判断会失效，粘出一堆重复文件。
    """
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


__all__ = [
    "Failure",
    "PasteResult",
    "PastedFile",
    "Skipped",
    "copy_into",
]
