"""Gallery 命令行入口。

v0.1 阶段仅用于验证环境与承载批处理任务（扫描、导出元数据）。
界面入口见 ``gallery.ui``。
"""

from __future__ import annotations

import sys

from gallery import __version__


def main(argv: list[str] | None = None) -> int:
    """打印版本信息并返回退出码。"""
    del argv  # 参数解析将在 M1 引入
    print(f"Gallery {__version__}")
    print(f"Python {sys.version.split()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
