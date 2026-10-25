"""退不出去的那一类现场：读不出来的照片与关窗。

云盘（OneDrive 之类）把照片放进文件夹，而文件还没下载完 —— ``stat`` 得到正常的大小与时间，
**读却永远不返回**。那一个解码任务就把一个工作线程永久占住；占满之后整条缩略图流水线停摆，
而关窗时 Qt 的线程池在析构时又要 join 它们 —— 窗口关了、事件循环退了，**进程却永远不结束**。

这一组把「⌘Q 一定能结束」钉成两件事：

1. 关窗要**丢掉排队里还没开始的任务**（``abandon``），否则一次丢进来几百张时，
   关窗要一个一个地等它们跑完；
2. 已经在跑的那几个要**问一句还要不要继续**，好在切库 / 关窗时立刻放手。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.tasks import SHUTDOWN_TIMEOUT_MS, TaskRunner  # noqa: E402


def _run_fresh_python(
    script: str, *args: str, timeout: float = 90.0
) -> subprocess.CompletedProcess[bytes]:
    """在子进程里跑：这里要验的正是「进程退不退得出去」，本进程退不出去就没法测了。

    **刻意不接管道**（用 DEVNULL 而不是 ``capture_output``）：打开库会起 ``exiftool``
    子进程，它继承走标准输出，而 ``subprocess`` 要读到 EOF 才返回 —— 那个进程比本测试
    活得久，于是父测试会一直等下去，测的却不是关窗。结论写进 ``args`` 里的那个文件。
    """
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


#: ⌘Q 走的就是 ``MainWindow._quit_action`` 那个槽。跑完把结论写进 ``sys.argv[2]``：
#: 「能走到最后一行」本身就是答案 —— 它意味着解释器收尾没有卡住。
_QUIT_UNDER_STUCK_WORKERS = """
import os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PIL.Image
from PIL import Image

real_open = PIL.Image.open
BLOCKED = "拉不下来"
NEVER = threading.Event()

def stuck_open(fp, *args, **kwargs):
    # 等一把永远不会被 set 的锁：阻塞在 ``wait`` 里会**释放 GIL**，与真的卡在
    # ``open()`` 的系统调用上同形。用 ``while True: sleep()`` 是不行的 ——
    # 那是个 Python 忙等，它攥着 GIL，会把整个进程饿死，测的就不是同一件事了。
    if BLOCKED in str(getattr(fp, "name", fp)):
        NEVER.wait()
    return real_open(fp, *args, **kwargs)

PIL.Image.open = stuck_open

from pathlib import Path
from gallery.core.library.config import prepare_library
from gallery.core.library.session import SessionState, save_session
from gallery.ui.app import create_application
from gallery.ui.main_window import MainWindow

root = Path(sys.argv[1])
album = root / "相册"
album.mkdir(parents=True, exist_ok=True)
Image.new("RGB", (400, 300), (30, 120, 200)).save(album / "好照片.jpg")
for i in range(12):
    Image.new("RGB", (400, 300), (200, 30, 30)).save(album / f"拉不下来{i}.jpg")

prepare_library(root)
base = root.parent / "状态"
save_session(SessionState(recent_roots=(root,)), base=base)
app = create_application([])
# ``cache_base`` 要**显式传**：这是个子进程，父进程那条 autouse 的
# ``isolated_app_data`` 管不到它 —— 不传的话那几张能解码的照片会往用户真实的
# ``~/Library/Application Support/Gallery/cache/thumbs/`` 丢下一个没人认领的摘要目录。
window = MainWindow(session_base=base, cache_base=Path(sys.argv[3]), initial_library=root)
window.resize(900, 600)
window.show()
deadline = time.monotonic() + 6
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)

started = time.monotonic()
window._quit_action.trigger()          # ⌘Q 走的就是这一个槽
elapsed = time.monotonic() - started
Path(sys.argv[2]).write_text(f"CLOSE_SECONDS {elapsed:.2f}\\nREAPED", encoding="utf-8")
"""


def test_the_process_really_exits_when_decodes_are_stuck(tmp_path: Path) -> None:
    """整条钉在这里：**进程自己退得出去**，而不是「窗口关了」。

    子进程跑完这一段之后退出码为 0，就是答案 —— 它必须自己走完解释器收尾。
    硬上限由 ``subprocess.run`` 的 ``timeout`` 兜着：退不出去时那正是要认出来的失败。
    """
    root = tmp_path / "库"
    root.mkdir()
    verdict = tmp_path / "结论.txt"
    cache_base = tmp_path / "系统缓存"

    completed = _run_fresh_python(
        _QUIT_UNDER_STUCK_WORKERS, str(root), str(verdict), str(cache_base)
    )

    assert verdict.read_text(encoding="utf-8").startswith("CLOSE_SECONDS"), "压根没走到关窗那一步"
    assert completed.returncode == 0, (
        "有解码卡死时进程退不出去（只能强制退出），子进程退出码 "
        f"{completed.returncode}（-9 / 超时都算）"
    )


def test_closing_drops_the_tasks_that_have_not_started_yet() -> None:
    """排队里还没开始的任务必须丢掉，不能一个一个跑完。

    ``cancel_all`` 只动令牌：排队的那些照样会一个个跑起来，每一个都可能是一次几秒的
    解码。丢进来几百张时，关窗要等好几分钟 —— 表现上与「卡死」分不出彼此。

    **线程池必须只给一个线程**，否则后面的任务根本进不了「排队」，这条就什么都没测到。
    """
    import threading

    runner = TaskRunner(max_threads=1)
    gate = threading.Event()
    started = threading.Event()
    done: list[str] = []

    def blocking(_context: object) -> object:
        started.set()
        gate.wait()
        done.append("跑完了")
        return None

    def quick(_context: object) -> object:
        done.append("跑完了")
        return None

    try:
        runner.submit(blocking)
        assert started.wait(5), "第一个任务没跑起来"
        for _ in range(5):  # 排在后面、还轮不到
            runner.submit(quick)

        runner.abandon(timeout_ms=SHUTDOWN_TIMEOUT_MS)

        assert done == [], "尚未开始的任务在关窗时照样跑了 —— 关窗会被拖住"
    finally:
        # 一定放行并**等它真的结束**：这个 TaskRunner 是个裸的局部变量，它被回收时会连带析构
        # 线程池，而池里那个任务还卡在 ``gate.wait()`` 上 —— 形状见 ``tests/ui/conftest.py``
        # 里 qt_teardown 那段注释。
        gate.set()
        runner.abandon(timeout_ms=5000)
        runner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_abandon_reports_whether_everything_finished() -> None:
    """``abandon`` 的返回值要说实话：全部结束为 ``True``，否则调用方该记一条日志。"""
    runner = TaskRunner()
    runner.submit(lambda _context: None)

    assert runner.abandon(timeout_ms=2000) is True


def test_a_window_closes_cleanly_while_photos_are_unreadable(
    qt_app: object, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """真窗口走一遍：有读不出来的照片在场时，关窗照样结束。"""
    root = tmp_path / "库"
    root.mkdir()
    make_photo("一张.jpg", directory=root)
    make_photo("空文件.jpg", directory=root, data=b"")
    prepare_library(root)
    base = tmp_path / "状态"
    save_session(SessionState(recent_roots=(root,)), base=base)
    window = MainWindow(session_base=base, initial_library=root)
    window.show()
    try:
        qt_app.processEvents()  # type: ignore[attr-defined]
        started = time.monotonic()
        window.close()
        elapsed = time.monotonic() - started
    finally:
        qt_app.processEvents()  # type: ignore[attr-defined]

    assert elapsed < SHUTDOWN_TIMEOUT_MS / 1000 + 2, (
        f"关窗花了 {elapsed:.1f} 秒 —— 那就是被什么卡住了"
    )
