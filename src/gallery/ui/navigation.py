"""相册模式的导航计算（纯函数，不依赖 Qt）。

面包屑与「上一级」这类路径推导最容易出错（相对路径、走出库、根目录的名称），
所以它们都在这里用普通 pytest 覆盖，界面只负责把结果画出来。
"""

from __future__ import annotations

from pathlib import Path


def breadcrumb_parts(root: Path, current: Path) -> list[tuple[str, Path]]:
    """面包屑：``(显示名, 路径)``，从库根一直到当前层。

    ``current`` 不在 ``root`` 之下时只返回库根一项 —— 界面据此回退到库根，
    而不是画一条走不通的路径。
    """
    root = Path(root)
    current = Path(current)
    label = root.name or str(root)
    parts: list[tuple[str, Path]] = [(label, root)]
    try:
        relative = current.relative_to(root)
    except ValueError:
        return parts
    for index, name in enumerate(relative.parts):
        parts.append((name, root.joinpath(*relative.parts[: index + 1])))
    return parts


def parent_of(root: Path, current: Path) -> Path | None:
    """上一级目录；已经在库根（或不在库内）时返回 ``None``。"""
    root = Path(root)
    current = Path(current)
    if current == root or root not in current.parents:
        return None
    return current.parent


__all__ = ["breadcrumb_parts", "parent_of"]
