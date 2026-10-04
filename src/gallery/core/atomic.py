"""原子写盘小工具。

配置文件与会话状态都是「小、偶尔写、写完必须完整」的文件。直接覆盖写入在崩溃或断电时
会留下半截文件，因此统一走「同目录临时文件 → fsync → ``os.replace``」。

``os.replace`` 在同一文件系统上是原子的，这正是临时文件必须放在目标同目录的原因。
本模块只抛 ``OSError``，由调用方翻译成面向用户的中文错误。

**默认每次都 fsync**（``durable=True``）。只有「丢了也能自己长回来」的东西才该显式传
``durable=False`` 去省掉那次 fsync（目前是缩略图缓存），因为 fsync 的代价会按写入次数
一分不少地落在用户等的每一张图上。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """把 ``text`` 原子地写进 ``path``（父目录会被创建）。

    **没有 ``durable`` 开关**：写出来的都是配置与会话状态，丢了就没了，每次都要 fsync。
    """
    _write_atomic(path, text.encode("utf-8"), durable=True)


def write_bytes_atomic(path: Path, data: bytes, *, durable: bool = True) -> None:
    """把 ``data`` 原子地写进 ``path``（父目录会被创建）。

    :param durable: 默认 ``True``，写完落 ``fsync``，断电后这一份也还在。
        传 ``False`` 只是省掉那次 fsync —— ``os.replace`` 的原子性**照旧**（读者不会
        读到半截文件，崩了也不会留下改名到一半的目录），弱化的只是「断电之后字节还在」。

        因此它只配给**丢了也能重新长出来**的写入（可再生的缓存）。``config.toml``、
        journal 记录这类一旦丢了就没了的东西必须留着默认值。
    """
    _write_atomic(path, data, durable=durable)


def _write_atomic(path: Path, data: bytes, *, durable: bool) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if durable:
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
