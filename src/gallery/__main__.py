"""``python -m gallery`` 的入口。

存在它是为了让 ``Gallery.app`` 的启动器只需要 ``exec <解释器> -m gallery`` ——
不用在命令行里拼 ``-c "from gallery.cli import main; ..."``。
"""

from __future__ import annotations

from gallery.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
