"""``ui.desktop`` 里那几个「把文件交给系统」的动作（ADR 0014 决策 3 / 7）。

只测纯函数那一层：**真跑会把访达与默认应用叫起来**，那是测试机上真实的副作用。这里把
``QDesktopServices`` 与 ``QProcess`` 换成假实现，于是「交给系统的是什么」与「系统不接时
我们说什么」都能固定，而一个文件也不必真的存在。

窗口那一侧（什么时候可用、快捷键按不按得出来，以及**照片与相册走的是不是同一条**）在
``test_context_menu.py`` 里测 —— 它离不开真实的 ``MainWindow``。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
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


class _Process:
    """假的 ``QProcess``：记下起的是哪个程序、递了哪些参数，按 ``answers`` 回话。

    ``startDetached`` 在真 Qt 里是个**静态**方法，所以这里用一个可调用的替身整体顶掉
    ``desktop.QProcess``；调用点写的是 ``QProcess.startDetached(...)``，属性查找落在这一份上。
    """

    def __init__(self, *answers: bool) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, list[str]]] = []

    def startDetached(self, program: str, arguments: Sequence[str]) -> bool:  # noqa: N802
        self.calls.append((program, list(arguments)))
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


def test_opening_a_folder_goes_inside_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """对着文件夹走的是**不带 ``-R``** 的那个 ``open``：访达落到它**里面**去。

    这一行就是两条路的分界。带了 ``-R`` 访达会停在**上一级**里把这个文件夹选出来 ——
    用户要看的是**里面**摆着什么，看到的是外面那一层。这条断言硬编码参数表就是为了让
    任何人想把两条并成一条时立刻看见红。
    """
    process = _Process(True)
    monkeypatch.setattr(desktop, "QProcess", process)

    message = desktop.open_folder_in_finder([Path("/照片/京都")])

    assert process.calls == [("open", ["/照片/京都"])]
    assert message == "已在访达中打开「京都」。"


def test_opening_several_folders_still_starts_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多选相册时所有路径**一次**递过去（``open`` 本来就收一串），不是一个文件夹一个进程。

    上面那条只递了一个文件夹，于是「按文件夹逐个起进程」这一种退化它照样绿 —— 多选是
    右键菜单上真实存在的那一支（相册与照片走的是同一条路），所以这里固定的是
    **一串路径一次请求**。
    """
    process = _Process(True)
    monkeypatch.setattr(desktop, "QProcess", process)

    message = desktop.open_folder_in_finder([Path("/照片/京都"), Path("/照片/奈良")])

    assert process.calls == [("open", ["/照片/京都", "/照片/奈良"])]
    assert message == "已在访达中打开 2 个文件夹。"


def test_revealing_a_photo_is_still_about_selecting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**照片那一路没动**：仍然 ``-R``，用户要的是在访达里把这些照片**选出来**。

    与上面那条成对：文件夹打开进去、照片选出来，是两件事，谁也别顺手把另一条改了。
    """
    process = _Process(True)
    monkeypatch.setattr(desktop, "QProcess", process)

    message = desktop.reveal_in_finder([Path("/照片/京都/一张.jpg")])

    assert process.calls == [("open", [desktop.REVEAL_FLAG, "/照片/京都/一张.jpg"])]
    assert message == "已在访达中显示「一张.jpg」。"


def test_opening_a_folder_falls_back_to_the_desktop_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``open`` 起不来就交给 Qt —— 递的仍然是**这个文件夹本身**，不是它上一级。

    降级之后文案与正常那一条是同一句：两条路在 macOS 上落地的都是「打开这个文件夹」，
    区别只在谁起的进程，用户看见的结果一样。
    """
    services = _DesktopServices(True)
    monkeypatch.setattr(desktop, "QProcess", _Process(False))
    monkeypatch.setattr(desktop, "QDesktopServices", services)

    message = desktop.open_folder_in_finder([Path("/照片/京都")])

    assert services.urls == ["/照片/京都"]
    assert message == "已在访达中打开「京都」。"


def test_opening_a_folder_says_why_when_the_finder_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两条路都不通时必须说一句 —— 静默不动会被当成程序卡住了。"""
    monkeypatch.setattr(desktop, "QProcess", _Process(False))
    monkeypatch.setattr(desktop, "QDesktopServices", _DesktopServices(False))

    message = desktop.open_folder_in_finder([Path("/照片/京都")])

    assert message is not None
    assert "访达" in message


def test_opening_a_folder_in_qspace_hands_a_file_url_and_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交给 QSpace 的是**应用包 + 一条 ``file://`` 地址**，不是路径文字。

    QSpace 自己注册了 ``file`` URL 方案（见 ``core.external.qspace``），它认的就是地址。
    路径用纯 ASCII 的，那一条地址于是可以逐字断言 —— 中文名会被百分号转义成一串 ``%E2…``，
    断言里就只剩「我抄了一遍编码器」。
    """
    process = _Process(True)
    monkeypatch.setattr(desktop, "QProcess", process)
    app = Path("/Applications/QSpace Pro.app")

    message = desktop.open_folder_in_qspace([Path("/photos/kyoto")], app=app)

    assert process.calls == [
        ("open", ["-a", "/Applications/QSpace Pro.app", "file:///photos/kyoto"])
    ]
    assert message == "已在 QSpace 中打开「kyoto」。"


def test_qspace_falls_back_to_the_finder_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``open`` 起不来就改用访达打开，**并且说清换的是哪一个**。

    含糊不得：用户以为东西开在 QSpace 里、看到的却是访达，比明说换过更难查。
    """
    services = _DesktopServices(True)
    monkeypatch.setattr(desktop, "QProcess", _Process(False))
    monkeypatch.setattr(desktop, "QDesktopServices", services)

    message = desktop.open_folder_in_qspace([Path("/照片/京都")], app=Path("/Applications/Q.app"))

    assert services.urls == ["/照片/京都"]
    assert message == "没能交给 QSpace，已改用访达打开「京都」。"


def test_qspace_says_why_when_nothing_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    """两条路都不通时同样必须说一句。"""
    monkeypatch.setattr(desktop, "QProcess", _Process(False))
    monkeypatch.setattr(desktop, "QDesktopServices", _DesktopServices(False))

    message = desktop.open_folder_in_qspace([Path("/照片/京都")], app=Path("/Applications/Q.app"))

    assert message is not None
    assert "QSpace" in message


def test_showing_a_photo_in_qspace_hands_that_files_url_and_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """露出一张照片递的是**这个文件自己的**地址 —— QSpace 打开它所在的那一层并把它选出来。

    **不能递它所在的文件夹**：那样只会落到文件夹**里面**，与「打开这一层」那一支就没区别了。
    路径用纯 ASCII 的，那一条地址于是可以逐字断言（同上面那条的理由）。
    """
    process = _Process(True)
    monkeypatch.setattr(desktop, "QProcess", process)
    app = Path("/Applications/QSpace Pro.app")

    message = desktop.reveal_in_qspace([Path("/photos/kyoto/one.jpg")], app=app)

    assert process.calls == [
        ("open", ["-a", "/Applications/QSpace Pro.app", "file:///photos/kyoto/one.jpg"])
    ]
    assert message == "已在 QSpace 中显示「one.jpg」。"


def test_showing_a_photo_in_qspace_falls_back_to_the_finder_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``open`` 起不来就交给访达去选，**并且说清换的是哪一个**（与打开文件夹那一条同一个理由）。

    降级用的是 ``reveal_in_finder`` 自己的那一句，于是它还有的第二级（选不中就打开所在文件夹）
    也一并跟来 —— 这里把两次 ``open`` 都挡回去，落到的正是最后那一级。
    """
    services = _DesktopServices(True)
    monkeypatch.setattr(desktop, "QProcess", _Process(False, False))
    monkeypatch.setattr(desktop, "QDesktopServices", services)

    message = desktop.reveal_in_qspace(
        [Path("/照片/京都/一张.jpg")], app=Path("/Applications/Q.app")
    )

    assert services.urls == ["/照片/京都"]
    assert message == "没能交给 QSpace，没能选中这些文件，已改为在访达里打开「/照片/京都」。"
