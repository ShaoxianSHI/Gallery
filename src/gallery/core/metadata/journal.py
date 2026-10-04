"""写操作日志。

凡是**会改动用户文件**的操作都要在这里留一条记录：改了什么、从哪到哪、什么时候、
结果如何。记录既是给用户看的历史，也是回滚的依据 —— ``undo.undo_last_write``
读的就是它。

日志是追加写的 JSONL：配置目录下 ``journal/YYYY-MM-DD.jsonl``，一行一条。追加写中途崩溃
只会丢掉最后一行，已写完的行仍然完好；这正是这里需要的性质，日志本身不值得原子化。

**记账失败绝不能让操作失败**：用户已经改好的文件不能因为「记不上账」就被说成没改成。
所以 :func:`append_record` 吞掉 ``OSError`` 并如实返回 ``False``，由调用方决定要不要提示。
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

#: 日志里的动作名：把剪贴板里的文件**复制**进某个目录（``core.metadata.paste``、ADR 0018）。
#:
#: **它不在撤销栈里**（``undo._PUSH_ACTIONS`` 收的是改名、移到废纸篓与移动到相册）：粘进来
#: 的是**新文件**，撤销一次粘贴就是删文件，而删除在这个项目里只允许用户亲手触发（红线 1）。
#: 这一条只是**记录** —— 粘了些什么、从哪来、什么时候、结果如何。
ACTION_PASTE: Final = "paste"

#: 日志里的动作名：把一项**移动**进另一个相册（``core.metadata.transfer``、ADR 0019）。
#:
#: 它**在**撤销栈里：撤销一次移动仍然是一次移动 —— 东西一直都在，只是回到原来待着的地方
#: （与撤销粘贴正好相反，那一次是删除，红线 1 不允许）。
ACTION_MOVE: Final = "move"

#: 日志里的动作名：新建一个相册（``core.metadata.album``、ADR 0022）。
#:
#: 与 :data:`ACTION_PASTE` 同属「只增不改」那一类，**同样不在撤销栈里**：撤销一次新建
#: 就是删掉一个文件夹，而删除在这个项目里只允许用户在界面上亲手触发（红线 1）。
#: 紧接着那一次改名（用户给新相册起名）照旧记账、照旧可 ``⌘Z`` 撤销。
ACTION_CREATE: Final = "create"

#: 日志里的动作名：一次**元数据写回**（坐标 / 地点，见 :mod:`gallery.core.metadata.writeback`、
#: ADR 0030）。
#:
#: **这是本项目里第一次「文件不动、内容动」的操作**：``source`` 与 ``target`` 是同一个路径，
#: 真正记下来的是 :attr:`WriteRecord.changes` 里那几条字段的原值与新值 —— 撤销靠的就是它
#: （红线 4：写回前记录原值）。
ACTION_METADATA: Final = "metadata"

#: 日志里的动作名：把一次元数据写回改回去。
#:
#: 与 :data:`ACTION_UNDO_METADATA` 的差别只在这一条会被弹栈：撤销记录说的是「我把这个字段
#: 写回了原值」，于是 :func:`gallery.core.metadata.undo.pending_undo_runs` 能把当初那几条
#: 从栈上摘下来。写回**不全等于**原值时（只撤了能撤的那些），记的仍是这个动作名 ——
#: 冲突的那几个字段留在原处并如实报告，它们对应的写回记录留在栈上，再按一次 ⌘Z 还会试。
ACTION_UNDO_METADATA: Final = "undo_metadata"

#: 操作成功。
RESULT_OK: Final = "ok"

#: 操作失败（压根没改成；或者改到一半出错，已经写下去的那部分如实地记在这里）。
RESULT_FAILED: Final = "failed"

#: **打算改**（还没改）。
#:
#: 写回元数据是本项目里唯一「文件不搬家、内容被改」的操作，因此它的记账必须**早于**
#: 那次写入：exiftool 起一次子进程就要几十到几百毫秒，这个窗口里断电 / 被强杀 /
#: 磁盘写满，原图已经改了而日志里一条记录也没有 —— 用户拿不回任何东西。
#:
#: 所以先落一条 :data:`RESULT_PENDING`（字段原值与新值都已经齐了），再动文件，
#: 最后补一条终态记录。正常情况下终态记录会把这条**顶掉**（见
#: :func:`gallery.core.metadata.undo.pending_undo_runs`）；只有「进程死在中间」才会
#: 留下一条孤零零的 pending，而那正是最需要 ⌘Z 的时候（红线 4）。
RESULT_PENDING: Final = "pending"

#: 终态：后面不会再有别的记录来说明这一条的结果了。
_TERMINAL_RESULTS: Final = frozenset({RESULT_OK, RESULT_FAILED})


def _now() -> str:
    """带时区的秒级时间戳。日志按它排序，所以必须包含偏移量。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class FieldChange:
    """一个字段被写成了什么、又原来是什么。

    **「原来没有」与「原来是空串」不是一回事，但都用 ``None``**：exiftool 对一个不存在的
    标签压根不给键，对一个被清空的标签给 ``""``。撤销时两者的动作是一样的（删掉它），
    所以它们不必区分 —— 真要区分就得在读的那一侧额外判一次，判出来的信息没有用处。
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

    #: :data:`ACTION_RENAME`、:data:`ACTION_MOVE`、:data:`ACTION_PASTE`、
    #: :data:`ACTION_TRASH`、:data:`ACTION_RESTORE`、:data:`ACTION_CREATE`、
    #: :data:`ACTION_UNDO`、:data:`ACTION_METADATA` 或 :data:`ACTION_UNDO_METADATA`。
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
    #: 同一次操作里多项共享的编号（批量删除）。单条操作为 ``None``。
    #:
    #: 撤销按「一次操作」为单位，而记账按「一项」为单位：批量删十张要写十条记录
    #: （其中可能有的成功、有的失败），但用户只该按一次 ⌘Z（见 ``undo.undo_last_write``）。
    batch: str | None = None
    #: 元数据写回时逐字段记下的原值与新值（ADR 0030）。其余动作恒为空。
    #:
    #: **只有这一种记录带字段级信息**：改名与删除靠 ``source`` / ``target`` 就能还原，
    #: 而写回动的既不是路径也不是文件集合，只有「哪个标签、从什么变成什么」才说得清。
    changes: tuple[FieldChange, ...] = ()

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
        raw_changes = data.get("changes")
        changes: tuple[FieldChange, ...] = ()
        if isinstance(raw_changes, list):
            # 坏的一条丢掉而不是整条记录作废：一次写回十来个字段，
            # 因为其中一条写歪了就把整条撤销凭据丢掉，用户反而再也撤不掉。
            changes = tuple(
                change
                for item in raw_changes
                if (change := FieldChange.from_json(item)) is not None
            )
        return cls(
            action=str(data.get("action", ACTION_RENAME)),
            source=Path(source),
            target=Path(target),
            result=str(data.get("result", RESULT_FAILED)),
            message=message if isinstance(message, str) else None,
            is_dir=bool(data.get("is_dir", False)),
            timestamp=str(data.get("timestamp", "")),
            batch=batch if isinstance(batch, str) and batch else None,
            changes=changes,
        )


def journal_file(root: Path, *, when: datetime | None = None) -> Path:
    """某一天（或今天）的日志文件路径。"""
    day = (when or datetime.now().astimezone()).date().isoformat()
    return paths.journal_dir(root) / f"{day}.jsonl"


def append_record(root: Path, record: WriteRecord) -> bool:
    """把一条记录追加进当天的日志。返回**是否记账成功**。

    失败不抛异常：写不进去只意味着这次操作没有留下可回滚的记录，
    而用户的文件已经改好了 —— 两害相权，保留改动、如实返回 ``False``。
    """
    path = journal_file(root)
    line = record.to_json() + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            # fsync 是为了让「用户以为改成功了」和「日志里确实有这条」是同一件事。
            #
            # 量过：一条记录约 **0.05 ms**（APFS 上追加写 + fsync），粘一千张照片也就是
            # 45 ms。曾想过批量那三条路改成「开一次句柄、最后 fsync 一次」，实测省下的
            # 远不到一帧，却要把一个口一路穿过 paste / transfer / trash 的每个单项函数 ——
            # 不值。这条就保持简单。
            os.fsync(handle.fileno())
    except OSError:
        _forget(root)
        return False
    _remember(root, record)
    return True


#: 已经解析好的记录，按库根记着（见 :func:`read_records`）。
#:
#: 只留最近几个库 —— 换库、切库是常事，而一份用不上的日志没必要一直占着内存。
#:
#: 这是模块内的一份**记忆**，不是用来传递库路径或配置的全局状态：键就是入参，
#: 值完全由本模块自己写自己读（整个程序里只有 :func:`append_record` 会动日志目录），
#: 与 ``core.media.decode`` 里那个 ``_stuck_reads`` 同一形状。
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

    **结果记在进程内**，写进去的那一条也会顺手把缓存补上（:func:`append_record`）。

    为什么值得记：调用方是界面线程上的撤销栈，而**每做完一次写操作都要重算一次
    「下一次 ⌘Z 会撤掉什么」**。没有缓存时那是一次「读完库里每一个按天分文件的日志
    + 逐行 ``json.loads``」—— 而用户一天做二十次写操作，就是把这个代价付二十遍，
    且日志越大越贵，**总代价是操作次数的平方**。一天二十次操作就足以让「按一下 ⌘Z」
    开始发涩，用上一年更不用提。

    缓存只在**本进程内**有效：换个进程（日志被别的工具动过、上次没记干净）自然重新读，
    而本进程里写日志的只有 :func:`append_record` 一处，它负责同步 —— 于是这份缓存
    不会漏读，也不会读到过期内容。
    """
    key = Path(root)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        # 交一份副本：调用方（撤销栈）会就地整理它，原地改坏了这份缓存就麻烦了。
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
