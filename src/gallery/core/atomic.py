"""原子写盘小工具。

配置文件与会话状态都是「小、偶尔写、写完必须完整」的文件。直接覆盖写入在崩溃或断电时
会留下半截文件，因此统一走「同目录临时文件 → fsync → ``os.replace``」。

``os.replace`` 在同一文件系统上是原子的，这正是临时文件必须放在目标同目录的原因。
本模块只抛 ``OSError``，由调用方翻译成面向用户的中文错误。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """把 ``text`` 原子地写进 ``path``（父目录会被创建）。"""
    _write_atomic(path, text.encode("utf-8"))


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """把 ``data`` 原子地写进 ``path``（父目录会被创建）。"""
    _write_atomic(path, data)


def _write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_text_or_none(path: Path) -> str | None:
    """读取文本文件；不存在或读不了时返回 ``None``（调用方决定是否算错误）。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


__all__ = ["read_text_or_none", "write_bytes_atomic", "write_text_atomic"]
