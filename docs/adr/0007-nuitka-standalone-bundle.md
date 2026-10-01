# ADR 0007：用 Nuitka 编译出自包含的 Gallery.app

- 状态：已接受
- 日期：2026-10-06
- 相关代码：`scripts/build_app.py`、`src/gallery/core/paths.py`、`src/gallery/ui/app.py`

## 背景

`gallery.macos_app` 生成的 `dist/Gallery.app` 只解决了一半问题：Dock、访达、`⌘Tab`
里的图标是对的（那来自包内 `Info.plist` 的 `CFBundleIconFile`），但**它不含任何
Python 依赖** —— 启动器 `exec` 的是本仓库 `.venv` 里的解释器。于是这个包只在开发机上
有效：仓库一移动、`uv sync` 重建一次虚拟环境，它就起不来；想发给朋友，还得先让他装好
uv、clone 仓库、装依赖。

要做到「拷过去就能跑」，就得把解释器本身和全部依赖一起打进去。这属于影响面很大的
工程决策（构建链路、产物大小、启动方式），先记一条。

## 决策

**用 Nuitka 编译，而不是拼一个自制的包结构。** 产物是 standalone 的 `.app`：

```bash
uv sync --group build
uv run --group build python scripts/build_app.py
```

约 110 MiB，编译几分钟；`macos_app` 的开发包仍然保留（一秒生成，用于日常本机调试）。

编译带来两个必须处理的问题，都属于「源码原本没考虑被打包」：

### 1. `resources_dir()` 不能只靠 `__file__`

Nuitka 把 `gallery` 整个编译成 dist 目录里的单个扩展模块，模块的 `__file__` 因此指向一条
**并不存在**的虚拟路径。原来那句 `Path(__file__).resolve().parents[1] / "resources"`
会算出一个查不到图标的目录，窗口图标于是**静默**变成空白 —— 不报错、不崩溃，只是图标没了。

改为**先查 `sys.prefix`**（standalone 运行时它就是 dist 目录，数据文件正落在那下面），
查不到再回退到 `__file__`。源码树与虚拟环境里 `sys.prefix` 下没有这份资源，自然走回退分支。

> 顺带确认过：Nuitka 生成的 macOS 包里 `sys.prefix` 是 `<包>/Contents/MacOS`，
> 与数据文件的落点一致。

### 2. exiftool 的 `PATH` 必须由进程自己补

从访达 / Dock 启动的进程只拿到 `/usr/bin:/bin:/usr/sbin:/sbin`。原先是靠 `macos_app`
的 shell 启动器补 `PATH` 的，而编译出来的包**没有那个启动器**，于是 exiftool 找不到，
元数据读不全，一路降级成只读模式 —— 表现就是「明明装了 exiftool，Gallery 却说没有」。

修法是把这个逻辑收进 `core.paths.augment_external_tool_path()`，在 `ui/app.py` 的
`run()` 里、建 `QApplication` 之前调用一次。这样两条打包路径共用同一份逻辑，源码运行
（终端里有 PATH）和 `.app` 运行（没有）都不再依赖谁来补环境。

## 后果

- **好处**：产物可分发。`codesign` 验证通过，ad-hoc 签名下在本机双击即可运行；
  换自己的开发者证书签名并公证后即可对外分发。
- **代价**：构建链路多了一个重量级工具（编译期依赖 clang、会下载 ccache），
  因此 `nuitka` 单独放在 `[dependency-groups] build`，不拖慢日常的 `uv sync`。
- **代价**：日常本机调试仍用 `macos_app` 的开发包（秒级），编译版留给「要拿去跑」的场合。
- **后果**：exiftool 仍然**不进包**。它保持运行时外部依赖，缺失时降级为只读模式。

## 被否决的方案

- **PyInstaller**：`.app` 打包体验不如 Nuitka，PySide6 需要额外的 hook 脚本，
  首屏启动也更慢。
- **onefile（单文件）**：每次启动都要解压到临时目录，GUI 应用的启动明显更慢，
  且 macOS 下容易与 Dock 图标归属冲突。
- **把 exiftool 也打进包里**：会让产物再大几十 MiB，且与「运行时外部依赖、
  缺失则降级」既有约定冲突。
- **在编译版里也保留 shell 启动器补 `PATH`**：等于把同一个坑留在两处；
  收进 `core` 之后只有一处。
