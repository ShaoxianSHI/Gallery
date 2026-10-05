"""用 Nuitka 把 Gallery 编译成自包含的 macOS ``Gallery.app``。

**与 ``gallery.macos_app`` 的区别：** 那一个生成的是*开发用*的包 —— 启动器指向本仓库的
``.venv``，不含依赖，仓库一移动就失效。这个脚本用 Nuitka 把解释器、Python 依赖和
Qt 插件全部编译进包里，产物拷到别的 Mac 上（装了相同或更高版本的 macOS）也能直接跑。

用法::

    uv run --group build python scripts/build_app.py            # 生成 dist/Gallery.app
    uv run --group build python scripts/build_app.py --dry-run  # 只打印将要执行的命令

**为什么入口是 ``src/gallery/__main__.py``：** 它就是 ``python -m gallery`` 走的那个
入口，编译出来的行为与开发时完全一致，不需要另写一个只有一行 ``import`` 的启动脚本。

**依赖 ``.icns``：** Nuitka 的 ``--macos-app-icon`` 只收 ``.icns``，而仓库里存的是 PNG
（见 ``resources/README.md``）。这里直接复用 ``gallery.macos_app.make_icns``，
用系统自带的 ``sips`` + ``iconutil`` 转出来，不额外引入依赖或二进制资产。

**打包不包含 exiftool：** 它是运行时外部依赖（见 AGENTS.md）。包内程序在启动时由
``core.paths.augment_external_tool_path`` 把 Homebrew 等常见目录补回 ``PATH``，
所以从访达 / Dock 启动同样找得到它。

**包会裁掉一批用不到的东西**（约 14 MiB，见 ADR 0007）：Qt 默认带上的插件家族里有
好几个这个软件根本用不到的（网络、SVG、PDF 当图片读），Pillow 也带着 AVIF 与色彩管理
两个从没调用过的插件 —— 而它们各自拖进来的原生库比插件本身大一个量级。
裁剪分两步，因为 Nuitka 的依赖图算得比 ``--noinclude-dlls`` 生效早：命令行参数排掉
插件，编译完再删掉因此没人再链接的 Qt 库并重新签名。两步都由
:func:`trim_unused_libraries` 负责，删之前会核对「剩下的东西确实不再链接它们」。
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

#: 编译入口：**包目录本身**，不是里面的 ``__main__.py``。
#:
#: Nuitka 对带 ``__main__`` 的包明确要求这样给（并配 ``--python-flag=-m``），
#: 否则产物的名字会变成入口文件名（``__main__.app``），而不是包名。
_ENTRY_DIR: Final = _PROJECT_ROOT / "src" / "gallery"

#: Nuitka 按入口推导出的产物名字（= 包名），用来找回它刚生成的东西。
_NUITKA_STEM: Final = "gallery"

#: 随包分发的资源目录，在包内的落点。
#:
#: 只能是 dist 根目录下的 ``resources``，**不能**是 ``gallery/resources``：
#: Nuitka 把 ``gallery`` 整个编译成 dist 目录里的单个 ``gallery.so``，
#: ``<dist>/gallery`` 因此是个文件而不是目录，往里塞数据会直接失败。
#: ``core.paths.resources_dir`` 正是先查 ``sys.prefix`` 下的这个位置。
_RESOURCES_SOURCE: Final = _ENTRY_DIR / "resources"
_RESOURCES_TARGET: Final = "resources"

#: 排掉用不到的依赖。逐条的理由见 ADR 0007 的「裁掉了什么」。
#:
#: **两个参数家族的语义不一样，合起来才完整：**
#:
#: - ``--noinclude-qt-plugins`` 按**家族名**排（``tls`` 那一族整个不要）。注意插件家族
#:   里 ``imageformats`` 不能排 —— ``libqicns`` 要留，界面上那两枚「读系统里那一份」的
#:   图标（访达、Google 日历 PWA）靠它才能解 .icns。
#: - ``--noinclude-dlls`` 匹配的是**产物里的目标路径**，不是文件名：写在
#:   ``PySide6/qt-plugins/...`` 下面，不是 ``libqpdf``。写错不匹配、**且一条警告都没有**。
#:
#: **``--nofollow-import-to=PIL.ImageMath`` 不在名单里，是有原因的：** ``GifImagePlugin``
#: 顶层就 ``from . import ImageMath``，而 Pillow 加载插件时 ``except ImportError: pass`` ——
#: 排掉它不报错、不崩溃，只是 GIF 永远解不出来。Pillow 内部模块之间的这条隐式依赖
#: 只有实测才发现得了（拿未裁剪的包跑同一组样本做对照，少了一张缩略图才定位到）。
#: 同理 :data:`PIL.ImageCms` 只在 ``Image.py`` 的函数体里惰性引用，排在名单里是安全的。
_TRIM_OPTIONS: Final[tuple[str, ...]] = (
    # 应用完全不联网，TLS 后端一个都用不上。
    "--noinclude-qt-plugins=tls",
    # PDF 当图片读（libqpdf）、SVG 当图片读（libqsvg）、SVG 图标引擎（libqsvgicon）。
    "--noinclude-dlls=PySide6/qt-plugins/imageformats/libqpdf.*",
    "--noinclude-dlls=PySide6/qt-plugins/imageformats/libqsvg.*",
    "--noinclude-dlls=PySide6/qt-plugins/iconengines/libqsvgicon.*",
    # 平台插件只留 libqcocoa：offscreen / minimal 是给无窗口与测试环境用的，
    # 随包分发的这个是 GUI 包。
    "--noinclude-dlls=PySide6/qt-plugins/platforms/libqoffscreen.*",
    "--noinclude-dlls=PySide6/qt-plugins/platforms/libqminimal.*",
    # AVIF 不在 core.library.photos.SUPPORTED_SUFFIXES 里（那是唯一的支持格式白名单），
    # 所以 Pillow 的 AVIF 插件连同 libavif 一起是纯白带。
    "--nofollow-import-to=PIL.AvifImagePlugin",
    "--nofollow-import-to=PIL._avif",
    # 色彩管理（ImageCms / littlecms）从没被调用过。
    "--nofollow-import-to=PIL.ImageCms",
    "--nofollow-import-to=PIL._imagingcms",
)

#: 上面那些插件排掉之后，就**没有任何东西再链接**的 Qt 库，必须跟着一起删。
#:
#: 这三个是「只被那几个插件拖进来」的：QtCore / QtGui / QtWidgets 是应用自己在用的，
#: ``QtDBus`` 则是 ``QtGui`` 在 macOS 上的 ``LC_LOAD_DYLIB`` 硬依赖（不是 dlopen，
#: 删了 ``QtGui`` 就起不来），所以它留着不动。
#:
#: **为什么不能靠命令行参数删：** Nuitka 在 ``--noinclude-dlls`` 生效之前就把依赖图算完了，
#: 于是插件文件本身会被排掉，它拖进来的 Qt 库却仍然留在产物里（实测：``libqpdf``
#: 已经标了 ``ignored="yes"``，``QtPdf`` 还标着 ``ignored="no"``，理由仍是
#: 「Used by … libqpdf.dylib」）。所以这一步只能编译完再做。
_ORPHANED_QT_LIBS: Final[tuple[str, ...]] = ("QtPdf", "QtSvg", "QtNetwork")

#: 重新签名用的身份。Nuitka 默认就是 ad-hoc 签名（构建命令里没给证书），
#: 所以这里同样用 ``-``，保持与 Nuitka 签出来的一致。
#:
#: **将来若加了真证书，这里要跟着改成同一个身份** —— 用 ad-hoc 重新签会把
#: 前一步的开发者签名降级掉。
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
        # 两者同时给会被 Nuitka 判为互相矛盾并直接报错退出。
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
        # pyside6 插件负责 Qt 插件（图像格式、平台插件等）的收集；显式写出来，
        # 免得以后插件改名了只剩一句不好查的警告。
        "--enable-plugin=pyside6",
        f"--include-package={_NUITKA_STEM}",
        f"--include-data-dir={_RESOURCES_SOURCE}={_RESOURCES_TARGET}",
        # ---- 裁掉用不到的依赖 ----
        # 注释挂在 _TRIM_OPTIONS 上；它只能排掉「文件」，排不掉这些文件拖进来的 Qt 库，
        # 后半截由 trim_unused_libraries() 在编译完之后做。
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

    ``.app`` 的**文件夹名**不影响签名与启动：系统的身份认定读的是包内
    ``Info.plist`` 的 ``CFBundleIdentifier``，可执行文件名读的是 ``CFBundleExecutable``。
    所以改外层文件夹名是安全的，访达里显示的名字也仍由 ``CFBundleName`` 决定。
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

    **只剩文件名这一步很要紧：** 依赖方写的可能是 Nuitka 改写过的
    ``@executable_path/QtGui``，也可能是原封不动的
    ``@rpath/QtGui.framework/Versions/A/QtGui`` —— 两者指的是同一个库，
    而只有剥掉路径之后才比得出来。
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
    # 每个 Qt 库都会「链接着」自己，闸门恒真。
    names.discard(binary.name)
    return names


def _rejects_removal(bundle: Path, removing: tuple[Path, ...]) -> Path | None:
    """找出包内**留下来之后仍然链接着**待删 Qt 库的那个文件；没有就返回 ``None``。

    这是删之前的一道闸：那三个库是「只被已排除的插件拖进来」这个判断成立才敢删的，
    而这个判断的依据（依赖链）会随 Qt 版本变。删错了的症状是**运行期**才炸 ——
    ``dyld`` 找不到库，整个包起不来，而 ``codesign --verify`` 是绿的。

    **待删的那几个文件本身要跳过：** 它们互相链接着（``QtPdf`` 链 ``QtNetwork``），
    一起走掉，谁也不欠谁。
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

    必须在编译之后做：Nuitka 在 ``--noinclude-dlls`` 生效之前就把依赖图算完了，
    排掉插件并不会连带排掉它的依赖。详见 :data:`_ORPHANED_QT_LIBS`。

    删完要重新签名：Nuitka 签好的包是「密封」的，动了里面的文件就失效，
    而一个签名失效的包在访达里会被当成损坏的应用。

    :returns: 实际删掉的库名；一个都没删（插件压根没进包，裁剪自动生效了）时是空元组。
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
    """执行打包，返回产物路径与用到的命令。"""
    if sys.platform != "darwin":
        raise BuildError(
            f"Nuitka 的应用包只在 macOS 上生成，当前系统是 {sys.platform}。",
        )

    _check_destination(destination)

    icns = _prepare_icns(_PROJECT_ROOT / BUILD_DIR_NAME / _ICNS_RELPATH)
    output_dir = destination.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(icns, output_dir)

    if dry_run:
        return BuildResult(bundle=destination, command=tuple(command))

    completed = subprocess.run(command, cwd=_PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        # Nuitka 的日志直接流向终端（编译进度要看得见），所以这里只补一句指路的话，
        # 不重复它的内容 —— 重复一遍反而容易让人以为是两处独立的错误。
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
        help=f"生成位置（默认 <项目目录>/{DIST_DIR_NAME}/{APP_NAME}.app）",
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
    "trim_unused_libraries",
]

if __name__ == "__main__":
    raise SystemExit(main())
