"""用 Nuitka 把 Gallery 编译成自包含的 macOS ``Gallery.app``（``gallery.macos_app``
生成的那个不含依赖，只在开发机上跑）。

用法::

    uv run --group build python scripts/build_app.py            # 生成 dist/Gallery.app
    uv run --group build python scripts/build_app.py ~/Applications  # 放进该文件夹
    uv run --group build python scripts/build_app.py --dry-run  # 只打印将要执行的命令

exiftool 不在包里：它是运行时外部依赖，程序启动时由
``core.paths.augment_external_tool_path`` 把常见安装目录补回 ``PATH``。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# 允许从任意工作目录运行：把 src/ 挂到 sys.path 上，才能 import gallery.*。
_PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from gallery import __version__  # noqa: E402
from gallery.core.constants import APP_NAME  # noqa: E402
from gallery.core.errors import GalleryError  # noqa: E402
from gallery.macos_app import BUNDLE_IDENTIFIER, MINIMUM_SYSTEM_VERSION, make_icns  # noqa: E402

#: Nuitka 的中间产物目录。与 ``.gitignore`` 里的 ``build/`` 对应，不入库。
BUILD_DIR_NAME: Final = "build"

#: 最终产物目录，与 ``.gitignore`` 里的 ``dist/`` 对应。
DIST_DIR_NAME: Final = "dist"

#: 中间产物用的 ``.icns`` 放哪（相对 ``build/``）。不进最终产物，但需要真实文件。
_ICNS_RELPATH: Final = Path("icons") / f"{APP_NAME}.icns"

#: 编译入口：**包目录本身**（配 ``--python-flag=-m``）。给 ``__main__.py`` 会让产物
#: 叫 ``__main__.app`` 而不是包名。
_ENTRY_DIR: Final = _PROJECT_ROOT / "src" / "gallery"

#: Nuitka 按入口推导出的产物名字（= 包名），用来找回它刚生成的东西。
_NUITKA_STEM: Final = "gallery"

#: 随包分发的资源目录，在包内的落点。**只能**是 dist 根下的 ``resources``：Nuitka 把
#: ``gallery`` 整个编成 dist 里的单个 ``gallery.so``，``<dist>/gallery`` 是文件不是目录。
_RESOURCES_SOURCE: Final = _ENTRY_DIR / "resources"
_RESOURCES_TARGET: Final = "resources"

#: 排掉用不到的依赖（逐条理由见 ADR 0007）。三处坑：``--noinclude-qt-plugins`` 按**家族名**
#: 排，但 ``imageformats`` 不能排（``libqicns`` 要读系统里的 .icns）；``--noinclude-dlls``
#: 匹配的是**产物里的目标路径**，写错不匹配且无警告；``PIL.ImageMath`` 也不能排
#: （``GifImagePlugin`` 顶层就 import 它），而惰性引用的 ``PIL.ImageCms`` 排掉是安全的。
_TRIM_OPTIONS: Final[tuple[str, ...]] = (
    # 应用完全不联网，TLS 后端一个都用不上。
    "--noinclude-qt-plugins=tls",
    # PDF 当图片读、SVG 当图片读、SVG 图标引擎。
    "--noinclude-dlls=PySide6/qt-plugins/imageformats/libqpdf.*",
    "--noinclude-dlls=PySide6/qt-plugins/imageformats/libqsvg.*",
    "--noinclude-dlls=PySide6/qt-plugins/iconengines/libqsvgicon.*",
    # 平台插件只留 libqcocoa：offscreen / minimal 是给无窗口与测试环境用的。
    "--noinclude-dlls=PySide6/qt-plugins/platforms/libqoffscreen.*",
    "--noinclude-dlls=PySide6/qt-plugins/platforms/libqminimal.*",
    # AVIF 不在 photos.SUPPORTED_SUFFIXES（唯一的支持格式白名单）里，插件与 libavif 是白带。
    "--nofollow-import-to=PIL.AvifImagePlugin",
    "--nofollow-import-to=PIL._avif",
    # 色彩管理（littlecms）从没被调用过。
    "--nofollow-import-to=PIL.ImageCms",
    "--nofollow-import-to=PIL._imagingcms",
)

#: 上面那些插件排掉之后，就**没有任何东西再链接**的 Qt 库，必须跟着一起删（QtCore / QtGui /
#: QtWidgets 是应用自己在用的；QtDBus 是 QtGui 在 macOS 上的硬依赖）。**QtNetwork 不在其中**：
#: QtMultimedia 与它的多媒体插件都链接着它，删了视频就放不了。
#: **不能靠命令行参数删** —— Nuitka 在 ``--noinclude-dlls`` 生效之前就把依赖图算完了，
#: 这一步只能编译完再做。
_ORPHANED_QT_LIBS: Final[tuple[str, ...]] = ("QtPdf", "QtSvg")

#: 重新签名用的身份。Nuitka 默认是 ad-hoc 签名，这里跟着用 ``-``；将来若加了真证书，
#: 这里要改成同一个身份 —— 用 ad-hoc 重签会把前一步的开发者签名降级掉。
_CODESIGN_IDENTITY: Final = "-"


class BuildError(GalleryError):
    """打包失败：Nuitka 不可用、图标缺失、编译出错、目标位置被占。"""


@dataclass(frozen=True)
class BuildResult:
    """一次打包的结果。"""

    #: 生成的 ``.app`` 包。
    bundle: Path
    #: 实际执行的 Nuitka 命令（便于复现与排错）。
    command: tuple[str, ...]
    #: 裁剪阶段从包里删掉的 Qt 库（为空表示那几个库本来就没进包）。
    removed: tuple[str, ...] = ()


def resolve_bundle(destination: Path) -> Path:
    """把「放包的文件夹」补成其中的 ``Gallery.app``；以 ``.app`` 结尾的路径原样返回。

    这样 ``~/Applications`` 这种写法不必先确认目录存不存在：它永远被当成容器，
    而不是一个名字里没有扩展名的应用包。
    """
    if destination.suffix.lower() == ".app":
        return destination
    if destination.exists() and not destination.is_dir():
        return destination  # 交给 _check_destination 报「这里已经有东西」
    return destination / f"{APP_NAME}.app"


def _check_destination(bundle: Path) -> None:
    """确认目标位置可以安全地清空。

    只有「已经是一个 ``.app`` 包」才允许删：脚本会做删除，就不能靠猜。
    """
    if not bundle.exists():
        return
    if not (bundle / "Contents" / "Info.plist").is_file():
        raise BuildError(
            f"目标位置已经有东西，且不是应用包：{bundle}。换个位置，或自己先清掉它。",
            path=bundle,
        )


def _prepare_icns(destination: Path) -> Path:
    """生成（必要时重新生成）打包用的 ``.icns``。"""
    if not _RESOURCES_SOURCE.is_dir():
        raise BuildError("找不到随包分发的资源目录，图标也就无从生成。", path=_RESOURCES_SOURCE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return make_icns(_RESOURCES_SOURCE / "gallery-icon-1024.png", destination)


def build_command(icns: Path, output_dir: Path) -> list[str]:
    """拼出 Nuitka 命令行。

    拆成独立函数是为了让 ``--dry-run`` 能打印出与真正执行时**完全一致**的命令 ——
    否则 dry-run 验证不了任何东西。
    """
    return [
        sys.executable,
        "-m",
        "nuitka",
        # ---- 产物形态 ----
        # 刻意不写 --mode=standalone：--macos-create-app-bundle 本身蕴含 standalone，
        # 两者同时给会被 Nuitka 判为互相矛盾。
        "--macos-create-app-bundle",
        f"--macos-app-name={APP_NAME}",
        f"--macos-app-icon={icns}",
        f"--macos-app-version={__version__}",
        f"--macos-app-macos-min-version={MINIMUM_SYSTEM_VERSION}",
        f"--macos-signed-app-name={BUNDLE_IDENTIFIER}",
        # 图标应用：有窗口、要出现在 Dock 里，且不该弹出终端窗口。
        "--macos-app-mode=gui",
        "--macos-app-console-mode=disable",
        f"--output-dir={output_dir}",
        f"--company-name={BUNDLE_IDENTIFIER.split('.')[1]}",
        f"--product-name={APP_NAME}",
        "--file-description=本地优先的相册软件",
        # ---- 打包内容 ----
        # pyside6 插件负责 Qt 插件（图像格式、平台插件等）的收集；显式写出来免得以后插件改名了
        # 只剩一句不好查的警告。
        "--enable-plugin=pyside6",
        # Nuitka 的「常用插件」名单是 Qt5 的 mediaservice；Qt6 的多媒体后端目录叫 multimedia。
        # 不点名要，包里就只有 QtMultimedia 这个库、**一个播放后端也没有**（打开视频报找不到后端）。
        "--include-qt-plugins=multimedia",
        f"--include-package={_NUITKA_STEM}",
        f"--include-data-dir={_RESOURCES_SOURCE}={_RESOURCES_TARGET}",
        # pypinyin 的词典是包内 JSON：Nuitka 只跟 Python 代码，不会自己带上它们。
        "--include-package-data=pypinyin",
        # ---- 裁掉用不到的依赖 ----
        # 只排得掉「文件」；它们拖进来的 Qt 库由 trim_unused_libraries() 事后删。
        *_TRIM_OPTIONS,
        # ---- 构建过程 ----
        # 入口给的是包目录，必须配上 -m 才是「python -m gallery」的语义。
        "--python-flag=-m",
        # 首次运行会下载 ccache 等辅助工具；打包是显式操作，直接替人确认。
        "--assume-yes-for-downloads",
        "--remove-output",
        str(_ENTRY_DIR),
    ]


def _finalize_bundle(output_dir: Path, destination: Path) -> Path:
    """把 Nuitka 按包名生成的 ``gallery.app`` 落成 ``Gallery.app``，并清掉中间目录。

    ``.app`` 的**文件夹名**不影响签名与启动：身份认定读包内 ``Info.plist`` 的
    ``CFBundleIdentifier``，可执行文件名读 ``CFBundleExecutable``。
    """
    produced = output_dir / f"{_NUITKA_STEM}.app"
    if not (produced / "Contents" / "Info.plist").is_file():
        raise BuildError("编译结束了，但没有生成预期的应用包。", path=produced)
    if produced != destination:
        shutil.move(str(produced), str(destination))

    for suffix in (".build", ".dist"):
        leftover = output_dir / f"{_NUITKA_STEM}{suffix}"
        # 只删名字精确对得上、且确实落在输出目录里的东西。
        if leftover.is_dir() and leftover.parent == output_dir:
            shutil.rmtree(leftover)
    return destination


def _linked_libraries(binary: Path) -> set[str]:
    """读一个 Mach-O 文件依赖的库名（只要文件名，不含路径与版本后缀）。

    剥掉路径很要紧：依赖方写的可能是 Nuitka 改写过的 ``@executable_path/QtGui``，
    也可能是原封不动的 ``@rpath/QtGui.framework/Versions/A/QtGui`` —— 两者是同一个库。
    """
    completed = subprocess.run(
        ["otool", "-L", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        # 不是 Mach-O（数据文件、签名、Info.plist 之类），不是错误。
        return set()
    names: set[str] = set()
    for line in completed.stdout.splitlines()[1:]:
        name = line.strip().split(" ")[0].strip()
        if name:
            names.add(name.rsplit("/", 1)[-1])
    # `otool` 把 LC_ID_DYLIB 也列成一条依赖，而库从不依赖自己 —— 不去掉它的话
    # 每个 Qt 库都会「链接着」自己，这条判断恒真。
    names.discard(binary.name)
    return names


def _rejects_removal(bundle: Path, removing: tuple[Path, ...]) -> Path | None:
    """找出包内**留下来之后仍然链接着**待删 Qt 库的那个文件；没有就返回 ``None``。

    这是删之前的一道检查：那三个库「只被已排除的插件拖进来」的判断会随 Qt 版本失效，
    而删错只会在**运行期**炸（``dyld`` 找不到库，``codesign --verify`` 却绿）。
    待删的那几个文件本身要跳过：它们互相链接着，一起走掉，谁也不欠谁。
    """
    wanted = {path.name for path in removing}
    doomed = set(removing)
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path in doomed:
            continue
        if wanted & _linked_libraries(path):
            return path
    return None


def trim_unused_libraries(bundle: Path) -> tuple[str, ...]:
    """删掉「只被已排除的 Qt 插件拖进来」的那几个 Qt 库，然后重新签名。

    必须在编译之后做（见 :data:`_ORPHANED_QT_LIBS`）；删完必须重新签名：动了包里的文件
    签名就失效，而签名失效的包在访达里会被当成损坏的应用。

    :returns: 实际删掉的库名；一个都没删（插件压根没进包）时是空元组。
    :raises BuildError: 包里还有东西链接着待删的库（删了会起不来），或重新签名失败。
    """
    macos_dir = bundle / "Contents" / "MacOS"
    removing = tuple(macos_dir / name for name in _ORPHANED_QT_LIBS if (macos_dir / name).is_file())
    if not removing:
        # 插件没进包 → 那几个库也没被拖进来，裁剪「自动生效」了，什么都不用做。
        return ()

    blocker = _rejects_removal(bundle, removing)
    if blocker is not None:
        raise BuildError(
            f"包内 {blocker.name} 仍然链接着 {'、'.join(path.name for path in removing)}，"
            "不能删。删了它，运行时会因为找不到库而起不来。",
            path=blocker,
            detail="多半是 Qt 升级后这些库有了新的依赖方。先查清楚，再决定是留下还是换裁剪清单。",
        )

    for path in removing:
        path.unlink()
    present = tuple(path.name for path in removing)

    completed = subprocess.run(
        ["codesign", "--force", "--sign", _CODESIGN_IDENTITY, str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BuildError(
            "裁剪之后重新签名失败，产物在访达里会被当成损坏的应用。",
            path=bundle,
            detail=completed.stderr.strip(),
        )

    verified = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    if verified.returncode != 0:
        raise BuildError(
            "重新签名之后校验没通过。",
            path=bundle,
            detail=verified.stderr.strip(),
        )
    return present


def build(destination: Path, *, dry_run: bool = False) -> BuildResult:
    """执行打包，返回产物路径与用到的命令。``destination`` 可以是包路径或放包的文件夹。"""
    if sys.platform != "darwin":
        raise BuildError(
            f"Nuitka 的应用包只在 macOS 上生成，当前系统是 {sys.platform}。",
        )

    destination = resolve_bundle(Path(destination))
    _check_destination(destination)

    icns = _prepare_icns(_PROJECT_ROOT / BUILD_DIR_NAME / _ICNS_RELPATH)
    output_dir = destination.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(icns, output_dir)

    if dry_run:
        return BuildResult(bundle=destination, command=tuple(command))

    completed = subprocess.run(command, cwd=_PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        # Nuitka 的日志直接流向终端（编译进度要看得见），这里只补一句指路的话。
        raise BuildError(
            f"Nuitka 编译失败，退出码 {completed.returncode}。",
            detail="真正的原因在上面那行以 FATAL: 或 ERROR: 开头的日志里。",
        )

    _finalize_bundle(output_dir, destination)
    removed = trim_unused_libraries(destination)
    return BuildResult(bundle=destination, command=tuple(command), removed=removed)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="build_app",
        description="用 Nuitka 把 Gallery 编译成自包含的 macOS Gallery.app。",
    )
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help=(
            "生成位置：.app 包路径，或放包的文件夹"
            f"（默认 <项目目录>/{DIST_DIR_NAME}/{APP_NAME}.app）"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的 Nuitka 命令，不真的编译",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    destination = args.destination or _PROJECT_ROOT / DIST_DIR_NAME / f"{APP_NAME}.app"

    try:
        result = build(destination, dry_run=args.dry_run)
    except GalleryError as exc:
        print(exc.message, file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 1

    if args.dry_run:
        print(" ".join(result.command))
        return 0

    size = sum(f.stat().st_size for f in result.bundle.rglob("*") if f.is_file())
    print(f"已生成 {result.bundle}（{size / 1024**2:.0f} MiB）")
    print("解释器、Python 依赖与 Qt 插件都已打进包里，可以直接拷到别的 Mac 上运行。")
    if result.removed:
        print(f"已裁掉用不到的 Qt 库并重新签名：{'、'.join(result.removed)}。")
    print("exiftool 不在包里：装好它的那台 Mac 上启动时会自动找到（启动时补 PATH）。")
    return 0


__all__ = [
    "BuildError",
    "BuildResult",
    "build",
    "build_command",
    "main",
    "resolve_bundle",
    "trim_unused_libraries",
]

if __name__ == "__main__":
    raise SystemExit(main())
