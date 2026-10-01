"""UI 测试共用的 fixture 与等待工具。

界面里的扫描、缩略图、元数据读取都跑在后台线程上，测试只能靠「转事件循环 + 看
状态」等它们。这里提供两种等法，区别很重要：

- ``settle(seconds)`` —— 单纯转一会儿事件循环。用在「必须等满，否则测不到」的场景，
  比如「这 200ms 里不该出现加载提示」：那种等待时长本身就是被断言的一部分。
- ``wait_for(predicate, timeout=...)`` —— 转到条件成立为止。**``timeout`` 只是上限，
  不是固定开销**：条件一成立立刻返回。

固定 sleep 的毛病是必须比真实耗时长才能在慢机器上也稳，于是每次都在空转：实测
「面板出内容」约 56ms、「预读填满」约 107ms，而代码里原先写的是 1 秒 —— 十倍的浪费。
换成 ``wait_for`` 之后，超时上限原样保留当保险，常态耗时还回来，而且**不会比原来
更早断言**，只会更早结束。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

#: 轮询间隔。够密才抓得住一闪而过的东西，又不至于把 CPU 跑满。
POLL_SECONDS = 0.005


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    """整个模块共用一个 ``QApplication``：它不能被反复创建与销毁。"""
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


@pytest.fixture
def settle(qt_app: QApplication) -> Callable[[float], None]:
    """转满给定秒数的事件循环，不看任何条件。"""

    def _settle(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(POLL_SECONDS)

    return _settle


@pytest.fixture
def wait_for(qt_app: QApplication) -> Callable[..., bool]:
    """转到 ``predicate()`` 成立为止，返回它是否真的成立过。"""

    def _wait_for(predicate: Callable[[], bool], *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            qt_app.processEvents()
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)

    return _wait_for
