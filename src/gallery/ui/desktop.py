"""与桌面系统打交道的那几个动作：在访达或 QSpace 里打开、往剪贴板里放文本或文件。

``QDesktopServices`` 没有「在访达里选中并露出它」那个动作，所以走 ``open -R``，起不来就降级成
打开所在文件夹（ADR 0014 决策 3）；剪贴板放的是**文件引用**（``QMimeData.setUrls``，ADR 0017）。
**「显示」与「打开文件夹」是两件不同的事**：访达靠 ``-R`` 分，QSpace 靠递**文件**还是递**文件夹**
分 —— 见 :func:`reveal_in_finder` / :func:`open_folder_in_finder` 与那两个 ``_in_qspace``。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QMimeData, QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

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
        return "没有可拷贝的文件。"
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(target)) for target in targets])
    QGuiApplication.clipboard().setMimeData(data)
    if len(targets) == 1:
        return f"已拷贝「{targets[0].name}」到剪贴板。"
    return f"已拷贝 {len(targets)} 项到剪贴板。"


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
    return f"没能用默认应用打开「{target.name}」，请检查它是否还在原处。"


def reveal_in_finder(paths: Sequence[Path]) -> str:
    """在访达里选中并露出这些路径，返回一句说清**实际做了什么**的中文文案。

    **这一条是给照片用的**（要选中文件本身，不是打开它里面）；文件夹走
    :func:`open_folder_in_finder`。降级只有两级：``open -R`` 起不来 → 用 ``QDesktopServices``
    打开所在文件夹。文案必须与实际动作一致：弹出访达却什么都没选中，比明说更糟。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return "没有可显示的路径。"
    if not QProcess.startDetached(REVEAL_PROGRAM, [REVEAL_FLAG, *map(str, targets)]):
        directory = targets[0].parent
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        if opened:
            return f"没能选中这些文件，已改为在访达里打开「{directory}」。"
        return "访达没能响应，请检查系统的权限设置。"
    if len(targets) == 1:
        return f"已在访达中显示「{targets[0].name}」。"
    return f"已在访达中显示 {len(targets)} 项。"


def open_folder_in_finder(paths: Sequence[Path]) -> str:
    """在访达里**打开这些文件夹**（落到它们**里面**去），返回一句中文文案。

    与 :func:`reveal_in_finder` 正好相反，也因此**不能互相顶替**：用户对着相册要看的是里面
    摆着什么。**只对文件夹用** —— ``open`` 递一个文件进去是交给默认应用打开它（那是
    :func:`open_with_default_app` 的事）。降级同级：起不来就逐个用 ``QDesktopServices`` 打开。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return "没有可打开的文件夹。"
    if not QProcess.startDetached(REVEAL_PROGRAM, [*map(str, targets)]):
        opened = [QDesktopServices.openUrl(QUrl.fromLocalFile(str(t))) for t in targets]
        if not all(opened):
            return "访达没能响应，请检查系统的权限设置。"
    if len(targets) == 1:
        return f"已在访达中打开「{targets[0].name}」。"
    return f"已在访达中打开 {len(targets)} 个文件夹。"


def open_folder_in_qspace(paths: Sequence[Path], *, app: Path) -> str:
    """在 QSpace 里**打开这些文件夹**（落到它们**里面**去），返回一句中文文案。

    与 :func:`open_folder_in_finder` 是同一件事的两个软件，同样**不能拿 :func:`reveal_in_finder`
    顶替**：用户对着相册想看的是它**里面**摆着什么。递过去的是 ``file://`` 地址（QSpace 自己
    注册的 URL 方案，路径文字不是它认的输入）；``app`` 是 core 查到的应用包 —— 这一层不许
    import core，于是由 ``MainWindow`` 递进来，起不来时降级成访达并照实说。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return "没有可打开的文件夹。"
    urls = [QUrl.fromLocalFile(str(target)).toString() for target in targets]
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *urls]):
        opened = [QDesktopServices.openUrl(QUrl.fromLocalFile(str(t))) for t in targets]
        if not all(opened):
            return "QSpace 没能响应，访达也没能打开，请检查系统的权限设置。"
        if len(targets) == 1:
            return f"没能交给 QSpace，已改用访达打开「{targets[0].name}」。"
        return f"没能交给 QSpace，已改用访达打开 {len(targets)} 个文件夹。"
    if len(targets) == 1:
        return f"已在 QSpace 中打开「{targets[0].name}」。"
    return f"已在 QSpace 中打开 {len(targets)} 个文件夹。"


def reveal_in_qspace(paths: Sequence[Path], *, app: Path) -> str:
    """在 QSpace 里**选中并露出这些文件**（打开它们所在的那一层），返回一句中文文案。

    **QSpace 没有 ``-R`` 那一支**：``-R`` 是访达自己的语义，把它连着 ``-a QSpace`` 一起递过去，
    弹出来的仍然是访达（实测）。QSpace 认的是**地址里的那一样东西** —— 递一个文件过去，它打开
    所在的那一层并把这个文件选出来，于是与 :func:`reveal_in_finder` 效果一致。
    """
    targets = [Path(path) for path in paths]
    if not targets:
        return "没有可显示的路径。"
    urls = [QUrl.fromLocalFile(str(target)).toString() for target in targets]
    if not QProcess.startDetached(REVEAL_PROGRAM, ["-a", str(app), *urls]):
        # 降级借 ``reveal_in_finder``：它自己还有第二级（选不中就打开所在文件夹），
        # 抄一份下来就成了两条各自演化的降级链。
        return f"没能交给 QSpace，{reveal_in_finder(targets)}"
    if len(targets) == 1:
        return f"已在 QSpace 中显示「{targets[0].name}」。"
    return f"已在 QSpace 中显示 {len(targets)} 项。"


__all__ = [
    "REVEAL_FLAG",
    "REVEAL_PROGRAM",
    "copy_files_to_clipboard",
    "copy_to_clipboard",
    "format_paths",
    "open_folder_in_finder",
    "open_folder_in_qspace",
    "open_with_default_app",
    "read_files_from_clipboard",
    "reveal_in_finder",
    "reveal_in_qspace",
]
