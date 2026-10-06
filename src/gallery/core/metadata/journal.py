"""写操作日志：凡是**会改动用户文件**的操作都在这里留一条可回滚的记录（红线 4）。

追加写 JSONL（``journal/YYYY-MM-DD.jsonl``），中途崩溃只丢最后一行，够用。
**记账失败绝不能让操作失败** —— 用户已经改好的文件不能因为记不上账就被说成没改成，所以
:func:`append_record` 吞掉 ``OSError`` 并如实返回 ``False``，由调用方决定要不要提示。
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from gallery.core import paths

#: 日志里的动作名：一次改名。
ACTION_RENAME: Final = "rename"

#: 日志里的动作名：把一次改名改回去。
ACTION_UNDO: Final = "undo"

#: 日志里的动作名：把一项移进系统废纸篓（Gallery 语境里的「删除」，见 ADR 0008）。
ACTION_TRASH: Final = "trash"

#: 日志里的动作名：把一项从废纸篓里取回来（撤销删除）。
ACTION_RESTORE: Final = "restore"

#: 日志里的动作名：把剪贴板里的文件**复制**进某个目录（ADR 0018）。只**记录**：粘了些什么、
#: 从哪来、什么时候、结果如何。**它不在撤销栈里** —— 撤销粘贴就是删文件（红线 1）。
ACTION_PASTE: Final = "paste"

#: 日志里的动作名：把一项**移动**进另一个相册（ADR 0019）。
#: 它**在**撤销栈里：撤销一次移动仍然是一次移动，东西一直都在，只是回到原来待着的地方。
ACTION_MOVE: Final = "move"

#: 日志里的动作名：新建一个相册（ADR 0022）。与 :data:`ACTION_PASTE` 同属「只增不改」，
#: 同样不在撤销栈里（撤销它就是删文件夹，红线 1）；紧接着的改名照旧可 ⌘Z。
ACTION_CREATE: Final = "create"

#: 日志里的动作名：一次**元数据写回**（坐标 / 地点，ADR 0030）。第一次「文件不动、内容动」的
#: 操作：``source`` 与 ``target`` 是同一个路径，真正记下来的是 :attr:`WriteRecord.changes`
#: 里逐字段的原值与新值 —— 撤销靠的就是它（红线 4）。
ACTION_METADATA: Final = "metadata"

#: 日志里的动作名：把一次元数据写回改回去。只有这一条会被弹栈 —— 它说的是「我把这个字段写回
#: 了原值」，于是 ``undo.pending_undo_runs`` 能把当初那几条从栈上摘下来。**能不能摘取决于那次
#: 撤销自己记的是 ``ok`` 还是 ``failed``**：部分冲突（有的字段撤得成）记 ``ok`` → 弹栈；全冲突
#: 记 ``failed`` → 栈上**留着**当初那条写回，再按一次 ⌘Z 会重试。冲突的字段一律留在原处。
ACTION_UNDO_METADATA: Final = "undo_metadata"

#: 日志里的动作名：改一个事件的内容（ADR 0043）。它**动的是我们自己的库**，不是用户的照片，
#: 但一样可 ⌘Z —— 事件是唯一一份重扫重建不出来的数据。前后值在 :attr:`WriteRecord.changes`
#: 里逐字段记着，而 ``payload`` 里记着是哪一条事件。
ACTION_EVENT: Final = "event"

#: 日志里的动作名：新建一个事件。与 :data:`ACTION_CREATE`（新建相册）同属「只增不改」，
#: **不在撤销栈里** —— 撤销它就是删，而刚建的那一刻用户还没往里放东西。留着是为了可追溯。
ACTION_EVENT_CREATE: Final = "event_create"

#: 日志里的动作名：删一个事件。``payload`` 里带着它当时那些绑定，撤销要把它们一起放回去 ——
#: 只恢复事件本体的话，照片关联没了而用户看不出来（ADR 0043 决策 5）。
ACTION_EVENT_DELETE: Final = "event_delete"

#: 日志里的动作名：把一次事件改动改回去（弹栈）。
ACTION_UNDO_EVENT: Final = "undo_event"

#: 操作成功。
RESULT_OK: Final = "ok"

#: 操作失败（压根没改成；或者改到一半出错，已经写下去的那部分如实记在这里）。
RESULT_FAILED: Final = "failed"

#: **打算改**（还没改）—— 写回前先落一条，那时原值与新值已经齐了。必须早于那次写入：
#: exiftool 子进程要几十到几百毫秒，这个窗口里断电 / 被强杀，原图就改了而日志上一条也没有。
#: 终态记录会顶掉它，只有「进程死在中间」才留下孤零零的 pending —— 那正是最需要 ⌘Z 的时候。
RESULT_PENDING: Final = "pending"

#: 终态：后面不会再有别的记录来说明这一条的结果了。
_TERMINAL_RESULTS: Final = frozenset({RESULT_OK, RESULT_FAILED})


def _now() -> str:
    """带时区的秒级时间戳。日志按它排序，所以必须包含偏移量。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class FieldChange:
    """一个字段被写成了什么、又原来是什么。

    **「原来没有」与「原来是空串」都用 ``None``**：exiftool 对不存在的标签压根不给键，对被
    清空的标签给 ``""``，而两者的撤销动作是一样的（删掉它），不必区分。
    """

    #: exiftool 的标签名（``EXIF:GPSLatitude``、``XMP-photoshop:City``……）。
    tag: str
    #: 写之前的值。``None`` 表示原来没有这个字段。
    before: str | None = None
    #: 写之后的值。``None`` 表示这次把它删掉了。
    after: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {"tag": self.tag, "before": self.before, "after": self.after}

    @classmethod
    def from_json(cls, payload: object) -> FieldChange | None:
        """解析一条字段改动；形状不对返回 ``None``（日志不该把程序带崩）。"""
        if not isinstance(payload, dict):
            return None
        tag = payload.get("tag")
        if not isinstance(tag, str) or not tag:
            return None
        return cls(
            tag=tag,
            before=_optional_text(payload.get("before")),
            after=_optional_text(payload.get("after")),
        )


def _optional_text(value: object) -> str | None:
    """日志里的字段值：非空字符串才是值，空串与别的类型一律当「没有」。"""
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class WriteRecord:
    """一条写操作记录。"""

    #: 动作名（:data:`ACTION_RENAME` / ``ACTION_MOVE`` / ``ACTION_PASTE`` / ``ACTION_TRASH``
    #: / ``ACTION_RESTORE`` / ``ACTION_CREATE`` / ``ACTION_UNDO`` / ``ACTION_METADATA`` /
    #: ``ACTION_UNDO_METADATA``）。
    action: str
    #: 改之前的路径。
    source: Path
    #: 改之后的路径。删除动作里是它在废纸篓中的位置。
    target: Path
    #: :data:`RESULT_OK` 或 :data:`RESULT_FAILED`。
    result: str
    #: 出错原因（给用户看的中文描述），成功时为 ``None``。
    message: str | None = None
    #: 改的是不是文件夹。撤销时要靠它把引用还原成对的类型。
    is_dir: bool = False
    #: 记录产生的时间。
    timestamp: str = field(default_factory=_now)
    #: 同一次操作里多项共享的编号（批量删除）。单条操作为 ``None``：撤销按「一次操作」
    #: 为单位而记账按「一项」，所以删十张写十条记录、只值一次 ⌘Z。
    batch: str | None = None
    #: 元数据写回时逐字段记下的原值与新值（ADR 0030），其余动作恒为空 —— 改名与删除靠
    #: ``source`` / ``target`` 就能还原，只有写回既不动路径也不动文件集合。
    #: 事件记录（ADR 0043）也用这一栏记字段的前后值，因为「一个字段从什么变成了什么」正是
    #: 撤销要的东西。
    changes: tuple[FieldChange, ...] = ()
    #: **自己解释的一段 JSON**，只有事件记录在用（ADR 0043）：``{"event_id": …, "kind": …,
    #: "bindings": [...]}``。加这一栏是为了让日志**不长出事件的领域知识** —— 事件前后像的形状
    #: 由 ``core.events`` 自己讲，日志只管把它原样存下来。
    payload: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.result == RESULT_OK

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "action": self.action,
                "from": str(self.source),
                "to": str(self.target),
                "is_dir": self.is_dir,
                "result": self.result,
                "message": self.message,
                "batch": self.batch,
                "changes": [change.to_json() for change in self.changes],
                "payload": self.payload,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> WriteRecord | None:
        """解析一行日志；坏行返回 ``None``（日志不该把程序带崩）。"""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        source = data.get("from")
        target = data.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        message = data.get("message")
        batch = data.get("batch")
        raw_payload = data.get("payload")
        raw_changes = data.get("changes")
        changes: tuple[FieldChange, ...] = ()
        if isinstance(raw_changes, list):
            # 坏的一条丢掉而不是整条记录作废：一条写歪了就把整条撤销凭据丢掉，用户再也撤不掉。
            changes = tuple(
                change
                for item in raw_changes
                if (change := FieldChange.from_json(item)) is not None
            )
        return cls(
            action=str(data.get("action", "")),
            source=Path(source),
            target=Path(target),
            result=str(data.get("result", RESULT_FAILED)),
            message=message if isinstance(message, str) else None,
            is_dir=bool(data.get("is_dir", False)),
            timestamp=str(data.get("timestamp", "")),
            batch=batch if isinstance(batch, str) and batch else None,
            changes=changes,
            payload=raw_payload if isinstance(raw_payload, str) and raw_payload else None,
        )


def journal_file(root: Path, *, when: datetime | None = None) -> Path:
    """某一天（或今天）的日志文件路径。"""
    day = (when or datetime.now().astimezone()).date().isoformat()
    return paths.journal_dir(root) / f"{day}.jsonl"


def append_record(root: Path, record: WriteRecord) -> bool:
    """把一条记录追加进当天的日志。返回**是否记账成功**。

    失败不抛异常：写不进去只意味着这次没有可回滚的记录，而用户的文件已经改好了。
    """
    path = journal_file(root)
    line = record.to_json() + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            # fsync 是为了让「用户以为改成功了」和「日志里确实有这条」是同一件事（约 0.05 ms）。
            os.fsync(handle.fileno())
    except OSError:
        _forget(root)
        return False
    _remember(root, record)
    return True


#: 已解析记录的缓存，按库根记着，最多留这么多个库（换库是常事，用不上的不必一直占内存）。
#: 只是模块内的一份记忆：键就是入参，只有本模块写、只有本模块读。
_CACHE_LIMIT = 4

_cache: OrderedDict[Path, list[WriteRecord]] = OrderedDict()


def _remember(root: Path, record: WriteRecord) -> None:
    """新追加了一条：让缓存里那份也跟着长一条，省得下一次重读整个日志。"""
    key = Path(root)
    existing = _cache.get(key)
    if existing is not None:
        existing.append(record)
        _cache.move_to_end(key)
        return
    _forget(key)


def _forget(root: Path) -> None:
    _cache.pop(Path(root), None)


def read_records(root: Path) -> list[WriteRecord]:
    """按时间顺序读出全部记录（跨天）。坏行跳过。

    **结果记在进程内**，写进去的那一条也会顺手把缓存补上（:func:`append_record`）。值得记
    是因为调用方每做完一次写操作都要重算「下一次 ⌘Z 撤掉什么」，没有缓存时总代价是操作
    次数的平方（一天二十次操作就足以让 ⌘Z 开始发涩）。缓存不会漏读：写日志的只有
    :func:`append_record` 一处且它负责同步。
    """
    key = Path(root)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        # 交一份副本：调用方（撤销栈）会就地整理它。
        return list(cached)

    directory = paths.journal_dir(root)
    try:
        # 文件名就是日期，字典序即时间序。
        files = sorted(path for path in directory.iterdir() if path.suffix == ".jsonl")
    except OSError:
        return []
    records: list[WriteRecord] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            record = WriteRecord.from_json(line)
            if record is not None:
                records.append(record)
    _cache[key] = records
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)
    return list(records)


__all__ = [
    "ACTION_CREATE",
    "ACTION_METADATA",
    "ACTION_MOVE",
    "ACTION_PASTE",
    "ACTION_RENAME",
    "ACTION_RESTORE",
    "ACTION_TRASH",
    "ACTION_UNDO",
    "ACTION_UNDO_METADATA",
    "RESULT_FAILED",
    "RESULT_OK",
    "RESULT_PENDING",
    "FieldChange",
    "WriteRecord",
    "append_record",
    "journal_file",
    "read_records",
]
