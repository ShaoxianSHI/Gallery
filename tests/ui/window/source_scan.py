"""扫 ``src/gallery/ui`` 的那几间回归测试共用的一个入口。

这一层有六份文件按 AST 读界面层的源码（窗口 mixin、状态对象、换层那几处），而
``src/gallery/ui`` 相对 ``tests/ui/window/`` 有几级得数对 —— 数错一级不会报错，
只会**扫到零个文件**，于是回归测试一路绿（AGENTS.md：「永远为绿的回归测试比没有更坏」）。
所以这一个入口把那个断言带上，六间共用一份。
"""

from __future__ import annotations

from pathlib import Path


def source_root() -> Path:
    """``src/gallery/ui`` 那一层。

    **``parents[3]`` 不是 ``[2]``**：本文件在 ``tests/ui/window/`` 下，往上三级才是仓库根。
    写成 ``[2]`` 时那个路径指向 ``tests/src/gallery/ui``（不存在），``rglob`` 扫到
    **零个文件**而那几条回归测试一路绿。
    """
    root = Path(__file__).resolve().parents[3] / "src" / "gallery" / "ui"
    assert root.is_dir(), f"扫的目录不存在：{root}（会静默扫零个文件而一路绿）"
    return root
