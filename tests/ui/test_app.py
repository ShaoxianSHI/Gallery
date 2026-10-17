"""应用这一层：⌘W 关掉窗口与 ⌘Q 退出是**两件事**（ADR 0037）。

macOS 上这两件事本来是两件事：⌘W 关掉窗口，**应用还活着**（Dock 里那枚图标还亮着，
点它就把窗口摆回来）；⌘Q 才是退出。Qt 不是这么想的 —— 默认「最后一个窗口一关，事件
循环就退」，于是关窗与退出被绑成同一件。

这里钉三样：

1. :func:`gallery.ui.app.keep_running_without_windows` 把那条默认关掉；
2. :class:`gallery.ui.app.WindowHost` 在应用回到前台而一个窗口都没有时重新开一个，
   已经摆着窗口时**不多开**；
3. **在子进程里真跑一遍事件循环**：⌘W 之后进程还活着，⌘Q 才退。

第 3 条得跑在子进程里：这里要验的正是「进程退不退得出去」，本进程退不出去就没法测
（与 ``tests/ui/test_quit_under_unreadable_photos.py`` 同一个理由）。判据是那一份
按顺序写下来的记录 —— ``⌘W`` 关窗之后还得有「还活着」那一条，而 ``⌘Q`` 之后
才轮得到 ``exec()`` 返回。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl  # noqa: E402
from PySide6.QtGui import QFileOpenEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.app import WindowHost, keep_running_without_windows  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 子进程里那一份脚本。**刻意不接管道**（用 DEVNULL）：打开库会起 exiftool 子进程而它继承走
#: 标准输出，``subprocess`` 要读到 EOF 才返回 —— 那等上的根本不是关窗这件事。结论按顺序追加。
_LIFECYCLE = """
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from gallery.ui.app import WindowHost, create_application, keep_running_without_windows

session_base = Path(sys.argv[1])
notes = Path(sys.argv[2])


def note(text: str) -> None:
    with notes.open("a", encoding="utf-8") as handle:
        handle.write(text + "\\n")


app = create_application([])
keep_running_without_windows(app)
host = WindowHost(app, session_base=session_base)   # 必须活到 exec() 结束
window = host.open_window()

# ⌘W 走的就是 close()；⌘Q 走的就是 quit_app()。
QTimer.singleShot(40, lambda: (window.close(), note("CLOSED")))
# 关掉之后应用还活着：事件循环还在转，这一条还得来。
QTimer.singleShot(200, lambda: note("ALIVE"))
QTimer.singleShot(400, lambda: (note("QUITTING"), window.quit_app()))
note(f"EXITED {app.exec()}")
"""


def test_the_app_is_told_not_to_quit_when_the_last_window_closes(
    qt_app: QApplication,
) -> None:
    """⌘W 的「不退出应用」那一半就在这一行上。

    **改动要还原**：``qt_app`` 是模块级 fixture，而这条把它整个模块的默认值改了 ——
    不还回去，后面的用例就在一个来路不明的应用配置下跑。
    """
    original = qt_app.quitOnLastWindowClosed()
    try:
        keep_running_without_windows(qt_app)

        assert qt_app.quitOnLastWindowClosed() is False
    finally:
        qt_app.setQuitOnLastWindowClosed(original)


def test_coming_back_to_the_app_reopens_a_window(qt_app: QApplication, tmp_path: Path) -> None:
    """⌘W 关掉之后从 Dock 点回来：开的是**新一个窗口**，关掉那个不是捡回来用。

    **新一个而不是把旧的复活**：``closeEvent`` 已经停掉文件监听、收了后台任务
    （见 :meth:`MainWindow.closeEvent`），那个窗口此时只剩一个空壳 —— 复用它等于
    造一个「库是空的、网格是上一层」的开局。
    """
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        first = host.open_window()
        qt_app.processEvents()
        first.close()  # ⌘W 走的就是它
        assert first.isVisible() is False

        host.reopen_if_needed()
        qt_app.processEvents()

        assert host.window is not None
        assert host.window is not first, "关掉的那个已经被收干净了，不该被捡回来"
        assert host.window.isVisible() is True
    finally:
        _discard(host)


def test_a_window_that_is_still_there_is_left_alone(qt_app: QApplication, tmp_path: Path) -> None:
    """还在就不动：应用被激活而已，已经摆着窗口时再开一个只会凭空多一扇。"""
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        first = host.open_window()
        qt_app.processEvents()

        host.reopen_if_needed()
        qt_app.processEvents()

        assert host.window is first
    finally:
        _discard(host)


def test_cmd_w_keeps_the_process_alive_and_cmd_q_ends_it(tmp_path: Path) -> None:
    """**整条链在一个子进程里真跑一遍**：⌘W 关窗、进程继续转、⌘Q 才退出。

    两半缺一不可，而且**都只能在这儿验**：

    - ⌘W 退出了应用 —— 记录里会缺掉 ``ALIVE`` 那一行（进程在 200ms 之前就走了）；
    - ⌘Q 退不出去 —— 事件循环不返回，子进程被 ``timeout`` 掐掉（这一条失败要等
      满时限，所以上限给得短；走的时候一秒不到）。

    窗口那份记录还要留着 ``QUITTING`` 之前的那几条：顺序本身就是「先关窗、再退出」。
    """
    notes = tmp_path / "记录.txt"

    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_LIFECYCLE), str(tmp_path / "状态"), str(notes)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, f"子进程退出码 {completed.returncode}（-9 / 超时都算）"
    assert notes.read_text(encoding="utf-8").splitlines() == [
        "CLOSED",
        "ALIVE",
        "QUITTING",
        "EXITED 0",
    ]


def _discard(host: WindowHost) -> None:
    """把一个 :class:`WindowHost` 与它的窗口确定性地收掉。

    ``WindowHost`` 是 ``QApplication`` 的子对象，不显式删掉就一直挂着 ——
    而它连着 ``applicationStateChanged``，后面那条用例里应用状态一变就会多开一个窗口。
    """
    if host.window is not None:
        host.window.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_host_keeps_a_reference_to_the_window_it_opened(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """开之前是「一个都没有」，开之后手上那一个就是它 —— 没有窗口的那半条路也是真走的。"""
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        assert host.window is None

        window = host.open_window()

        assert host.window is window
        assert isinstance(window, MainWindow)
    finally:
        _discard(host)


def test_a_folder_from_outside_is_opened_in_the_window_that_is_already_there(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> None:
    """Dock 右键菜单里点一个库，系统发 Apple Event，Qt 交给我们一个 ``QFileOpenEvent``。

    窗口摆着的时候就**换库**，不再开一个 —— 它与「⌘W 之后从 Dock 点回来」不是同一件事
    （那一条是要一个窗口，这一条是要这个库）。
    """
    other = _library_with_a_photo(tmp_path, make_photo, "另一个库")
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        window = host.open_window()
        qt_app.processEvents()

        handled = qt_app.sendEvent(qt_app, QFileOpenEvent(str(other)))
        qt_app.processEvents()

        assert handled is True
        assert host.window is window, "库是换到手上这个窗口里的，不是另开一个"
        assert window._library is not None
        assert window._library.root == other
    finally:
        _discard(host)


def test_a_folder_from_outside_opens_a_window_when_there_is_none(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> None:
    """一个窗口都没有时（刚启动、或⌘W 之后）：**开一个带上这个库**，而不是回到欢迎页。

    这一条走 URL 那一支（系统送来的是 ``file://`` 开头的 URL），上一条走 ``file()`` 那一支。
    """
    root = _library_with_a_photo(tmp_path, make_photo, "照片")
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        handled = qt_app.sendEvent(qt_app, QFileOpenEvent(QUrl.fromLocalFile(str(root))))
        qt_app.processEvents()

        assert handled is True
        assert host.window is not None
        assert host.window._library is not None
        assert host.window._library.root == root
    finally:
        _discard(host)


def test_a_file_from_outside_is_not_ours(qt_app: QApplication, tmp_path: Path) -> None:
    """送来的是文件而不是文件夹时不管 —— Gallery 打开的是库，不是一个文件。"""
    photo = tmp_path / "一张.jpg"
    photo.write_bytes(b"jpg")
    host = WindowHost(qt_app, session_base=tmp_path / "状态")
    try:
        handled = qt_app.sendEvent(qt_app, QFileOpenEvent(str(photo)))
        qt_app.processEvents()

        assert handled is False
        assert host.window is None
    finally:
        _discard(host)


def _library_with_a_photo(tmp_path: Path, make_photo: Callable[..., Path], name: str) -> Path:
    """造一个有照片的库目录，返回它 —— 空目录也算库，但真的开得起来才说明这一路是通的。"""
    root = tmp_path / name
    root.mkdir()
    make_photo("一张.jpg", directory=root)
    return root
