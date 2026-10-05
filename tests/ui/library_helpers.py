"""界面测试共用的**库级**夹具助手。

**为什么是独立模块而不是 ``conftest.py``**：``conftest.py`` 是给 fixture 与 autouse
钩子用的，里面的普通函数只能靠 ``from conftest import ...`` 捡到 —— 而那条 import
走的是 pytest 给 conftest 自己插的那条 ``sys.path``，换个收集顺序就不成立。
``waiting.py`` 与 ``browser/grid_helpers.py`` 是同样的东西，也都在各自的目录里
各占一个真模块，这里跟着它们走。
"""

from __future__ import annotations

import pathlib

from gallery.core.library.config import (
    LibraryConfig,
    LibraryUIState,
    prepare_library,
    save_config,
)


def set_library_ui(root: pathlib.Path, **preferences: object) -> None:
    """把**库级**界面偏好写进 ``root`` 的 ``.gallery/config.toml``。

    这些偏好 2026-10-05 起住在库里而不是 ``state.json`` 里（ADR 0038 决策 4），
    所以要让一个库「一打开就是某个浏览方式 / 某种排序」，写的不是会话文件，
    而是这个库的配置文件。

    **别在测试模块里自己拼一遍**：这七项刚从 ``state.json`` 搬过一次家，
    各自在模块里写一份的话，下次再搬就得改一遍所有模块
    （``tests/ui/conftest.py`` 里那条 ``test_no_test_module_shadows_the_shared_fixtures``
    守着同一类问题）。

    :param preferences: :class:`~gallery.core.library.config.LibraryUIState` 的字段名。
    """
    library = pathlib.Path(root)
    prepare_library(library)
    save_config(library, LibraryConfig(ui=LibraryUIState(**preferences)))  # type: ignore[arg-type]


__all__ = ["set_library_ui"]
