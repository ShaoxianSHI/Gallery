"""与桌面系统打交道的那几个动作：在访达或 QSpace 里打开、用指名的应用打开、往剪贴板里放东西。

``QDesktopServices`` 没有「在访达里选中并露出它」那个动作，所以走 ``open -R``，起不来就降级成
打开所在文件夹（ADR 0014 决策 3）；剪贴板放的是**文件引用**（``QMimeData.setUrls``，ADR 0017）。
**「显示」与「打开文件夹」是两件不同的事**：访达靠 ``-R`` 分，QSpace 靠递**文件**还是递**文件夹**
分 —— 见 :func:`reveal_in_finder` / :func:`open_folder_in_finder` 与那两个 ``_in_qspace``。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QMimeData, QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

from gallery.core.i18n import t, t_plural

logger = logging.getLogger(__name__)

#: 「在访达中显示」「打开文件夹」「在 QSpace 里打开」用的系统命令。找不到它就降级。
REVEAL_PROGRAM = "open"

#: 同一个 ``open`` 命令的「reveal」选项：在访达里**选中**它们，而不是打开它们。
REVEAL_FLAG = "-R"


def copy_to_clipboard(text: str) -> None:
    """把一段文本放进剪贴板（覆盖上一次的内容）。"""
    QGuiApplication.clipboard().setText(text)


def copy_files_to_clipboard(paths: Sequence[Path]) -> str:
    """把这些**文件**放进剪贴板，返回一句说清实际做了什么的中文文案。

    放的是文件引用，不是路径文字、也不是像素：访达里 ⌘V 得到一份拷贝（ADR 0017 决策 1）。
    **不需要 pyobjc**：``QMimeData.setUrls`` 写的正是访达认的公开类型，放引用不读原图。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.clipboard.empty")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(target)) for target in targets])
    QGuiApplication.clipboard().setMimeData(data)
    if len(targets) == 1:
        return t("banner.desktop.clipboard.copied.single", name=targets[0].name)
    return t_plural("banner.desktop.clipboard.copied.many", len(targets))


def read_files_from_clipboard() -> list[Path]:
    """剪贴板里那些**是文件**的路径；剪贴板里没有文件就当空的。

    Gallery 自己拷的与访达拷的都读得到（两边写的是同一种剪贴板类型，见 ADR 0017）。**只认本地
    文件**：从浏览器里拷过来的 ``https://…`` 不是文件。读一次约 1 微秒，所以随时问得起。
    """
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return []
    return [Path(url.toLocalFile()) for url in data.urls() if url.isLocalFile()]


def format_paths(paths: Sequence[Path]) -> str:
    """若干条路径 → 剪贴板里的文本，一行一个（访达的「拷贝路径」也是这个格式）。"""
    return "\n".join(str(path) for path in paths)


def open_with_default_app(path: Path) -> str | None:
    """用系统为这类文件设定的**默认应用**打开它，返回一句需要说出口的话（成功时没有）。

    **成功时不说话**：那个应用自己弹到前台就是反馈；失败必须说一句，静默不动会被当成卡住。
    **没有「打开方式…」那种选应用的版本**：那会改掉系统对这个类型的默认设置。
    """
    target = Path(path)
    if QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
        return None
    return t("banner.desktop.open.default_failed", name=target.name)


def open_with_app(paths: Sequence[Path], *, app: Path, name: str) -> str:
    """用**指名的那个应用**打开这些文件，返回一句说清实际做了什么的中文文案。

    ``app`` 是 ``core.external`` 从名册上查到的**应用包路径**、``name`` 是写进文案的名字 ——
    **不能把名字递给 ``open -a``**：它不认中文显示名（实测「预览」会被它拒掉）。起不来就退回
    系统默认应用并照实说，静默不动会被当成卡住。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.open.no_files")
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *map(str, targets)]):
        refusal = open_with_default_app(targets[0])
        if refusal is not None:
            return refusal
        return t("banner.desktop.open.app_refused", name=name, file=targets[0].name)
    if len(targets) == 1:
        return t("banner.desktop.open.app.single", name=name, file=targets[0].name)
    return t_plural("banner.desktop.open.app.many", len(targets), name=name)


def reveal_in_finder(paths: Sequence[Path]) -> str:
    """在访达里选中并露出这些路径，返回一句说清**实际做了什么**的中文文案。

    **这一条是给照片用的**（要选中文件本身，不是打开它里面）；文件夹走
    :func:`open_folder_in_finder`。降级只有两级：``open -R`` 起不来 → 用 ``QDesktopServices``
    打开所在文件夹。文案必须与实际动作一致：弹出访达却什么都没选中，比明说更糟。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.reveal.no_paths")
    if not QProcess.startDetached(REVEAL_PROGRAM, [REVEAL_FLAG, *map(str, targets)]):
        directory = targets[0].parent
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        if opened:
            return t("banner.desktop.reveal.fallback", folder=directory)
        return t("banner.desktop.finder.no_response")
    if len(targets) == 1:
        return t("banner.desktop.reveal.single", name=targets[0].name)
    return t_plural("banner.desktop.reveal.many", len(targets))


def open_folder_in_finder(paths: Sequence[Path]) -> str:
    """在访达里**打开这些文件夹**（落到它们**里面**去），返回一句中文文案。

    与 :func:`reveal_in_finder` 正好相反，也因此**不能互相顶替**：用户对着相册要看的是里面
    摆着什么。**只对文件夹用** —— ``open`` 递一个文件进去是交给默认应用打开它（那是
    :func:`open_with_default_app` 的事）。降级同级：起不来就逐个用 ``QDesktopServices`` 打开。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.folder.no_folders")
    if not QProcess.startDetached(REVEAL_PROGRAM, [*map(str, targets)]):
        opened = [QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))) for target in targets]
        if not all(opened):
            return t("banner.desktop.finder.no_response")
    if len(targets) == 1:
        return t("banner.desktop.folder.single", name=targets[0].name)
    return t_plural("banner.desktop.folder.many", len(targets))


def open_folder_in_qspace(paths: Sequence[Path], *, app: Path) -> str:
    """在 QSpace 里**打开这些文件夹**（落到它们**里面**去），返回一句中文文案。

    与 :func:`open_folder_in_finder` 是同一件事的两个软件，同样**不能拿 :func:`reveal_in_finder`
    顶替**：用户对着相册想看的是它**里面**摆着什么。递过去的是 ``file://`` 地址（QSpace 自己
    注册的 URL 方案，路径文字不是它认的输入）；``app`` 是 core 查到的应用包 —— 这一层不许
    import core，于是由 ``MainWindow`` 递进来，起不来时降级成访达并照实说。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.folder.no_folders")
    urls = [QUrl.fromLocalFile(str(target)).toString() for target in targets]
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *urls]):
        opened = [QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))) for target in targets]
        if not all(opened):
            return t("banner.desktop.qspace.no_response")
        if len(targets) == 1:
            return t("banner.desktop.qspace.folder_fallback.single", name=targets[0].name)
        return t_plural("banner.desktop.qspace.folder_fallback.many", len(targets))
    if len(targets) == 1:
        return t("banner.desktop.qspace.folder.single", name=targets[0].name)
    return t_plural("banner.desktop.qspace.folder.many", len(targets))


def open_folder_in_terminal(paths: Sequence[Path], *, app: Path) -> str:
    """在终端里**打开这些文件夹**（新窗口的工作目录就是它），返回一句中文文案。

    与 :func:`open_folder_in_finder` 是同一件事换了个软件：终端把目录参数当成新窗口的工作目录，
    于是 ``open -a <终端> <文件夹>`` 就够，**不能拿 :func:`reveal_in_finder` 顶替** —— 用户要的
    是进到这一层里面。``app`` 是 core 查到的应用包（这一层不许 import core，由 ``MainWindow``
    递进来）；起不来就降级成访达并照实说。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.folder.no_folders")
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *map(str, targets)]):
        opened = [QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))) for target in targets]
        if not all(opened):
            return t("banner.desktop.terminal.no_response")
        if len(targets) == 1:
            return t("banner.desktop.terminal.folder_fallback.single", name=targets[0].name)
        return t_plural("banner.desktop.terminal.folder_fallback.many", len(targets))
    if len(targets) == 1:
        return t("banner.desktop.terminal.folder.single", name=targets[0].name)
    return t_plural("banner.desktop.terminal.folder.many", len(targets))


def reveal_in_qspace(paths: Sequence[Path], *, app: Path) -> str:
    """在 QSpace 里**选中并露出这些文件**（打开它们所在的那一层），返回一句中文文案。

    **QSpace 没有 ``-R`` 那一支**：``-R`` 是访达自己的语义，把它连着 ``-a QSpace`` 一起递过去，
    弹出来的仍然是访达（实测）。QSpace 认的是**地址里的那一样东西** —— 递一个文件过去，它打开
    所在的那一层并把这个文件选出来，于是与 :func:`reveal_in_finder` 效果一致。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return t("banner.desktop.reveal.no_paths")
    urls = [QUrl.fromLocalFile(str(target)).toString() for target in targets]
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *urls]):
        # 降级借 ``reveal_in_finder``：它自己还有第二级（选不中就打开所在文件夹），
        # 抄一份下来就成了两条各自演化的降级链。
        return t("banner.desktop.qspace.reveal_fallback", message=reveal_in_finder(targets))
    if len(targets) == 1:
        return t("banner.desktop.qspace.reveal.single", name=targets[0].name)
    return t_plural("banner.desktop.qspace.reveal.many", len(targets))


# ---- 系统音效 -------------------------------------------------------------------

#: Finder 把文件移到废纸篓时实际请求的是系统音效表的第 16 项
#: （``SystemSounds/dock/drag to trash.aif``）。**不是同名的那份**
#: ``finder/move to trash.aif``（第 14 项）—— 那一项对应另一条动作。
#: 编号不是公开常量，但 Apple 从 10.5 起一直用这个值；音效文件的名字与路径才是会变的那一头。
TRASH_SOUND_ID = 16

#: Finder 撤销那次删除时请求的是第 1 项（``SystemSounds/system/Volume Mount.aif``）。
#: 名字与用途对不上，但 Finder 的撤销确实用这一声。
UNDO_TRASH_SOUND_ID = 1

_AUDIO_TOOLBOX = "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"

_player: Callable[[int], None] | None = None
_player_resolved = False


def play_move_to_trash_sound() -> None:
    """播放 Finder 的「移到废纸篓」音效。**失败就安静**：一段音效不该让删除报错。

    **开关与音量都归 macOS 管**：系统设置里关掉「播放用户界面声音效果」时不会出声。
    """
    _play(TRASH_SOUND_ID)


def play_undo_trash_sound() -> None:
    """播放 Finder 撤销删除时的音效。失败与开关的处理跟删除音效完全一样。"""
    _play(UNDO_TRASH_SOUND_ID)


def _play(sound_id: int) -> None:
    try:
        _play_system_sound(sound_id)
    except Exception:
        logger.debug("系统音效未能播放", exc_info=True)


def _play_system_sound(sound_id: int) -> None:
    player = _system_sound_player()
    if player is not None:
        player(sound_id)


def _system_sound_player() -> Callable[[int], None] | None:
    """加载 AudioToolbox 里播放系统音效的那个函数；做不到就返回 ``None``（只试一次）。"""
    global _player, _player_resolved
    if _player_resolved:
        return _player
    _player_resolved = True
    if sys.platform != "darwin":
        return None
    try:
        library = ctypes.CDLL(_AUDIO_TOOLBOX)
        function = library.AudioServicesPlaySystemSound
        function.argtypes = [ctypes.c_uint32]
        function.restype = None
        _player = cast("Callable[[int], None]", function)
    except (OSError, AttributeError):
        logger.debug("系统音效不可用", exc_info=True)
    return _player


__all__ = [
    "REVEAL_FLAG",
    "REVEAL_PROGRAM",
    "TRASH_SOUND_ID",
    "UNDO_TRASH_SOUND_ID",
    "copy_files_to_clipboard",
    "copy_to_clipboard",
    "format_paths",
    "open_folder_in_finder",
    "open_folder_in_qspace",
    "open_folder_in_terminal",
    "open_with_app",
    "open_with_default_app",
    "play_move_to_trash_sound",
    "play_undo_trash_sound",
    "read_files_from_clipboard",
    "reveal_in_finder",
    "reveal_in_qspace",
]
