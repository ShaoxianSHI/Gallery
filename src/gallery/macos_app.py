"""把 Gallery 打包成 macOS 的 ``Gallery.app``，让 Dock、访达、⌘Tab 用上应用图标。

**启动器为什么长这样**：``Contents/MacOS/python`` 是指向解释器的符号链接，启动器 ``exec`` 的
正是这个链接 —— 只有最终运行的那个进程映像位于包内，系统才认得出是这个应用（直接 ``exec``
到 ``.venv`` 里的解释器，图标就丢了）；代价是此时解释器找不到 ``pyvenv.cfg``，要用 CPython
认得的 ``__PYVENV_LAUNCHER__`` 把前缀指回 ``.venv``，这两件事必须成对出现。
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from gallery import __version__
from gallery.core.constants import APP_NAME
from gallery.core.errors import GalleryError
from gallery.core.paths import app_icon_master

#: 打包产物的默认落点（相对仓库根），与 ``.gitignore`` 里的 ``dist/`` 对应。
BUILD_DIR_NAME: Final = "dist"

#: 逆向域名标识符（包标识符不能含大写字母）。换签名或公证时改这里的前缀。
BUNDLE_IDENTIFIER: Final = f"com.example.{APP_NAME.lower()}"

#: ``Contents/MacOS/`` 下的启动脚本名，也就是 ``CFBundleExecutable``。
LAUNCHER_NAME: Final = "Gallery"

#: ``Contents/MacOS/`` 下指向解释器的符号链接名。名字有讲究：它必须在包内。
PYTHON_LINK_NAME: Final = "python"

#: 包内图标文件名（``CFBundleIconFile``）。
ICNS_NAME: Final = "Gallery.icns"

#: ``iconutil`` 要求的 iconset 文件名与边长（像素）。
ICONSET_ENTRIES: Final = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

#: 包内最低系统版本，只影响 LaunchServices 的判断。
MINIMUM_SYSTEM_VERSION: Final = "13.0"

#: 启动时补进 PATH 的常见安装目录。从访达 / Dock 启动的进程只拿到系统那几个目录，Homebrew
#: 装的 exiftool 因此不在里面（读不全元数据，还会一路降级成只读模式）。硬编码在这里是刻意的：
#: 启动器没有登录 shell 的上下文，只能这样把常见安装位置补回去。
EXTRA_PATH_DIRS: Final = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "$HOME/.local/bin",
)

_LAUNCHER_TEMPLATE = """#!/bin/sh
# 由 `uv run python -m gallery.macos_app` 生成，请勿手改：里面的解释器路径是构建时的。
HERE=$(cd "$(dirname "$0")" && pwd)
export __PYVENV_LAUNCHER__="{python}"
if [ ! -x "$HERE/{link}" ]; then
    echo "Gallery 启动失败：找不到解释器 {python}" >&2
    echo "仓库移动过，或虚拟环境被重建过。回到项目目录重新生成即可：" >&2
    echo "    uv sync && uv run python -m gallery.macos_app" >&2
    exit 1
fi
# 从访达 / Dock 启动时 PATH 只有系统目录，Homebrew 装的 exiftool 就找不到。
PATH="$PATH:{extra}"
export PATH
exec "$HERE/{link}" -m gallery "$@"
"""


class BundleError(GalleryError):
    """打包失败：解释器缺失、图标缺失、系统工具不可用、目标位置被占。"""


def find_project_root(start: Path | None = None) -> Path:
    """从 ``start`` 往上找仓库根（含 ``pyproject.toml`` 与 ``src/gallery/``）。

    默认从本模块所在位置开始：源码树里能直接找到，装进虚拟环境后则自然找不到 ——
    那时给出明确的提示，而不是让人对着一个诡异的路径猜。
    """
    origin = (start or Path(__file__)).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "gallery").is_dir():
            return candidate
    raise BundleError(
        "找不到 Gallery 的源码目录。请在项目目录里运行，或用 uv run python -m gallery.macos_app。"
    )


def default_destination(project_root: Path) -> Path:
    """默认生成位置：``<仓库根>/dist/Gallery.app``。"""
    return project_root / BUILD_DIR_NAME / f"{APP_NAME}.app"


def _run_tool(command: Sequence[str], *, missing: str) -> None:
    """跑一个系统工具，把「没装」与「跑挂了」翻译成可行动的中文提示。"""
    try:
        completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BundleError(missing) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise BundleError(
            f"生成图标失败：{command[0]} 退出码 {completed.returncode}", detail=detail
        )


def make_icns(source_png: Path, destination: Path) -> Path:
    """由 1024×1024 的 PNG 生成 macOS 的 ``.icns``。

    走系统自带的 ``sips`` + ``iconutil``：``.icns`` 是系统私有容器格式，
    没必要自己拼，也省掉一份二进制进仓库。
    """
    if not source_png.is_file():
        raise BundleError("找不到应用图标源文件。", path=source_png)

    iconset = destination.parent / f".{destination.stem}.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    try:
        for name, edge in ICONSET_ENTRIES:
            _run_tool(
                [
                    "sips",
                    "-s",
                    "format",
                    "png",
                    "-z",
                    str(edge),
                    str(edge),
                    str(source_png),
                    "--out",
                    str(iconset / name),
                ],
                missing="找不到 macOS 自带的 sips，无法生成 .icns。",
            )
        _run_tool(
            ["iconutil", "-c", "icns", str(iconset), "--output", str(destination)],
            missing="找不到 macOS 自带的 iconutil，无法生成 .icns。",
        )
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
    return destination


def build_info_plist(version: str) -> dict[str, object]:
    """包内 ``Info.plist`` 的内容。``CFBundleIconFile`` 就是 Dock 图标的来源。"""
    return {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleExecutable": LAUNCHER_NAME,
        "CFBundleIconFile": ICNS_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": MINIMUM_SYSTEM_VERSION,
        "LSApplicationCategoryType": "public.app-category.photography",
        "NSHighResolutionCapable": True,
    }


def prepare_destination(destination: Path) -> None:
    """清出干净的目标位置。

    只有「已经是我们生成过的包」才允许删；任何别的目录一律拒绝 ——
    这个模块会做删除，就不能靠猜。
    """
    if not destination.exists():
        return
    if not (destination / "Contents" / "Info.plist").is_file():
        raise BundleError(
            f"目标位置已经有东西，且不是 Gallery.app：{destination}。换个位置，或自己先清掉它。",
            path=destination,
        )
    shutil.rmtree(destination)


def write_launcher(path: Path, *, python: Path) -> Path:
    """写出 ``Contents/MacOS/Gallery`` 启动脚本并加上可执行权限。"""
    path.write_text(
        _LAUNCHER_TEMPLATE.format(
            python=python,
            link=PYTHON_LINK_NAME,
            extra=":".join(EXTRA_PATH_DIRS),
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def build_bundle(
    destination: Path,
    *,
    project_root: Path | None = None,
    python: Path | None = None,
    icns: Path | None = None,
) -> Path:
    """生成（或重建）``destination`` 指向的 ``.app`` 包，返回它的路径。

    :param project_root: 仓库根，默认从本模块所在位置往上找。
    :param python: 启动器要用的解释器，默认 ``<仓库根>/.venv/bin/python``。
    :param icns: 直接使用现成的 ``.icns``；省略则由应用图标现生成。
    """
    root = find_project_root() if project_root is None else Path(project_root)
    interpreter = Path(python) if python is not None else root / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        raise BundleError(
            f"找不到虚拟环境里的解释器：{interpreter}。先在项目目录跑一次 uv sync。",
            path=interpreter,
        )

    prepare_destination(destination)
    contents = destination / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    target_icns = resources / ICNS_NAME
    if icns is None:
        make_icns(app_icon_master(), target_icns)
    elif not Path(icns).is_file():
        raise BundleError("找不到现成的 .icns 文件。", path=Path(icns))
    else:
        shutil.copyfile(icns, target_icns)

    write_launcher(macos / LAUNCHER_NAME, python=interpreter)
    (macos / PYTHON_LINK_NAME).symlink_to(interpreter)
    (contents / "Info.plist").write_bytes(plistlib.dumps(build_info_plist(__version__)))

    # Finder 靠这个属性显示图标；某些系统上不写就要等到图标缓存刷新才认出来。
    destination.touch()
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="gallery.macos_app",
        description="把 Gallery 打包成 macOS 的 Gallery.app，让 Dock、访达、⌘Tab 用上应用图标。",
    )
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help=f"生成位置（默认 <项目目录>/{BUILD_DIR_NAME}/{APP_NAME}.app）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        root = find_project_root()
        destination = args.destination or default_destination(root)
        bundle = build_bundle(destination, project_root=root)
    except GalleryError as exc:
        print(exc.message, file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 1

    print(f"已生成 {bundle}")
    print("双击即可启动，Dock、访达与 ⌘Tab 里都是 Gallery 的图标。")
    print("这个包借用仓库的 .venv，不含依赖；仓库移动或 uv sync 重建后请重新生成。")
    return 0


__all__ = [
    "BUNDLE_IDENTIFIER",
    "BundleError",
    "build_bundle",
    "default_destination",
    "find_project_root",
    "main",
    "make_icns",
]

if __name__ == "__main__":
    raise SystemExit(main())
