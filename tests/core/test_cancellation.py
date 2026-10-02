"""``should_cancel`` 的默认替身只有一份（见 ``core.cancellation`` 的模块文档）。"""

from __future__ import annotations

import ast
from pathlib import Path

from gallery.core.cancellation import never_cancel

CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gallery" / "core"


def test_never_cancel_says_keep_going() -> None:
    """它是个永远说「没取消」的替身 —— 调用方 ``should_cancel or never_cancel`` 之后
    少写那半句三元，扫描器、批量读取与两个写操作因此不必各留一份。"""
    assert never_cancel() is False
    check = never_cancel
    assert check() is False  # 可以直接当回调传出去


def test_no_module_redefines_the_stand_in() -> None:
    """核心层里**只有这一处**定义「永远不取消」，别的地方不许再抄一份。

    这条曾经散成四份一模一样的 ``_never_cancel``（scanner / batch / paste / transfer），
    而它们没有任何理由不同 —— 抄来抄去只会各自漂移。
    """
    offenders: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "cancellation.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "_never_cancel",
                "never_cancel",
            }:
                offenders.append(f"{path.relative_to(CORE_ROOT)}:{node.lineno} {node.name}")
    assert not offenders, f"核心层里有多于一份的「永远不取消」：{offenders}"
