# ADR 0090：库里的视频——扫描、第一帧缩略图、双击交给默认应用

- 状态：已接受
- 日期：2026-10-24
- 相关 ADR：0001（解码分层）、0004（重命名）、0015（缩略图缓存）、0034（外部应用）、
  0043（事件）、0051（后台任务）、0038（缓存位置）
- 相关代码：`src/gallery/core/library/photos.py`、`src/gallery/core/library/scanner.py`、
  `src/gallery/core/library/watcher.py`、`src/gallery/core/media/video.py`、
  `src/gallery/core/media/decode.py`、`src/gallery/core/metadata/facets.py`、
  `src/gallery/core/library/facade.py`、`src/gallery/ui/window/metadata_flow.py`、
  `src/gallery/ui/window/calendar_flow.py`、`src/gallery/ui/window/viewer_flow.py`、
  `src/gallery/ui/window/slideshow_flow.py`、`src/gallery/ui/write_ops.py`

## 背景

Gallery 从第一版起只扫描图片扩展名，视频文件在扫描器那一步就被丢掉：不进网格、不进索引、
不能改名、也不能记进事件。用户的文件夹里却常有视频，而「相册」在用户心里从来不只是照片。

要回答三个问题：**取第一帧要不要引新依赖**、**视频该不该有自己的大图页**、**改名与事件要不要
跟着改**。本轮先落第一问与第三问，大图页另起一条（0091）。

## 决策

### 1. 只收 AVFoundation 能解的五个容器：mp4 / m4v / mov / avi / 3gp

`photos.VIDEO_SUFFIXES` 与 `SUPPORTED_SUFFIXES` 分开，扫描用 `is_supported_media`。
**不把 mkv / webm 收进来**：macOS 没有 Matroska 的解复用器，收进来只会得到一格占位灰；
用户真要看得装 ffmpeg 或换播放器，那与「不加依赖」直接冲突。

**`SUPPORTED_SUFFIXES` 保持只含图片**：元数据写回那条路只认它，视频绝不能被写进 EXIF。
搜索的「#文件格式」与分组走 `MEDIA_SUFFIXES`，于是 `mp4` 与 `MP4` 都认。

### 2. 第一帧用 AVFoundation，经 PyObjC 动态加载，不引新包

`pyobjc-core` 已是现有依赖，`objc.loadBundle` 能直接加载系统框架。取帧走
`AVURLAsset` + `AVAssetImageGenerator` 的 `copyCGImageAtTime`：**第 0 秒**、按容器方向摆正、
解码时就缩到缩略图长边（`setMaximumSize`），不会先解出一整幅 4K。CGImage 经
`NSBitmapImageRep` 落成 PNG 字节，再交给 Pillow 变成现有的 `RasterImage`。

实测（本机）：512 px 长边，h264 mp4 约 8 ms，4K HEVC mov 约 37–109 ms，十个并发 65 ms。

**为什么不是 Qt**：`core` 不导入 Qt，缩略图必须能在工作线程、没有事件循环的地方产出；
Qt 的播放器是异步的，还会把 4K 帧整幅解出来（实测 3840×2160 的 QImage，约 33 MB）。

**为什么不是 `qlmanage -t`**：每次一个进程 0.357 s，给的是**代表帧**而不是第一帧，输出还按
文件名命名（不同目录同名会撞）。

**为什么不是 `pyobjc-framework-AVFoundation`**：那是一个新包。动态加载拿不到 ObjC 的出参
（`actualTime` / `error`），只认返回值 —— 空指针就是失败，够用。

### 3. 缓存与超时原样复用

`ThumbnailCache` 一行不改：缓存键已经覆盖 `(路径, mtime, size, max_edge, spec_version)`，
视频是全新的键，`THUMBNAIL_SPEC_VERSION` 不必升。云盘占位文件会卡住 `open()` 的老问题
沿用 `decode._within_deadline` 的 20 秒时限与卡死线程上限。

`Library.preview` / `full` 对视频直接拒绝：视频没有「大图」，解一帧 4K 只会白占内存。

### 4. 双击交给系统默认应用，其余照片语义不变

视频没有大图页：双击、右键「打开」、日历上点唯一那一格，都走
`ui.desktop.open_with_default_app`。翻图（← / →）、预读预览与元数据、放映**跳过视频** ——
那几处都在解大图，视频进去只会白解码。

**改名与事件一行都不用改**：`PhotoRef` 与路径就是全部约定。改名保扩展名、记 journal、可 ⌘Z，
缩略图缓存按路径搬；事件绑定按路径记，改名时跟着搬，封面渲染走缩略图。

### 5. exiftool 能读视频的容器标签，只接日期与尺寸

`WANTED_TAGS` 加 `QuickTime:CreationDate` / `CreateDate` / `ImageWidth` / `ImageHeight`。
拍摄时间**先 CreationDate（本地墙钟）再 CreateDate（UTC）**：反过来会让东八区晚上拍的片子
落进前一天。没有 exiftool 时 Pillow 读不了视频，降级来源对视频给一份**空快照**，索引才会
记住「看过了」，而不是每次切分组都重读一遍。GPS 在视频里是 `QuickTime:GPSCoordinates`
一个复合字段，本轮不接。

## 后果

- 网格、检索、排序、分组、相册封面、事件、改名、废纸篓、粘贴与复制**都按现有路径工作**，
  视频只是多了一种后缀。日历只收有拍摄时间的视频（装了 exiftool 才有）。
- 视频缩略图失败时的横幅改说「N 个文件」，不再说「N 张照片」。
- 取帧失败（编码不支持、云盘没下完）只影响那一格缩略图；文件本身仍可双击打开。
- 缩略图缓存里多了视频的第一帧（JPEG，与照片同一条淘汰策略）。
- 测试不提交视频二进制：用 `struct` + Pillow 现场造一个几百字节的 AVI/MJPEG，AVFoundation
  能解出第一帧。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 用 Qt 的 `QVideoSink` 取缩略图 | 解码落到界面层，还要事件循环；4K 整帧进内存 |
| 用 `qlmanage -t` | 慢（一进程一文件）、给代表帧不是第一帧、输出名会撞 |
| 加 `pyobjc-framework-AVFoundation` | 新依赖；动态加载已经够用 |
| 收 mkv / webm | 系统解不开，用户看到的是一格灰；要解得引 ffmpeg |
| 视频也允许写元数据 | 没有可写的 EXIF；写回那条路只认图片格式 |
| 视频进放映 | 固定秒数与视频时长是两套节奏，混在一起会打架 |
