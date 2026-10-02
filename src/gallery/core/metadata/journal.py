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

#: 操作成功。
RESULT_OK: Final = "ok"

#: 操作失败（压根没改成；或者改到一半出错，已经写下去的那部分如实地记在这里）。
RESULT_FAILED: Final = "failed"


def _now() -> str:
    """带时区的秒级时间戳。日志按它排序，所以必须包含偏移量。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class WriteRecord:
    """一条写操作记录。"""

    #: :data:`ACTION_RENAME`、:data:`ACTION_MOVE`、:data:`ACTION_PASTE`、
    #: :data:`ACTION_TRASH`、:data:`ACTION_RESTORE`、:data:`ACTION_CREATE`
    #: 或 :data:`ACTION_UNDO`。
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
        return cls(
            action=str(data.get("action", ACTION_RENAME)),
            source=Path(source),
            target=Path(target),
            result=str(data.get("result", RESULT_FAILED)),
            message=message if isinstance(message, str) else None,
            is_dir=bool(data.get("is_dir", False)),
            timestamp=str(data.get("timestamp", "")),
            batch=batch if isinstance(batch, str) and batch else None,
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
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def read_records(root: Path) -> list[WriteRecord]:
    """按时间顺序读出全部记录（跨天）。坏行跳过。"""
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
    return records


__all__ = [
    "ACTION_CREATE",
    "ACTION_MOVE",
    "ACTION_PASTE",
    "ACTION_RENAME",
    "ACTION_RESTORE",
    "ACTION_TRASH",
    "ACTION_UNDO",
    "RESULT_FAILED",
    "RESULT_OK",
    "WriteRecord",
    "append_record",
    "journal_file",
    "read_records",
]
