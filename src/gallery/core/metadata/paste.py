"""把文件**复制**进某个目录（界面上是「粘贴」⌘V，ADR 0018；「复制到相册」是同一个动作）。

只增不改这一点与改名（ADR 0004）、移到废纸篓（ADR 0008）根本不同，写在这里免得日后有人
照着前两个的规矩套错：

- **只增不改**：粘进来的是**新文件**，源文件一个字节都不动。于是**没有「撤销」这一说** ——
  撤销一次粘贴就是删文件，而删除在这个项目里只允许用户在界面上亲手触发（红线 1）。本模块仍然记账
  （:data:`~gallery.core.metadata.journal.ACTION_PASTE`），但那一条**不进撤销栈**。
- **绝不覆盖**：目标已有同名时不覆盖、也不把整批停下来（那会让十张里九张都粘不成），而是按
  **系统自己的规矩**改个名再粘：``一张.jpg`` → ``一张 2.jpg``（访达实测如此，文件夹同理）。
- 已经在目标目录里的原样跳过（用户明确要求），**不把文件夹粘进它自己或它自己的子目录**。

复制到一半失败时只删**本模块自己刚写了一半的那个文件**，不留给用户。
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
from gallery.core.metadata.failures import Failure, os_reason, same_directory
from gallery.core.metadata.journal import (
    ACTION_PASTE,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_target, occupied_names


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
    :raises PasteError: ``target`` 不是目录，或者它落在库外。
    """
    destination = Path(target)
    if not destination.is_dir():
        raise PasteError(f"「{destination.name}」不是一个文件夹。")
    _guard(root, destination)

    cancelled = should_cancel or never_cancel
    batch = uuid4().hex[:12]
    pasted: list[PastedFile] = []
    skipped: list[Skipped] = []
    failures: list[Failure] = []
    journaled = True
    # **目录只扫一次**：逐项 ``exists()`` 加编号在这一层是「路径数 × 已有同名数」次系统调用。
    # 扫不动时是 ``None``（「不知道」），那一层退回逐项探测，宁可慢也不猜。
    taken = occupied_names(destination)

    for raw_source in sources:
        source = Path(raw_source)
        if not source.exists():
            failures.append(Failure.at(source, message="源文件已经不在了。"))
            continue
        if same_directory(source.parent, destination):
            skipped.append(Skipped(source=source, message=f"已经在「{destination.name}」里了。"))
            continue
        if source.is_dir() and paths.is_within(source, destination):
            failures.append(Failure.at(source, message="不能把文件夹粘进它自己或它自己的子目录。"))
            continue
        if cancelled():
            break
        outcome = _copy_one(root, destination, source, batch, taken)
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


def _copy_one(
    root: Path, destination: Path, source: Path, batch: str, taken: set[str] | None
) -> _Outcome:
    """把一项复制进 ``destination``。

    ``taken`` 是这一批共用的一份「已经被占掉的名字」，由调用方扫一次目录得到；
    **目录读不了时它是 ``None``**（见 :func:`~gallery.core.metadata.naming.occupied_names`），
    那时每项退回逐个 ``exists()``。
    """
    is_dir = source.is_dir()
    created = free_target(destination, source.name, taken)
    if created is None:
        return _Outcome(failure=Failure.at(source, message="这个名字太长，再加编号也放不下了。"))
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
        # 清理那句 ``rmtree`` 删掉的是用户的文件夹连同里面所有照片（红线 1）。
        message = "这个位置已经有同名的文件或文件夹了，粘贴没有动它。"
        return _Outcome(
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
        message = os_reason(error, action="复制")
        return _Outcome(
            failure=Failure.at(source, message=message),
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
    **可以**在库外（访达里 ⌘C 复制进来的就是，ADR 0018）；目标必须在库内，落到库外则是谁都看不见、
    谁也不会清理的孤儿文件。配置目录仍然挡掉：往里粘照片只会被下一次清理当成垃圾。

    **库根本身是合法的目标**（没有选中项时粘的就是库根）：要挡的是「跑到库外」，不是「落在库里」。
    「不覆盖」不归这里管 —— 那是 :mod:`~gallery.core.metadata.naming` 的活。
    """
    root = Path(root)
    try:
        paths.guard_within_root(root, destination, allow_root=True)
    except ValueError as exc:
        raise PasteError(_paste_reason(destination, str(exc)), path=destination) from exc


def _paste_reason(path: Path, reason: str) -> str:
    """按闸门说的那一件事给一句中文 —— 理由见 :func:`rename._rename_reason`。"""
    if "配置目录" in reason:
        return "这是 Gallery 自己的配置目录，照片不该粘进去。"
    return f"只能粘到库里的文件夹：{path}。"


__all__ = [
    "Failure",
    "PasteResult",
    "PastedFile",
    "Skipped",
    "copy_into",
]
