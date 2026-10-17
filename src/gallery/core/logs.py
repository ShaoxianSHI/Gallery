"""把日志落到实处：一个轮转文件加一个终端处理器。

**为什么必须有它**：从访达 / Dock 启动的 ``Gallery.app`` **没有终端**，stderr 等于丢进虚空 ——
界面说「详情已写入日志」时用户手上什么也没有，排查只能靠猜。日志落在应用自己的状态目录里
（``app_log_file``，见 ADR 0064），**不在库的配置目录里**：库还没打开就出事的那一段
（认不出参数、起不来）也得记下来。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from gallery.core import paths

#: 一个日志文件到多大就换下一个。够装几次崩溃前后的经过，也不会把磁盘堆满。
LOG_MAX_BYTES: Final = 1024 * 1024

#: 留几个旧日志。**总占用是这个数的两倍以内**（含当前那个）。
LOG_BACKUPS: Final = 2

#: 一行日志的样子：时间、级别、来源（``core.events.store`` 这类），然后才是消息。
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: 我们自己装的处理器都打上它，重复调用时按它认出「已经装过了」。
_HANDLER_MARK: Final = "gallery-log"


def configure_logging(*, base: Path | None = None, level: int = logging.INFO) -> Path | None:
    """装好文件与终端的日志处理器，返回日志文件路径（**装不上返回 ``None``**）。

    幂等：重复调用只补缺的那一个。装不上（目录建不出来、没权限）**不抛也不报** ——
    记不成日志是小事，不让应用起来是大事。:param base: 应用数据根目录，测试用。
    """
    root = logging.getLogger()
    root.setLevel(min(root.level or level, level))
    attached = {getattr(handler, _HANDLER_MARK, None) for handler in root.handlers}
    if "console" not in attached:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        setattr(console, _HANDLER_MARK, "console")
        root.addHandler(console)
    if "file" in attached:
        return paths.app_log_file(base=base)
    try:
        target = paths.app_log_file(base=base)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _HANDLER_MARK, "file")
    root.addHandler(handler)
    return target


__all__ = ["LOG_BACKUPS", "LOG_FORMAT", "LOG_MAX_BYTES", "configure_logging"]
