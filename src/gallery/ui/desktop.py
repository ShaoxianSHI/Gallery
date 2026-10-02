"""与桌面系统打交道的那几个动作：在访达里显示、往剪贴板里放文本或文件。

这里会是**界面层第一个调起系统进程的地方**（``reveal_in_finder`` 走 ``open -R``），
所以先说清楚为什么：

``QDesktopServices.openUrl`` 只有「打开」这一个动作，没有「在访达里选中并露出它」——
访达的「在访达中显示」走的是 ``open -R <路径>``。为了让右键菜单与访达对齐，这里用了
``QProcess.startDetached``。这是权衡的结果，不是随手写的：进程起不来就降级成打开所在文件夹，
并在界面上如实说明做了什么（见 ADR 0014 决策 3）。

刻意只放这三件事：

- **往剪贴板里放文件是做得到的，不必引 pyobjc**（见 :func:`copy_files_to_clipboard` 与
  ADR 0017）。这里原先写着「访达认可的文件引用类型 Qt 放不进去」，那句话是错的：实测
  ``QMimeData.setUrls`` 写的两个公开类型与访达自己 ⌘C 时写的完全一样。
- **不碰元数据、不碰原图像素**。所有文件访问仍然只经过 ``gallery.core`` 的门面
  （见 ``core.library.facade``）。放进剪贴板的是一个**路径引用**：不读像素、不写文件。
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
    （访达的 ⌘C 也是这么放的，见 ADR 0017 决策 1）。相册是文件夹，同样算。

    **不需要 pyobjc，这条是复核过的**：早先 ADR 0014 以为「访达认可的文件引用类型 Qt 放不
    进去」，实测并非如此 ——

    ==========================  ======  ==============
    剪贴板类型                   访达    ``setUrls()``
    ==========================  ======  ==============
    ``public.file-url``          ✓       ✓
    ``NSFilenamesPboardType``    ✓       ✓
    ``Apple URL pasteboard …``   ✓       ✓
    两个私有的 ``dyn.*``          ✓       —（访达自己的账）
    ==========================  ======  ==============

    缺的只是访达私有的那两个类型，而决定能不能粘的是上面那几个公开的。

    放引用而不是数据，因此**拷多少张、每张多大都是同一次系统调用**，也不会把原图读进内存。
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


def format_paths(paths: Sequence[Path]) -> str:
    """若干条路径 → 剪贴板里的文本，一行一个。

    访达的「拷贝路径」也是这个格式，别的软件（终端、编辑器）能直接贴。
    """
    return "\n".join(str(path) for path in paths)


def reveal_in_finder(paths: Sequence[Path]) -> str:
    """在访达里选中并露出这些路径，返回一句说清**实际做了什么**的中文文案。

    降级只有两级：``open -R`` 起不来 → 用 ``QDesktopServices`` 打开所在文件夹。
    文案一定与实际动作一致，界面把它原样放进横幅 —— 弹出访达却什么都没选中，
    比明说「只打开了文件夹」糟糕得多。
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
    "reveal_in_finder",
]
