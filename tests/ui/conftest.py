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

import ast
import os
import pathlib
import time
from collections.abc import Callable, Iterator
from functools import partial

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from waiting import wait_until  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

#: 轮询间隔。够密才抓得住一闪而过的东西，又不至于把 CPU 跑满。
POLL_SECONDS = 0.005

#: 测试里的双击阈值（毫秒）。生产上它跟着系统走，macOS 是 500ms。
#:
#: 值得单独调小的原因：行内重命名的定时器**就是照它设的间隔**（``PhotoGrid`` 构造时读
#: ``QApplication.doubleClickInterval()``），于是测「第二次点击到底算不算改名」的用例也
#: 只能照同一个值去等 —— 每个这样的用例要在事件循环里空转一秒上下，一整个
#: ``test_rename_flow.py`` 有三分之一的时间是在等它。
#:
#: 调小它并没有削弱这些用例：它们发的是**显式**的 ``MouseButtonDblClick`` 事件，不靠
#: Qt 自己按时间判定双击；被测的「定时器按双击阈值走」这件事在 40ms 下照样成立，
#: 而用例额外留的余量是绝对的 50/100ms，相对余量反而更宽了。
DOUBLE_CLICK_INTERVAL_MS = 40


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    """整个模块共用一个 ``QApplication``：它不能被反复创建与销毁。"""
    app = QApplication.instance() or QApplication([])
    QApplication.setDoubleClickInterval(DOUBLE_CLICK_INTERVAL_MS)
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

    所以收尾必须**在 QApplication 还完整活着的时候做**，而且顺序是有讲究的：

    1. ``close()`` 触发 ``closeEvent``（停监听、停后台任务）；
    2. **先转一圈事件循环**，把还排着的普通事件与 0 毫秒定时器跑掉。这一步不能省 ——
       布局里那些「延到下一轮重排」的单次定时器如果留着，控件删掉之后它们照样会触发，
       去调一个已经析构的方法，换来一个 ``Internal C++ object already deleted``；
    3. ``deleteLater()`` 排队删除 C++ 对象（它只是排队，不删）；
    4. ``sendPostedEvents`` 把队列抽干，这一步对象才真的没了。

    顺序反过来（先删后转）就是第 2 步那个坑；少了第 4 步则整轮收尾等于没做。
    """
    for widget in list(app.topLevelWidgets()):
        widget.close()
    app.processEvents()  # 先让排队的回调在对象还活着的时候跑完
    for widget in list(app.topLevelWidgets()):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """**模态对话框一律当场失败**，不许把用例挂死。

    这一条是被一次实测逼出来的：一个用例打开了不存在的库，界面走到
    ``_report_error`` → ``QMessageBox.critical`` —— 离屏下没人点「确定」，
    那个用例**永远不返回**。症状是「这个文件跑了五分钟还没完」，而不是一条红的
    断言；查起来先是怀疑慢，再是怀疑死循环，最后才发现是弹窗。

    **失败要快、要指名道姓**：这里把 ``exec``（真正那个模态循环）换掉，报出
    「弹了个框」比让它挂着强得多。要测弹窗的用例自己 monkeypatch 掉那个静态方法
    即可（见 ``test_library_open.py``）—— 静态方法被换掉之后就走不到 ``exec``，
    所以那条路照样通。
    """

    def _refuse(self: QMessageBox) -> int:
        raise AssertionError(
            "这个用例弹出了一个模态对话框，而离屏测试里没人点确定 —— 它会一直挂着。"
            "要测它就自己 monkeypatch 掉（QMessageBox.critical / .question / .warning / .exec）。"
        )

    monkeypatch.setattr(QMessageBox, "exec", _refuse)


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
    """转到 ``predicate()`` 成立为止，返回它是否真的成立过。

    是 :func:`waiting.wait_until` 的 fixture 形态（用它就得在签名里写 ``timeout=``），
    而 ``waiting`` 那个是普通函数、按位置传 ``qt_app``。**只有一份轮询循环**，
    否则改了一处不会改到另一处。
    """
    return partial(wait_until, qt_app)


def test_no_test_module_shadows_the_shared_fixtures() -> None:
    """**测试模块不许自己再定义一份共享 fixture。**

    曾经 14 个模块各自写了一份 ``qt_app``，里面少了 conftest 那句
    ``QApplication.setDoubleClickInterval(40)`` —— 于是它们全部悄悄跑在系统默认的
    500ms 双击阈值上，而**测试照常全绿**：没人看得见少掉的那一句，直到某个用例
    开始按机器负载偶发变红。

    同类的还有各写一份的等待助手（``wait_until`` / ``_wait``，8 份）与
    ``WAIT_SECONDS``（两个不同的值）。它们的问题不是慢，是**同一件事有 8 份写法**，
    改一处不会改到其余七处。

    ``settle`` 也在名单里：``test_blank_double_click`` 曾自己写了一个同名的
    ``settle(qt_app, seconds)``。它不报错、也不冲突（没有用例把 ``settle`` 写成
    参数），于是那半边「共享的那一份」被顶掉了，而顶掉这件事**完全看不出来** ——
    与 ``qt_app`` 那次一模一样的形状，只是更安静。

    这条守护让「共享 fixture 只有一份」这件事从约定变成红灯。
    """
    reserved = {"qt_app", "wait_for", "settle", "make_grid"}
    offenders: list[str] = []
    for path in sorted(pathlib.Path(__file__).resolve().parent.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in reserved:
                has_fixture = any("fixture" in ast.dump(d) for d in node.decorator_list)
                if has_fixture:
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, (
        "这些模块重定义了共享 fixture，共享的那一份（含双击阈值、等待助手的正确实现）"
        f"就白写了：{offenders}"
    )


def _spins_the_loop_itself(node: ast.FunctionDef) -> bool:
    """这个函数**自己**转事件循环并且自己掐时间 —— 那就是自己写了一份轮询循环。

    判据是「同时出现 ``processEvents`` 与（``monotonic`` 或 ``sleep``）」：这两样
    一起才有轮询的样子。只有 ``processEvents`` 的多半是在收尾（转一圈就跑），
    只有 ``monotonic`` 的多半是在量时长。
    """
    calls = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return "processEvents" in calls and bool({"sleep", "monotonic"} & (calls | names))


def test_no_test_module_reimplements_the_wait_helper() -> None:
    """等待助手**只有 :mod:`waiting` 里的那几份**，任何测试模块都不许再自己写循环。

    曾经散着 8 份轮询循环（``wait_until`` / ``_wait`` / ``_wait_for_scan`` /
    ``wait_rows``），``WAIT_SECONDS`` 与 ``SCAN_TIMEOUT_SECONDS`` 各自定了两个不同的
    值，轮询间隔也有 0.02 与 0.005 两档。问题不在慢 —— 在于**同一件事有 8 份写法**：
    改一处不会改到其余七处，而「该等多久才算超时」这种数字一旦各处不同，偶发失败
    就很难归因（改好一处，另七处还在按旧数字等）。

    ``_settle`` 是第二轮清出来的：五个模块各有一份「等 MainWindow 背后那几个后台任务
    收工」（合计 114 处调用），写法一模一样，而且**全都超时静默返回** —— 扫描卡住与
    扫描完成在返回值上完全一样。判据与轮询现在都在 :mod:`waiting` 里
    （:func:`waiting.window_is_idle` / :func:`waiting.wait_for_idle`），不成立直接抛。

    **只拦「自己转循环」的**（见 :func:`_spins_the_loop_itself`），不按名字一刀切：
    那几个模块仍保留着同名的一行包装（``groups=True`` / ``index=False`` 之类的额外
    条件确实各不相同），但循环与判据都是 :mod:`waiting` 的 —— 把这种包装也拦掉，
    等于逼着 114 处调用点各自把 ``what=`` 抄一遍，那是另一种「同一件事多种写法」。
    """
    reserved = {
        "wait_until",
        "_wait",
        "_settle",
        "wait_for_pending",
        "_wait_for_scan",
        "wait_rows",
        "wait_for_idle",
    }
    offenders: list[str] = []
    for path in sorted(pathlib.Path(__file__).resolve().parent.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name in reserved
                and _spins_the_loop_itself(node)
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, (
        f"这些模块自己写了一份轮询循环，请用 waiting.wait_until / waiting.wait_for_level /"
        f" waiting.wait_for_idle：{offenders}"
    )


def test_the_polling_interval_is_defined_once() -> None:
    """轮询间隔与默认超时也只有一份 —— 两个数字在八处各写一遍时，偶发失败没法归因。

    ``READY_TIMEOUT_SECONDS`` 是第二轮清出来的：四个模块各自把它定义成 ``8.0``，
    恰好等于 :data:`waiting.DEFAULT_TIMEOUT_SECONDS` —— 同一件事两份定义，改了
    :mod:`waiting` 里那份，四处静悄悄地继续按旧数字等。
    """
    offenders: list[str] = []
    for path in sorted(pathlib.Path(__file__).resolve().parent.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for name in (
            "POLL_SECONDS",
            "WAIT_SECONDS",
            "SCAN_TIMEOUT_SECONDS",
            "READY_TIMEOUT_SECONDS",
        ):
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, f"这些模块自己定义了超时/间隔常数，请用 waiting 里的：{offenders}"
