"""原子写盘小工具。

走「同目录临时文件 → fsync → ``os.replace``」：``os.replace`` 在同一文件系统上才是原子的，
所以临时文件必须与目标同目录。本模块只抛 ``OSError``，由调用方翻译成用户可读的中文。
**默认每次都 fsync**（``durable=True``），只有丢了也能自己长回来的缓存才传 ``durable=False``。
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

    :param durable: 默认 ``True``，写完落 ``fsync``，断电后这一份也还在。传 ``False``
        只省掉那次 fsync，``os.replace`` 的原子性照旧，只是「断电之后字节还在」变弱；
        因此只配给丢了也能重新长出来的缓存，``config.toml``、journal 这类必须留默认值。
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
