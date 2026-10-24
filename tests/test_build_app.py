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


def test_pypinyin_data_files_are_included(tmp_path: Path) -> None:
    """拼音词典是包内 JSON，不显式带上，打包后的应用一启动就会 FileNotFoundError。"""
    module = _load()

    command = module.build_command(tmp_path / "Gallery.icns", tmp_path / "dist")

    assert "--include-package-data=pypinyin" in command


def test_the_qt_multimedia_plugins_are_included(tmp_path: Path) -> None:
    """Qt6 的多媒体后端目录叫 multimedia（Nuitka 的 sensible 名单里只有 Qt5 的 mediaservice）。

    不点名要，包里就只有 QtMultimedia 这个库、一个播放后端也没有：视频页一打开就报找不到后端。
    """
    module = _load()

    command = module.build_command(tmp_path / "Gallery.icns", tmp_path / "dist")

    assert "--include-qt-plugins=multimedia" in command


def test_qt_network_is_not_trimmed_away() -> None:
    """QtMultimedia 与多媒体插件都链接着 QtNetwork —— 删了它，视频就放不了。"""
    module = _load()

    assert "QtNetwork" not in module._ORPHANED_QT_LIBS
