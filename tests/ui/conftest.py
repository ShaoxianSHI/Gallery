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

第三样是 ``qt_teardown``：每个用例之后确定性地销毁这一轮造出来的控件。这条不是
可有可无的讲究，理由（以及不这么做会怎样）写在 :func:`_destroy_top_level_widgets` 里。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

#: 轮询间隔。够密才抓得住一闪而过的东西，又不至于把 CPU 跑满。
POLL_SECONDS = 0.005


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    """整个模块共用一个 ``QApplication``：它不能被反复创建与销毁。"""
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


def _destroy_top_level_widgets(app: QApplication) -> None:
    """销毁 ``app`` 底下所有顶层控件，并把排队删除的队列抽干。

    **为什么界面测试必须显式收尾**：Qt 控件的 C++ 对象归 ``QApplication`` 管，可它们的
    Python 包装却被信号连接拴住了 —— 网格与模型之间那条 ``rowsInserted`` 连接会把
    闭包连着网格一起留在模型的 C++ 侧，而视图又在 C++ 侧持有模型。于是一个引用环
    横跨了 Python 与 C++ 两边，引用计数归不了零，循环 gc 也看不见它（它的一部分根本
    不是 Python 对象）。结果是控件既不会被用例自己回收，也等不到 ``QApplication``
    的析构去收拾它们，一路活到解释器收尾。

    到了那一步它们是怎么死的就不由我们决定了：``Py_FinalizeEx`` 先抽干模块、再跑一轮
    循环 gc，销毁顺序随机器负载而变，于是「谁先被拆掉」成了掷硬币。实测表现为
    **用例全绿、最后进程 SIGSEGV**（退出码 139，汇总行都来不及打印）——这正是这套
    测试「有一定概率随机不通过」的来源，而且随机性只来自时序，与被测代码无关。

    所以收尾必须**在 QApplication 还完整活着的时候做**：``close()`` 触发 ``closeEvent``
    （停监听、停后台任务），``deleteLater()`` 排队删除 C++ 对象，再由
    ``sendPostedEvents`` 把队列抽干。顺序不能换，``deleteLater`` 只是排队。
    """
    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def teardown_qt(qt_app: QApplication) -> Callable[[], None]:
    """手动收尾一次（守护测试用得上；平时靠 autouse 的 :func:`qt_teardown` 就够了）。"""
    return lambda: _destroy_top_level_widgets(qt_app)


@pytest.fixture(autouse=True)
def qt_teardown(qt_app: QApplication) -> Iterator[None]:
    """每个用例之后确定性地收尾，不让控件活到解释器收尾去碰运气。

    依赖 ``qt_app`` 还带来一个顺序保证：autouse 的函数级 fixture 先于模块级的
    ``qt_app`` 被拆掉，所以收尾时 ``QApplication`` 一定还在。
    """
    yield
    _destroy_top_level_widgets(qt_app)


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
