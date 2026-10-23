"""``scripts/build_app.py`` 的目标路径：文件夹与 ``.app`` 包路径都认。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from gallery.core.constants import APP_NAME

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_app.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_app_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 解析字符串注解时要回头查模块，必须先登记再执行。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_folder_destination_gets_the_app_name(tmp_path: Path) -> None:
    """``~/Applications`` 是「放包的文件夹」，不是名字里没有扩展名的包。"""
    module = _load()

    assert module.resolve_bundle(tmp_path / "Applications") == (
        tmp_path / "Applications" / f"{APP_NAME}.app"
    )


def test_an_app_path_is_left_alone(tmp_path: Path) -> None:
    module = _load()
    bundle = tmp_path / f"{APP_NAME}.app"

    assert module.resolve_bundle(bundle) == bundle
    assert module.resolve_bundle(tmp_path / f"{APP_NAME}.APP") == tmp_path / f"{APP_NAME}.APP"


def test_an_existing_file_is_not_mistaken_for_a_folder(tmp_path: Path) -> None:
    """占位的普通文件原样交给目标检查，由它报「这里已经有东西」，而不是拼成文件下的路径。"""
    module = _load()
    occupied = tmp_path / "notes.txt"
    occupied.write_text("", encoding="utf-8")

    assert module.resolve_bundle(occupied) == occupied
