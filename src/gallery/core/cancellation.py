"""「可取消」在核心层只有一种形状：一个 ``() -> bool`` 的回调。

核心层不认识线程、事件循环，也不认识 ``TaskRunner``，只消费 ``should_cancel`` 这个约定；
具体令牌由界面层（``ui.tasks.CancellationToken``）实现。省略回调时调用方写
``or never_cancel``，免得扫描器与写操作各自留一份会漂移的替身。
只在**项与项之间**取消：写入中途停下会留下半截文件，那比写完更糟（见 ``core.metadata.paste``）。
"""

from __future__ import annotations


def never_cancel() -> bool:
    """「永远还要继续」—— ``should_cancel`` 省略时的替身。"""
    return False


__all__ = ["never_cancel"]
