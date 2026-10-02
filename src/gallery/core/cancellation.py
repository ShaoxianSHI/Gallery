"""「可取消」在核心层只有一种形状：一个 ``() -> bool`` 的回调。

扫描、批量读元数据、复制、移动都要能被界面叫停，但**核心层不认识线程、事件循环，
也不认识 ``TaskRunner``**：它只看到 ``should_cancel`` 这个回调，问它一句「还要继续吗」。
具体的令牌在界面层（``ui.tasks.CancellationToken``）实现，核心层只消费这个约定。

有了这个约定，「没给回调」也得有一个说法：调用方把它写成 ``or never_cancel``，
代码里从此不必再写 ``should_cancel if should_cancel is not None else …`` 那半句 ——
扫描器、批量读取与两个写操作原本各留了一份一模一样的 ``_never_cancel``，四处会各自漂移。

只在**项与项之间**取消，不要在一次写入的中途取消：粘到一半停下来会留下半截文件，
那比粘完更糟（见 ``core.metadata.paste``）。
"""

from __future__ import annotations


def never_cancel() -> bool:
    """「永远还要继续」—— ``should_cancel`` 省略时的替身。"""
    return False


__all__ = ["never_cancel"]
