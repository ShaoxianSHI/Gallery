"""界面测试的收尾：控件必须死在 ``QApplication`` 还完整活着的时候。

这里测的是测试台架自己的一条约定（``conftest`` 里的 ``teardown_qt`` / ``qt_teardown``），
不是产品代码。但它值得有自己的测试：这条约定一旦被摘掉，症状是**用例全绿、进程最后
SIGSEGV**，退出码 139，而且随机性只来自时序 —— 现场极难复现，往往被误判成「机器的问题」。

它之所以必要，是因为界面控件在 Python 这侧**回收不掉**：网格与模型之间的信号连接把
闭包和网格一起按在模型的 C++ 侧，而视图又在 C++ 侧持有模型，引用环横跨了 Python 与
C++ 两边 —— 引用计数归不了零，循环 gc 也看不见（环的一半根本不是 Python 对象）。
控件因此既不会被用例自己回收，也等不到 ``QApplication`` 的析构去收拾，一路活到
``Py_FinalizeEx``，而那里的销毁顺序随机器负载而变。实测每轮全跑必崩（12/12）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402


def _open_window(tmp_path: Path, make_photo: Callable[..., Path]) -> MainWindow:
    """造一个真的开了库、也真的在监听的窗口。"""
    root = tmp_path / "库"
    (root / "相册").mkdir(parents=True)
    make_photo("a.jpg", directory=root / "相册")
    prepare_library(root)
    session_base = tmp_path / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.show()
    return window


def test_the_teardown_fixture_is_autouse_for_every_ui_test(
    request: pytest.FixtureRequest,
) -> None:
    """这个模块里那几条只钉住了收尾**怎么做**；这一条钉住它**每个用例都会跑**。

    ``autouse`` 一旦被摘掉，同一个模块里那几条照样绿，但整套界面测试会重新变成
    「进程收尾时掷硬币」。而现场看到的是退出码 139 的随机红灯，几乎没人能当场
    联想到这一行。
    """
    assert "qt_teardown" in request.fixturenames


def test_a_widget_nobody_closes_still_gets_destroyed(
    qt_app: QApplication, teardown_qt: Callable[[], None]
) -> None:
    """没人管的顶层控件（界面测试的常态）也必须被收尾摘掉。"""
    grid = PhotoGrid()
    grid.show()
    qt_app.processEvents()
    assert grid in qt_app.topLevelWidgets(), "控件根本没被造出来，这条测试就没意义"

    teardown_qt()

    assert grid not in qt_app.topLevelWidgets()


def test_a_model_hooked_to_a_grid_does_not_pin_the_grid(
    qt_app: QApplication, teardown_qt: Callable[[], None]
) -> None:
    """正对着那个跨语言引用环：装上模型之后，控件照样收得掉。

    没有这一条，「控件回收不掉」很容易被当成 Qt 的正常行为放过去 —— 实际上正是
    ``grid.setModel`` 里那条 ``rowsInserted`` 连接把网格按在了模型身上。
    """
    grid = PhotoGrid()
    model = PhotoListModel()
    model.append(
        [PhotoRef(path=Path(f"/tmp/IMG_{index}.jpg"), size=1, mtime_ns=index) for index in range(4)]
    )
    grid.setModel(model)
    grid.show()
    qt_app.processEvents()
    assert grid in qt_app.topLevelWidgets()

    teardown_qt()

    assert grid not in qt_app.topLevelWidgets()


def test_teardown_returns_the_widget_count_to_where_it_started(
    qt_app: QApplication, teardown_qt: Callable[[], None]
) -> None:
    """收尾是「回到原点」，不是「清掉一部分」：一次多造几个也得全没。"""
    before = len(qt_app.topLevelWidgets())
    made: list[PhotoGrid] = []
    for index in range(3):
        grid = PhotoGrid()
        grid.resize(400 + index, 300)
        grid.show()
        made.append(grid)  # 不留引用的话，引用计数当场就把它收掉了
    qt_app.processEvents()
    assert len(qt_app.topLevelWidgets()) == before + 3

    teardown_qt()

    assert len(qt_app.topLevelWidgets()) == before
    assert not any(grid in qt_app.topLevelWidgets() for grid in made)


def test_a_window_left_open_is_still_fully_torn_down(
    qt_app: QApplication,
    teardown_qt: Callable[[], None],
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> None:
    """窗口被收尾销毁时，必须走完真正的关窗流程，而不是只把像素擦掉。

    这一条是「监听器有没有被停掉」的守护：真监听器带着一个 watchdog 观察者线程，
    漏停一步，那个线程就会在进程收尾时去碰一个已经拆掉的窗口。
    """
    window = _open_window(tmp_path, make_photo)
    qt_app.processEvents()
    assert window._watch.watching, "窗口没有开始监听，这条测试就没意义"

    teardown_qt()

    assert not window._watch.watching, "收尾没有停掉文件监听"
    assert window not in qt_app.topLevelWidgets()


def test_teardown_is_idempotent_after_the_test_closed_its_own_window(
    qt_app: QApplication,
    teardown_qt: Callable[[], None],
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> None:
    """收尾必须是幂等的：用例自己 ``close()`` 过一遍之后再收一次不能炸。

    「自己关窗」是这里绝大多数用例的写法，所以收尾一定会遇上已经关掉的窗口；
    ``QWidget.close()`` 对已关掉的窗口不再发 ``closeEvent``，收尾不能假设自己是第一个。
    """
    window = _open_window(tmp_path, make_photo)
    qt_app.processEvents()
    window.close()  # 用例自己先关一遍
    assert not window._watch.watching

    teardown_qt()
    teardown_qt()  # 再收一次，不该抛

    assert window not in qt_app.topLevelWidgets()
