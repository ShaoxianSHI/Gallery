"""Gallery 命令行入口。

默认启动图形界面；``--version`` 只打印版本，不加载 PySide6，
因此在没有图形环境（或还没装 Qt）的地方也能用来确认安装是否正常。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from gallery import __version__
from gallery.core.i18n import t


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="gallery",
        description=t("app.cli.description"),
    )
    parser.add_argument(
        "library",
        nargs="?",
        type=Path,
        help=t("app.cli.library_help"),
    )
    parser.add_argument("--version", action="store_true", help=t("app.cli.version_help"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """程序入口，返回进程退出码。"""
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if args.version:
        print(f"Gallery {__version__}")
        print(f"Python {sys.version.split()[0]}")
        return 0

    # 日志先落地再干别的：起不来的那一段（认不出参数、没装 Qt）最需要它。
    from gallery.core.logs import configure_logging

    configure_logging()

    # 延迟导入：--version 不应该拖着 Qt 一起加载。
    from gallery.ui.app import run

    library: Path | None = args.library
    return run(library=library)


if __name__ == "__main__":
    raise SystemExit(main())
