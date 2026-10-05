"""与桌面系统打交道的那几个动作：在访达里显示、往剪贴板里放文本或文件。

``QDesktopServices`` 没有「在访达里选中并露出它」那个动作，所以走 ``open -R``，起不来就降级成
打开所在文件夹（ADR 0014 决策 3）；剪贴板放的是**文件引用**（``QMimeData.setUrls``，ADR 0017）。
这里只读，不做粘进剪贴板（那是 ``core.metadata.paste`` 的事）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QMimeData, QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

#: 访达里「在访达中显示」用的系统命令。找不到它就降级去开文件夹。
REVEAL_PROGRAM = "open"

#: 同一个 ``open`` 命令的「reveal」选项：在访达里**选中**它们，而不是打开它们。
REVEAL_FLAG = "-R"


def copy_to_clipboard(text: str) -> None:
    """把一段文本放进剪贴板（覆盖上一次的内容）。"""
    QGuiApplication.clipboard().setText(text)


def copy_files_to_clipboard(paths: Sequence[Path]) -> str:
    """把这些**文件**放进剪贴板，返回一句说清实际做了什么的中文文案。

    放的是文件引用，不是路径文字、也不是像素：访达里 ⌘V 得到一份拷贝，粘到邮件里是附件
    （ADR 0017 决策 1）。相册是文件夹，同样算。

    **不需要 pyobjc**：``QMimeData.setUrls`` 写的 ``public.file-url`` 与
    ``NSFilenamesPboardType`` 正是访达认的那几个**公开**类型（ADR 0014 里那句「Qt 放不进去」
    是错的）。放引用而不是数据，因此**拷多少张、每张多大都是同一次系统调用**，也不读原图。
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

    与 :func:`reveal_in_finder` 相反的那一头（那个留在访达里挑出来，这个交给别的程序）。
    **成功时不说话**：那个应用自己会弹到前台，那就是反馈；失败必须说一句 —— 静默不动会被
    当成程序卡住了。**没有「打开方式…」那种选应用的版本**：那会改掉系统对这个类型的默认
    设置，不该由 Gallery 的一个菜单项顺手做掉。
    """
    target = Path(path)
    if QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
        return None
    return f"没能用默认应用打开「{target.name}」，请检查它是否还在原处。"


def reveal_in_finder(paths: Sequence[Path]) -> str:
    """在访达里选中并露出这些路径，返回一句说清**实际做了什么**的中文文案。

    降级只有两级：``open -R`` 起不来 → 用 ``QDesktopServices`` 打开所在文件夹。文案必须与
    实际动作一致：弹出访达却什么都没选中，比明说「只打开了文件夹」糟糕得多。
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


__all__ = [
    "REVEAL_FLAG",
    "REVEAL_PROGRAM",
    "copy_files_to_clipboard",
    "copy_to_clipboard",
    "format_paths",
    "open_with_default_app",
    "read_files_from_clipboard",
    "reveal_in_finder",
]
