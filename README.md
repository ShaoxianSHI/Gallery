# Gallery

一个本地优先的 macOS 相册软件：**照片留在你自己的文件夹里，软件只是它的一个视图。**

指定一个本地文件夹作为「库」，Gallery 读取它、而不接管它。照片始终是普通文件，用访达、
Photoshop 或任何别的工具都能打开；即使卸载 Gallery，照片与元数据依然完整可读。

- **实时浏览**：打开文件夹即可看，磁盘上的增删改立刻反映到界面。
- **元数据**：读取 EXIF / IPTC / XMP；拍摄时间与位置可以改并写回照片文件本身。
- **不碰你的照片**：扫描、索引、预览一律只读。会动文件的只有你亲手触发的几条命令，
  每一次都记账、都能 `⌘Z` 撤销。

## 环境要求

- macOS 13 或更高版本
- [uv](https://docs.astral.sh/uv/) —— 负责 Python 版本与依赖，不必自己装 Python
- [exiftool](https://exiftool.org/) —— 可选；读 IPTC / XMP 与写回元数据时需要它

```bash
brew install uv exiftool
```

## 从源码运行

```bash
git clone https://github.com/ShaoxianSHI/Gallery.git
cd Gallery
uv sync                     # 创建虚拟环境并装好依赖（含 Python 3.12 本身）
uv run gallery              # 启动
```

也可以直接打开指定的文件夹，或者只看版本号：

```bash
uv run gallery ~/照片
uv run gallery --version
```

## 打包成 .app

```bash
uv sync --group build
uv run --group build python scripts/build_app.py                    # 产物在 dist/Gallery.app
uv run --group build python scripts/build_app.py ~/Applications     # 直接放进「应用程序」
uv run --group build python scripts/build_app.py --dry-run          # 只看将要执行的命令
```

自包含版把解释器、Python 依赖与 Qt 插件一起编译进包里（约 110 MiB，首次编译要几分钟），
拷到别的 Mac 上双击即可运行。另有开发版，一秒生成、启动器指向本仓库的 `.venv`：

```bash
uv run python -m gallery.macos_app
```

两种包都能双击启动，Dock 与访达里显示的是同一枚图标。包默认用 ad-hoc 签名（`-`），
只在本机双击可用；要分发给别人，把 `scripts/build_app.py` 里的签名身份换成自己的开发者证书，
打包后自行签名与公证。

从访达 / Dock 启动的进程默认只看得到系统那几个目录，Homebrew 装的 exiftool 不在其中
（终端里有，因为 shell 会读你的配置文件）。Gallery 启动时会把常见的安装目录补回 `PATH`。

## 基本用法

启动后用 `⌘O` 选一个文件夹当库。常用操作：

| 操作 | 快捷键 |
|------|--------|
| 打开大图 / 退回网格 | 双击、`回车` / `Esc` |
| 上一张 / 下一张 | `←` / `→` |
| 适应窗口 / 实际大小 / 缩放 | `⌘0` / `⌘1` / `⌘+`、`⌘-` |
| 搜索（文件名、相册、元数据） | `⌘F` |
| 在搜索与「筛选当前视图」之间切换 | 搜索框右边的漏斗按钮，或「文件 → 筛选当前视图」 |
| 重命名（照片或相册） | 网格下再点一次已选中的缩略图，或 `⌘R` |
| 移到废纸篓 | `⌘⌫` |
| 编辑拍摄时间与位置 | `⇧⌘I` |
| 撤销上一次写操作 | `⌘Z` |
| 在默认应用里打开 / 在访达中显示 | `⇧⏎` / `⌘⏎` |
| 显示元数据面板 | `⌘I` |
| 关闭窗口（应用还在）/ 退出 | `⌘W` / `⌘Q` |

- **三种浏览方式**（「视图 → 浏览方式」）：**全部照片**把整库平铺在一起；**相册**是
  一个子文件夹一个相册，双击进入；**日历**按拍摄时间分年 / 月 / 周三层（`Y` / `M` / `W`
  切层，`←` / `→` 翻页，`T` 回到今天）。
- **搜索与筛选**用的是同一套条件语法（`#相机 Canon`、`#ISO >800`、`#拍摄时间 2024-05`，
  不带 `#` 就是按文件名），区别只在**管到哪儿**：
  - **搜索**（默认）横跨整个库，条件一提交整层就被结果接管；
  - **筛选**（点搜索框右边那枚漏斗按钮切过去）只把**当前这一层**收窄，视图本身不动 ——
    在月视图里加条件仍然是月视图，切到全部照片就筛整库，换相册就筛那个相册，条件一路跟着走。
  条件会作为胶囊摆在面包屑那一行的右端，点文字是改、点 `✕` 是撤掉，`Esc` 逐条撤销。
  筛选生效时 `⌘V` 与 `⇧⌘N` 会暂时让路（这一层显示的不是全部内容，粘进去的照片可能一条都
  不出现）；撤掉条件就恢复。
- **分组与排序**在「视图」菜单里：可按拍摄时间、相机、镜头、国家 / 城市、关键词、
  文件格式等字段把照片分成组卡片，也可按这些字段排序。
- **右键菜单**里的命令按落点（照片 / 相册 / 分组卡片 / 空白处）给。会动文件的只有
  **重命名、移到废纸篓、粘贴、移动到相册、新建相册**这五条，全部只在你亲手触发时执行；
  前三条与「移动到相册」都能 `⌘Z` 撤销。
- 磁盘上的变化会实时反映到网格：在访达里拖进一批照片、改名、删除，界面自己跟上。

## `.gallery/` 目录

首次打开一个文件夹时，Gallery 会在库根目录下建一个 `.gallery/`，存放自己的运行时数据：

```
<库根目录>/
├── 你的照片文件夹/          # Gallery 不做任何整理
└── .gallery/
    ├── config.toml         # 库级配置（相册封面、界面偏好）
    ├── index.db            # 索引缓存，删掉可重建
    └── journal/            # 写操作日志，用于回溯与撤销
```

除 `config.toml` 外都是缓存：整个删掉，重新扫描就能恢复等价状态。缩略图不放在库里，
而是按库路径摘要放在 `~/Library/Application Support/Gallery/cache/thumbs/` —— 照片文件夹
常常挂在云盘上，几万个小文件跟着同步是白白折腾。

## 支持的照片格式

- **读写元数据**：JPEG、HEIC/HEIF、TIFF、PNG
- **仅预览**：GIF、WebP、BMP
- **尚不支持**：常见 RAW（CR2/CR3、NEF、ARW、DNG）

## 已知限制

- 只有**拍摄时间**（含时区）与**位置**（坐标、城市 / 省州 / 国家）可以写回；相机、镜头、
  光圈 / 快门 / ISO 这些拍摄事实一律只读。
- 没装 exiftool 时退回 Pillow 读基础 EXIF，IPTC / XMP 相关的字段不可用，「编辑元数据」也不出现。
- 地点按坐标反查用的是随包分发的离线城市库（约 380 KB），**不联网、不上传坐标**。

## 开发

```bash
uv run pytest          # 测试（默认并行）
uv run ruff check .    # lint
uv run ruff format .   # 格式化
uv run mypy            # 类型检查（只查 core/）
```

设计决策记录在 [`docs/adr/`](docs/adr/README.md)。

## 第三方数据

`src/gallery/resources/places.tsv.gz` 由
[dr5hn/countries-states-cities-database](https://github.com/dr5hn/countries-states-cities-database)
生成（ODbL-1.0）。应用图标为本项目自己的图形，仓库里不含第三方商标素材。

## 许可证

[MIT](LICENSE) —— 可以自由使用、修改、再分发与商用，只需保留版权声明与许可声明。
软件按「原样」提供，不附带任何担保。
