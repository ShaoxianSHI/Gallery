"""列出 `src/gallery/` 里还没进文案目录的中文（CJK）字符串字面量。

**只报字符串，不报注释与文档字符串**：那两类不是给用户看的话，翻不翻是另一回事。
默认跳过 `core/locales/`（文案目录本身当然全是中文）。

用法::

    uv run python scripts/i18n_audit.py              # 每个文件一处计数
    uv run python scripts/i18n_audit.py --list       # 逐条：文件:行:值
    uv run python scripts/i18n_audit.py --tests      # 连 tests/ 一起数（含断言原文）
    uv run python scripts/i18n_audit.py --check      # 有残留就退出 1（迁移收尾用）

判据是 AST，所以注释里的中文不会冒出来；字符串拼接（f-string）报的是其中每一段字面量。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
SOURCE_ROOTS: Final = (
    ROOT / "src" / "gallery" / "core",
    ROOT / "src" / "gallery" / "ui",
    ROOT / "src" / "gallery" / "cli.py",
    ROOT / "src" / "gallery" / "macos_app.py",
)
CATALOG_DIR: Final = ROOT / "src" / "gallery" / "core" / "locales"

#: 汉字、假名、谚文都算：多语言之后这里也不该再冒出任何一种。
_CJK: Final = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def _python_files(roots: tuple[Path, ...] | list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.py")))
    return files


#: 这些调用里的字符串不是界面文案：日志、开发者异常、调试输出。
_NOT_COPY_CALLS: Final = frozenset(
    {
        "debug",
        "info",
        "warning",
        "error",
        "exception",
        "critical",
        "print",
        "ValueError",
        "KeyError",
        "IndexError",
        "RuntimeError",
        "AssertionError",
        "TypeError",
    }
)


def _silent_nodes(tree: ast.AST) -> set[int]:
    """不属于用户文案的 ``Constant`` 的 id：文档/说明性裸字符串、日志与开发者异常的参数。"""
    silent: set[int] = set()
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        parent = parents.get(id(node))
        # 裸字符串表达式：文档字符串或类体里的说明文字，不是界面文案。
        if isinstance(parent, ast.Expr):
            silent.add(id(node))
            continue
        if not isinstance(parent, ast.Call):
            continue
        func = parent.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in _NOT_COPY_CALLS:
            silent.add(id(node))
    return silent


def literals(path: Path) -> list[tuple[int, str]]:
    """这个文件里的 ``(行号, 字符串)``，跳过文档字符串。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - 源文件写坏时 ruff 先报
        print(f"{path}: {exc}", file=sys.stderr)
        return []
    skip = _silent_nodes(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            if _CJK.search(node.value):
                out.append((node.lineno, node.value))
    return out


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="逐条列出文件:行:值")
    parser.add_argument("--tests", action="store_true", help="把 tests/ 也数进来")
    parser.add_argument("--check", action="store_true", help="有残留就退出 1")
    parser.add_argument("--locales", action="store_true", help="把 core/locales/ 也算进来")
    args = parser.parse_args(argv)

    files = _python_files(SOURCE_ROOTS)
    if args.tests:
        files += _python_files([ROOT / "tests"])
    if not args.locales:
        files = [path for path in files if CATALOG_DIR not in path.parents]

    per_file: Counter[str] = Counter()
    unique: set[str] = set()
    for path in files:
        hits = literals(path)
        if not hits:
            continue
        per_file[_relative(path)] = len(hits)
        unique.update(text for _, text in hits)
        if args.list:
            for line, text in hits:
                print(f"{_relative(path)}:{line}:{text!r}")

    total = sum(per_file.values())
    print(f"{total} 处 / {len(unique)} 条不重复，分布在 {len(per_file)} 个文件", file=sys.stderr)
    for name, count in per_file.most_common():
        print(f"{count:5d} {name}", file=sys.stderr if args.list else sys.stdout)
    if args.tests:
        print("（含 tests/ 的断言原文，不要把总数当成待翻译量）", file=sys.stderr)
    return 1 if args.check and total else 0


if __name__ == "__main__":
    raise SystemExit(main())
