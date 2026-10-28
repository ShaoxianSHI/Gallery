# ADR 0091：视频大图页——在应用里播放，而不是交给默认应用

- 状态：已接受
- 日期：2026-10-24
- 修改：[0090](0090-videos-in-the-library.md) 决策 4 里「视频没有大图页、双击交给默认应用」
- 相关 ADR：0001（解码分层）、0021（大图页的当前项）、0025（标题栏改名）、0033（日历）、
  0050（`_sync_ui`）、0063（影片网站）、0067（大图页的图片应用）
- 相关代码：`src/gallery/ui/viewer/video.py`、`src/gallery/ui/main_window.py`、
  `src/gallery/ui/window/viewer_flow.py`、`src/gallery/ui/window/slideshow_flow.py`、
  `src/gallery/ui/window/navigation_flow.py`、`src/gallery/ui/window/library_flow.py`

## 背景

0090 让视频进了网格（第一帧缩略图、可改名、可记入事件），但双击只是交给系统默认应用。
用户随后问：Qt 能不能在应用里放？先量了一遍再决定：

| 素材 | 实测 |
|---|---|
| 320×240 H.264 30fps | 30fps，首帧 80 ms，3 秒耗 CPU 0.23 s，RSS 108 MB |
| 3840×2160 HEVC 240fps | 239fps（VideoToolbox 硬解），2.85 秒耗 CPU 0.58 s，RSS 375 MB |
| 带音轨的 mp4 | `hasAudio=True`，音量可设，正常出声 |
| 定位 / 暂停 / 循环 / 中途换片 / 坏文件 | 都正常；坏文件给 `FormatError`，不崩 |
| `QT_QPA_PLATFORM=offscreen` | 照样出帧，测试能覆盖 |

依赖上不新增任何包：`pyside6` 已经带着 `QtMultimedia` 与 `QtMultimediaWidgets`。代价是
打包体积多约 65 MB（`libavcodec` 27 MB、`libavformat` 4.6 MB 等）。

## 决策

### 1. `QMediaPlayer` + `QAudioOutput` + `QGraphicsVideoItem`，单独一个 `VideoView`

`QGraphicsVideoItem` 是 **`QGraphicsItem`**，所以视频页与照片页共用同一种容器：
场景、底色、适配窗口都由 `QGraphicsView` 给，不需要原生视频窗口。

**不改 `DetailView`**：它现在是纯 pixmap 的 —— 适配窗口、100%、缩放、双击复位、⌥ 点开网站、
右键菜单、`has_image`，一共 13 处直接读 `_pixmap` 的宽高。往里面塞一个 video item 要先把
「内容尺寸」抽成一处，等于动照片那条最常用的路径。新写一个 `VideoView` 与它在
`QStackedWidget` 里二选一：照片路径一行不改，视频的播放器生命周期也收在一个类里。

**层级栈不动**：视频页仍然是 `PhotoFrame`，`PageRole.DETAIL` 也不变 —— 页面是栈的函数这一条
（ADR 0041）因此照旧成立，只是这一页的内容按后缀二选一。

### 2. 打开即播放，控件只有进度条

- 打开视频页就 `play()`（有音轨就出声；双击本来就是明确意图）。
- 首帧到达前盖着网格里那张第一帧缩略图，直接黑屏会让人以为点错了文件。
- 点画面或空格：播放 / 暂停。底部一条半透明进度条，拖动定位，右侧 `m:ss / m:ss`。
- `←` / `→` 仍是「上一个 / 下一个」（与 README 一致）；`Esc` / `⏎` 回网格；
  `⇧⏎` 与右键菜单里的「在默认应用中打开」仍然保留；`⌥` 点画面打开所属影片的网站。
- 播完回到第一帧（Qt 的 `QGraphicsVideoItem` 在 `EndOfMedia` 之后不再保留画面，用首帧缩略图盖回去），再按播放从头开始。

### 3. 视频页上没有的东西

- **缩放**：视频固定适配窗口，`⌘+` / `⌘-` / `⌘0` 在视频页置灰。
- **元数据面板**：EXIF 对视频没有意义，进入视频页先收起，翻回照片时按库级偏好放回去。
- **「预览 / ImageGlass」**：它们打不开视频，`_viewer_ready` 对视频返回 `False`，
  下拉框只剩访达 / QSpace。
- **「复制元数据」**：没有可读的 EXIF 页，置灰。

### 4. 生命周期只有一个出口

离开照片帧（`_leave_frame`，覆盖回网格、回日历、日历单格那一支）、离开大图页
（`leave_detail_page`）、切库 / 关窗（`_teardown_library`）都调 `VideoView.stop()`：
停播 + `setSource(QUrl())`，把解码线程、音频与文件句柄一起放开。

**每条回来的信号都要先问「还是这个视频吗」**：`_ref is None` 就丢弃。播放器是异步的，
切页、换片、切库都可能发生在那条信号到达之前。

### 5. 放映继续跳过视频

固定秒数与「视频自己有多长」是两套节奏，混在一起会打架。从视频页按播放键，从这一层的
第一张**照片**开始。

### 6. 两个解码器，职责不同

`core/media/video.py` 用 AVFoundation 取缩略图（core 不能导入 Qt，且必须在无事件循环的
工作线程里跑）；`ui/viewer/video.py` 用 Qt 的 FFmpeg 播放。前者是「网格里那张小图」，
后者是「点开之后放」—— 不是重复，也不该合并。

## 后果

- 视频双击的语义从「交给默认应用」变成「进播放页」；默认应用退到 `⇧⏎` 与右键菜单。
- 打包体积多约 65 MB，Nuitka 的裁剪只排了 tls / imageformats / offscreen / minimal，
  多媒体插件会跟着进包；**打出来的 `.app` 必须冒烟一次**。
- 测试用现场生成的 AVI（`make_video`）在离屏平台上验播放与坏文件；
  `QT_QPA_PLATFORM=offscreen` 下解码正常。
- 系统「正在播放」/ 媒体键没有接；音量跟着系统走，页面上没有音量控件。
- 视频页没有缩略图导航与缩放，这是刻意的：看视频要的是画面，不是像素级检查。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 把 video item 塞进 `DetailView` | 13 处 pixmap 假设要先重构，风险落在照片那条最常用的路径上 |
| `QVideoWidget` | 原生窗口，塞不进 `QGraphicsView`，缩放 / 背景 / 层级都要另做 |
| 用 AVFoundation 播放 | 要自己写音频、时钟、控件；Qt 已经是一份现成的播放器 |
| 视频进放映 | 固定间隔与视频时长是两套节奏；混在一起会互相打断 |
| 视频页保留缩放 | 视频固定适配窗口，100% 检查没有意义，还会让「⌘0」有两种解释 |
| 用 Qt 取缩略图 | 见 0090：4K 整帧进内存，还要事件循环 |
