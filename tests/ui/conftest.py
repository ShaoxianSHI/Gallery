"""UI 测试共用的 fixture 与等待工具。

界面里的扫描、缩略图、元数据读取都跑在后台线程上，测试只能靠「转事件循环 + 看
状态」等它们。这里提供两种等法，区别很重要：

- ``settle(seconds)`` —— 单纯转一会儿事件循环。用在「必须等满，否则测不到」的场景，
  比如「这 200ms 里不该出现加载提示」：那种等待时长本身就是被断言的一部分。
- ``wait_for(predicate, timeout=...)`` —— 转到条件成立为止。**``timeout`` 只是上限，
  不是固定开销**：条件一成立立刻返回。

固定 sleep 的毛病是必须比真实耗时长才能在慢机器上也稳，于是每次都在空转。换成
``wait_for`` 之后，超时上限原样保留当保险，常态耗时还回来，而且**不会比原来更早
断言**，只会更早结束。

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
#: ``QApplication.doubleClickInterval()``），于是测「第二次点击到底算不算改名」的用例
#: 也只能照同一个值去等。
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
    循环 gc，销毁顺序随机器负载而变，于是「谁先被拆掉」成了掷硬币 —— 表现出来是
    **用例全绿、最后进程 SIGSEGV**（退出码 139，汇总行都来不及打印）。

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
    """手动收尾一次（回归测试用得上；平时靠 autouse 的 :func:`qt_teardown` 就够了）。"""
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

    离屏下没人点「确定」，弹窗一旦漏出去，那个用例**永远不返回** —— 报出来的是
    「这个文件跑了五分钟还没完」，而不是一条红的断言。

    **要拦的是那几个 ``static`` 方法，不是 ``exec``**：``critical`` / ``question``
    这些静态辅助函数在 C++ 里就把模态循环跑完了，压根不经过 Python 那一层，
    所以换掉实例方法 ``QMessageBox.exec`` 等于没拦。``src/`` 里现存的四处调用
    **全是**静态形式（``banner_flow`` 的 ``critical``、``thumbnail_flow`` 的
    ``question`` 与它读回来的 ``StandardButton``），这几个名字一个都不能漏。

    要测弹窗的用例自己 monkeypatch 掉对应那个静态方法即可
    （见 ``test_library_open.py`` 与 ``test_thumbnail_window.py``）：用例里的
    那次 ``setattr`` 跑在本 fixture 之后，自然盖过它。
    """

    def _refuse(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        raise AssertionError(
            "这个用例弹出了一个模态对话框，而离屏测试里没人点确定 —— 它会一直挂着。"
            "要测它就自己 monkeypatch 掉（QMessageBox.critical / .question / "
            ".warning / .information）。"
        )

    for name in ("critical", "question", "warning", "information", "about"):
        monkeypatch.setattr(QMessageBox, name, _refuse)


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

    模块里重定义一份不报错、也不冲突，只是**悄悄丢掉共享那一半**：``qt_app``
    少了 ``setDoubleClickInterval(40)`` 就跑在系统默认的 500ms 上（测试照常全绿，
    没人看得见少掉的那一句）；同名的 ``settle(qt_app, seconds)`` 则把共享的那一份顶掉。
    等待助手与 ``WAIT_SECONDS`` 同理：它们的问题不是慢，是同一件事有多份写法。
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

    判据是三样同时出现：**有一个循环**（``for`` / ``while``）、循环里调了
    ``processEvents``、以及（``monotonic`` 或 ``sleep``）。三样一起才有轮询的样子。

    **「要有循环」这一条不能省**：只判后两样的话，量一次关窗耗时的那种
    （``processEvents`` + ``monotonic``，压根没有循环）会被当成轮询循环报上来 ——
    它量的是时长，不是等待。
    """
    spins = any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "processEvents"
        for loop in ast.walk(node)
        if isinstance(loop, (ast.For, ast.While))
        for call in ast.walk(loop)
    )
    if not spins:
        return False
    calls = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return bool({"sleep", "monotonic"} & (calls | names))


def test_no_test_module_reimplements_the_wait_helper() -> None:
    """等待助手**只有 :mod:`waiting` 里的那几份**，任何测试模块都不许再自己写循环。

    问题不在慢 —— 在于**同一件事有多种写法**：改一处不会改到其余几处，而「该等多久
    才算超时」这种数字一旦各处不同，偶发失败就很难归因（改好一处，另几处还在按旧
    数字等）。等 MainWindow 背后那几个后台任务结束的判据也在 :mod:`waiting` 里
    （:func:`waiting.window_is_idle` / :func:`waiting.wait_for_idle`），不成立直接抛。

    **按「写法」判，不按函数名判**：按名字要有一份白名单，而白名单看着整齐，实际是
    「只有我想得到的那几种写法才受管」。

    仍然**不拦只有 ``processEvents`` 的那种一行包装**（见 :func:`_spins_the_loop_itself`）：
    循环与判据都是 :mod:`waiting` 的，包装只是换个默认参数 —— 把这种也拦掉，
    等于逼着上百处调用点各自把 ``what=`` 抄一遍，那是另一种「同一件事多种写法」。
    """
    offenders: list[str] = []
    for path in sorted(pathlib.Path(__file__).resolve().parent.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and _spins_the_loop_itself(node):
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, (
        f"这些模块自己写了一份轮询循环，请用 waiting.wait_until / waiting.wait_for_level /"
        f" waiting.wait_for_idle / waiting.wait_while_watching：{offenders}"
    )


def test_the_polling_interval_is_defined_once() -> None:
    """轮询间隔与默认超时也只有一份 —— 两个数字在八处各写一遍时，偶发失败没法归因。

    ``READY_TIMEOUT_SECONDS`` 这种同义名也算：它恰好等于
    :data:`waiting.DEFAULT_TIMEOUT_SECONDS`，同一件事两份定义，改了 :mod:`waiting`
    里那份，其余各处静悄悄地继续按旧数字等。
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
