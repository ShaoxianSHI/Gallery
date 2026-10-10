# ADR 0007：用 Nuitka 编译出自包含的 Gallery.app

- 状态：已接受（2026-10-06 追加「裁掉用不到的依赖」一节）
- 日期：2026-10-06
- 相关代码：`scripts/build_app.py`、`src/gallery/core/paths.py`、`src/gallery/ui/app.py`

## 背景

`gallery.macos_app` 生成的 `dist/Gallery.app` 只解决了一半问题：Dock、访达、`⌘Tab` 里的图标是对的
（那来自包内 `Info.plist` 的 `CFBundleIconFile`），但**它不含任何 Python 依赖** —— 启动器 `exec`
的是本仓库 `.venv` 里的解释器，仓库一移动、`uv sync` 重建一次虚拟环境它就起不来；想发给朋友还得先
让他装好 uv、clone 仓库、装依赖。要做到「拷过去就能跑」，就得把解释器本身和全部依赖一起打进去 ——
这属于影响面很大的工程决策（构建链路、产物大小、启动方式），先记一条。

## 决策

**用 Nuitka 编译，而不是拼一个自制的包结构。** 产物是 standalone 的 `.app`：

```bash
uv sync --group build
uv run --group build python scripts/build_app.py
```

约 110 MiB，编译几分钟；`macos_app` 的开发包仍然保留（一秒生成，用于日常本机调试）。编译还带来
两个必须处理的问题，都属于「源码原本没考虑被打包」。

### 1. `resources_dir()` 不能只靠 `__file__`

Nuitka 把 `gallery` 整个编译成 dist 目录里的单个扩展模块，模块的 `__file__` 因此指向一条**并不存在**
的虚拟路径。原来那句 `Path(__file__).resolve().parents[1] / "resources"` 会算出一个查不到图标的目录，
窗口图标于是**静默**变成空白。改为**先查 `sys.prefix`**（standalone 运行时它就是 dist 目录，数据文件
正落在那下面），查不到再回退到 `__file__` —— 源码树与虚拟环境里 `sys.prefix` 下没有这份资源，自然走
回退分支。Nuitka 生成的 macOS 包里 `sys.prefix` 是 `<包>/Contents/MacOS`，与数据文件落点一致。

### 2. exiftool 的 `PATH` 必须由进程自己补

从访达 / Dock 启动的进程只拿到 `/usr/bin:/bin:/usr/sbin:/sbin`。原先是靠 `macos_app` 的 shell 启动器
补 `PATH` 的，而编译出来的包**没有那个启动器**，于是 exiftool 找不到、元数据读不全，一路降级成只读
模式。修法是把这个逻辑收进 `core.paths.augment_external_tool_path()`，在 `ui/app.py` 的 `run()` 里、建
`QApplication` 之前调用一次：两条打包路径共用同一份逻辑。

### 3. 裁掉用不到的依赖（117 → 103 MiB）

包里最大的几项**是被拖进来的，不是用到的** —— 源码只 import `QtCore`/`QtGui`/`QtWidgets`，可 Nuitka 的
PySide6 插件默认把整个「sensible」插件家族都带上：

| 裁掉的 | 大小 | 只因为 |
|---|---|---|
| `QtPdf` | 7.0 MiB | `imageformats/libqpdf.dylib`（把 PDF 当图片读） |
| `libavif` + `PIL/_avif.so` | 3.0 MiB | Pillow 的 AVIF 插件，而 `SUPPORTED_SUFFIXES` 里没有 AVIF |
| `QtNetwork` | 1.6 MiB | `tls/*` 三个插件，而这个应用不联网 |
| `QtSvg` | 560 KiB | `libqsvg.dylib` + `libqsvgicon.dylib` |
| `liblcms2` + `PIL/_imagingcms.so` | 600 KiB | `PIL.ImageCms`（色彩管理，从未调用） |
| `tls/*`、`libqoffscreen`、`libqminimal`、`libqpdf`、`libqsvg`、`libqsvgicon` | 1.2 MiB | 见上 |

**`QtDBus`（680 KiB）动不了**：macOS 上 `QtGui` 用 `LC_LOAD_DYLIB` 硬链它，不是 dlopen，删了 `QtGui`
就起不来。

#### 两步才能裁干净

**第一步：命令行参数排掉文件本身。** `--noinclude-qt-plugins` 按**家族名**排（`tls`）；
`--noinclude-dlls` 匹配的是**产物里的目标路径**而不是文件名，要写
`PySide6/qt-plugins/imageformats/libqpdf.*`，写错**不匹配、且一条警告都没有**；
`--nofollow-import-to=PIL.<模块>` 排 Pillow 模块。

**第二步：编译完再删那几个 Qt 库。** Nuitka 的依赖图算得比 `--noinclude-dlls` 生效早，所以排掉插件
**不会**连带排掉它拖进来的库 —— `--report` 里 `libqpdf.dylib` 已标 `ignored="yes"`，`QtPdf` 却还标着
`ignored="no"`，理由仍是 `Used by 'PySide6/qt-plugins/imageformats/libqpdf.dylib'`。因此
`trim_unused_libraries()` 在 `_finalize_bundle()` 之后把 `QtPdf`/`QtSvg`/`QtNetwork` 删掉，**再重新
`codesign`**（Nuitka 签好的包是密封的，动了就失效）。

#### 三个坑

1. **别想把 `libqicns` 单独补回来。** `--include-data-files` 是**数据文件**，原样拷贝，install name
   仍是 `@rpath/QtGui.framework/Versions/A/QtGui`，而包里根本没有 `QtGui.framework`；
   `codesign --verify --deep --strict` 照样报「valid on disk」，只有真跑起来 `QT_DEBUG_PLUGINS=1` 才看得见
   `dlopen` 失败。所以 `imageformats` 家族整个留着（2.1 MiB）从而不碰它的安装路径 —— 界面那两枚
   「读系统里那一份」的图标（访达、Google 日历 PWA）靠 `libqicns` 解 .icns。
2. **`PIL.ImageMath` 不能排。** `GifImagePlugin` 顶层就 `from . import ImageMath`，而 Pillow 加载插件
   时 `except ImportError: pass` —— 排掉它**不报错、不崩溃，只是 GIF 永远解不出来**。Pillow 内部模块之间
   的隐式依赖只有实测发现得了（判据是拿未裁剪的包跑同一组样本做对照）；`PIL.ImageCms` 只在 `Image.py`
   的**函数体里**惰性引用，排在名单里是安全的。
3. **前置条件要自己验过一次。** 删之前逐个读包内 Mach-O 的依赖，确认没有**留下来的**文件还链着那三个
   库；待删的那几个本身要跳过（`QtPdf` 链 `QtNetwork`，一起走掉，谁也不欠谁），而 `otool -L` 会把
   `LC_ID_DYLIB` 也列成一条依赖 —— 不去掉自身名字的话每个库都「链接着自己」，前置条件恒真。这道前置
   条件必须存在：判据会随 Qt 版本变，而删错的症状是**运行期** `dyld` 找不到库、整个包起不来，签名却绿。

## 后果

- **好处**：产物可分发。`codesign` 验证通过，ad-hoc 签名下在本机双击即可运行；换自己的开发者证书签名
  并公证后即可对外分发。
- **代价**：构建链路多了一个重量级工具（编译期依赖 clang、会下载 ccache），因此 `nuitka` 单独放在
  `[dependency-groups] build`，不拖慢日常的 `uv sync`；日常本机调试仍用 `macos_app` 的开发包（秒级）。
- exiftool 仍然**不进包**，保持运行时外部依赖，缺失时降级为只读模式。
- 裁剪清单是**写在代码里的显式决定**而不是自动推导，每一条都要能回答「谁在用、为什么不用」；判据会随
  Qt 版本变，`trim_unused_libraries()` 的前置条件是为此留的。

### 还没动的最大一块：`libx265` 9.5 MiB

`pillow-heif` 里的 libheif 带的是 **HEVC 编码器**，而这个软件只解码。它是 libheif 的
`LC_LOAD_DYLIB` 硬依赖，删文件 libheif 就起不来，**唯一干净的去法是不用 pillow-heif**，改走 Qt 的
`libqmacheif.dylib`（94 KiB，macOS ImageIO）—— 那是改 `core/media/decode.py` 的解码后端，要单独一条
ADR。HEIC 在当前裁剪包里解码正常，动它之前得单独测色彩与 EXIF 方向。

## 被否决的方案

- **PyInstaller**：`.app` 打包体验不如 Nuitka，PySide6 需要额外的 hook 脚本，首屏启动也更慢。
- **onefile（单文件）**：每次启动都要解压到临时目录，GUI 应用的启动明显更慢，且 macOS 下容易与 Dock
  图标归属冲突。
- **把 exiftool 也打进包里**：会让产物再大几十 MiB，且与「运行时外部依赖、缺失则降级」既有约定冲突。
- **在编译版里也保留 shell 启动器补 `PATH`**：等于把同一个坑留在两处；收进 `core` 之后只有一处。