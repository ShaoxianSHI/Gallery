"""缩略图进行中失败时该不该报给用户。

给一张照片改名，网格刚把它换到新路径上，而**改名之前**那次解码还在工作线程里跑着 ——
它读的是旧路径，文件已经不在了，解码失败是必然的。所以失败这一侧必须与成功的回填
（``PhotoListModel.set_thumbnail``）**对称**：同样按「这一格还在不在当前层」判。

少了那条判据就是**假警报**，而且是最坏的一种：横幅会从「已重命名为 海边日落.jpg」变成
「有 1 个文件无法显示缩略图：无法解码 IMG_0001.jpg」—— 把一次成功的改名说成了数据损坏。

这一组把两侧钉在同一条判据上：仍然在屏幕上的坏文件照报，已经离开这一层的静默丢掉。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

DECODE_FAILURE = "无法解码 IMG_0001.jpg：文件可能已损坏或被截断。"


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]) -> MainWindow:
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("IMG_0001.jpg", directory=root)
    prepare_library(root)
    session_base = tmp_path / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    qt_app.processEvents()
    return instance


def _first_path(window: MainWindow, wait_for: Callable[..., bool]) -> Path:
    """等这一层真的列出来，再取第一行。

    扫描在后台线程上：只转一圈事件循环并不等于列完了，模型里可能还一行都没有。
    """
    assert wait_for(lambda: window._model.rowCount() > 0, timeout=DEFAULT_TIMEOUT_SECONDS), (
        "这一层没有在时限内列出来"
    )
    entry = window._model.entry_at(0)
    assert entry is not None
    return entry.path


def test_a_broken_photo_still_on_screen_is_reported(
    qt_app: QApplication, window: MainWindow, wait_for: Callable[..., bool]
) -> None:
    """真的坏了就得说 —— 判据只放过「已经离开这一层」的，不放过读不出来的。"""
    path = _first_path(window, wait_for)
    assert window._model.shows_path(path) is True
    window._clear_banner()  # 横幅是共享的一个槽，窗口刚开时可能正挂着别的话

    window._on_thumbnail_failed(path, DECODE_FAILURE)

    assert "无法显示缩略图" in window._banner.text()
    assert "损坏" in window._banner.text()


def test_a_failure_for_a_photo_that_left_is_dropped_silently(
    qt_app: QApplication, window: MainWindow, wait_for: Callable[..., bool]
) -> None:
    """改名 / 移走 / 删掉之后的进行中失败：静默丢掉，绝不说「文件可能已损坏」。"""
    old = _first_path(window, wait_for)
    assert window._model.shows_path(old) is True

    # 改名发生：旧路径离开这一层，新路径顶上同一行。
    renamed = old.with_name("海边日落.jpg")
    assert window._model.replace_entry(0, PhotoRef(path=renamed, size=1, mtime_ns=1)) is True
    assert window._model.shows_path(old) is False

    window._on_thumbnail_failed(old, DECODE_FAILURE)  # 改名之前那次解码这时候才失败

    assert window._thumbnail_failures == 0, "不该被当成「照片读不出来」记一笔"
    assert DECODE_FAILURE not in window._banner.text()


def test_a_failure_for_a_path_the_app_just_wrote_is_dropped(
    qt_app: QApplication, window: MainWindow, wait_for: Callable[..., bool]
) -> None:
    """撤销改名那一类：文件已经回来了，照片好得很，不该报「损坏」。

    撤销的时序是：解码在改名之前就起了，读到的是那个**正在被自家移走的**路径，
    失败是必然的；而等它报上来，文件早就被撤销操作放回去了。这种失败是并发产物，
    不是照片坏了 —— 报出来就是把一次成功的撤销说成了数据损坏。
    """
    path = _first_path(window, wait_for)
    renamed = path.with_name("海边日落.jpg")
    # 模拟自家刚刚改过这个路径（改名 / 移到废纸篓 / 撤销都会记上），随后撤销把它换了回来。
    window._note_self_write(path)
    assert window._model.replace_entry(0, PhotoRef(path=renamed, size=1, mtime_ns=1)) is True
    assert window._model.replace_entry(0, PhotoRef(path=path, size=1, mtime_ns=1)) is True
    assert window._model.shows_path(path) is True  # 照片回来了，就还「在屏幕上」

    window._on_thumbnail_failed(path, DECODE_FAILURE)

    assert window._thumbnail_failures == 0, "不该被当成「照片读不出来」记一笔"
    assert DECODE_FAILURE not in window._banner.text()


# ---- 后台消息不许盖掉刚刚那个动作 -------------------------------------------------


def test_a_background_message_waits_its_turn_after_a_user_action(
    qt_app: QApplication, window: MainWindow, wait_for: Callable[..., bool]
) -> None:
    """刚做完一件事之后，横幅先把那句话说完，后台的失败让位。

    把照片移到废纸篓之后，网格立刻去给留下来的那些行要缩略图，于是「刚被自家动过、
    这一刻读不到」的那一张解码失败会**迟一步**报上来。横幅只有一个槽，抢到的就成了一句与
    刚做的操作无关的解码错误，而「1 张没移走」那句话没了。
    """
    path = _first_path(window, wait_for)
    window._clear_banner()
    window._show_banner("1 张照片没有移走：早就不在了.jpg")

    window._on_thumbnail_failed(path, DECODE_FAILURE)

    assert window._thumbnail_failures == 1, "失败本身仍然要记账"
    assert window._banner.text() == "1 张照片没有移走：早就不在了.jpg"


def test_a_background_message_still_gets_through_when_nothing_is_pending(
    qt_app: QApplication, window: MainWindow, wait_for: Callable[..., bool]
) -> None:
    """让位只针对「刚刚」：没有动作占着的时候，解码失败照常要说出来。"""
    path = _first_path(window, wait_for)
    window._clear_banner()

    window._on_thumbnail_failed(path, DECODE_FAILURE)

    assert "无法显示缩略图" in window._banner.text(), "没有用户动作占着时，后台消息必须照常上报"
