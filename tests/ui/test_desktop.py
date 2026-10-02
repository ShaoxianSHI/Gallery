"""``ui.desktop`` 里那两个「把文件交给系统」的动作（ADR 0014 决策 3 / 7）。

只测纯函数那一层：**真跑会把访达与默认应用叫起来**，那是测试机上真实的副作用。这里把
``QDesktopServices`` 换成假实现，于是「交给系统的是什么」与「系统不接时我们说什么」都能
钉死，而一个文件也不必真的存在。

窗口那一侧（什么时候可用、快捷键按不按得出来）在 ``test_context_menu.py`` 里测 ——
它离不开真实的 ``MainWindow``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from gallery.ui import desktop  # noqa: E402


class _DesktopServices:
    """假的 ``QDesktopServices``：记下交给它的 URL，按 ``answers`` 回话。"""

    def __init__(self, *answers: bool) -> None:
        self._answers = list(answers)
        self.urls: list[str] = []

    def openUrl(self, url: object) -> bool:  # noqa: N802 - 名字是 Qt 定的
        self.urls.append(url.toLocalFile())  # type: ignore[attr-defined]
        return self._answers.pop(0) if self._answers else True


def test_open_with_default_app_hands_the_path_to_the_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交给系统的是**这个文件本身**（本地文件 URL），不是路径文字、也不是目录。

    路径文字交给 ``QDesktopServices`` 只会被当成一个网页地址去搜 —— 那正是把路径传错
    地方之后最常见的症状，所以这里断言的是 URL 本身。
    """
    services = _DesktopServices(True)
    monkeypatch.setattr(desktop, "QDesktopServices", services)

    message = desktop.open_with_default_app(Path("/照片/京都/一张.jpg"))

    assert services.urls == ["/照片/京都/一张.jpg"]
    # 成功**不说话**：那个应用自己会弹到前台，横幅不必再抢一句。
    assert message is None


def test_open_with_default_app_says_why_when_the_system_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """系统不接时必须说一句带文件名的话 —— 静默不动会被当成程序卡住了。"""
    monkeypatch.setattr(desktop, "QDesktopServices", _DesktopServices(False))

    message = desktop.open_with_default_app(Path("/照片/京都/一张.jpg"))

    assert message is not None
    assert "一张.jpg" in message


def test_open_with_default_app_talks_to_the_system_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次点击只递一次请求。

    ``QDesktopServices.openUrl`` 只吃一个 URL，所以「打开多张」要么逐个调、要么根本
    没实现 —— 这条命令在产品里只对当前这一张可用（多选时菜单里压根不出现），因此这里
    钉死的就是**一张照片一次请求**，不多不少。
    """
    services = _DesktopServices(True)
    monkeypatch.setattr(desktop, "QDesktopServices", services)

    desktop.open_with_default_app(Path("/照片/京都/一张.jpg"))

    assert len(services.urls) == 1
