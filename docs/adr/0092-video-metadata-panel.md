# ADR 0092：视频的元数据面板——QuickTime 标签、UTC 换本机时区、GPS 进索引

- 状态：已接受
- 日期：2026-10-25
- 相关 ADR：0090（视频进库）、0091（视频播放页）、0002（元数据面板）、0003（离线地名）、
  0032（拍摄时间）、0038（缓存位置）
- 修改：[0093](0093-iina-video-external-player.md) 去掉决策 4 里「视频面板写一句只读理由」，
  改成只把按钮置灰。
- 相关代码：`src/gallery/core/metadata/exif.py`、`src/gallery/core/metadata/report.py`、
  `src/gallery/core/metadata/facets.py`、`src/gallery/core/metadata/batch.py`、
  `src/gallery/ui/window/metadata_flow.py`、`src/gallery/ui/window/context_menu_flow.py`

## 背景

0090 / 0091 让视频能扫描、能播放，但右侧的元数据面板对视频只写了一句「视频没有可读的元数据」。
视频其实有元数据：MP4 / MOV 的 QuickTime 容器里有拍摄时间、GPS、设备、时长、分辨率、帧率、
编码；exiftool 都读得到（实测：把这几项写进一个 `.mov` 副本再读回来，标签与值都对得上）。

**但不是每个文件都有。** 微信这类导出工具会把 `CreateDate` 清零、删掉 GPS 与设备；
AVI 基本只有尺寸与时长。面板要如实显示「没有」，而不是把整个功能藏起来。

## 决策

### 1. 面板对视频读 QuickTime 标签，新增「视频」组

「拍摄信息」（相机、拍摄时间）、「位置」（坐标）、「图像」（像素尺寸）复用现有的中文分组；
新增一组「视频」：时长（`QuickTime:Duration`）、帧率（`VideoFrameRate`）、编码
（`CompressorID`，`avc1` → H.264、`hvc1`/`hev1` → HEVC）、码率（`AvgBitrate`）。
没有的字段不出现，于是照片那边一行都不变。

### 2. 拍摄时间：`CreationDate` 原样，只有 `CreateDate` 时从 UTC 换成本机时区

QuickTime 的两栏语义不同：

| 标签 | 语义 | 显示 |
|---|---|---|
| `QuickTime:CreationDate` | 拍摄地墙钟 + 偏移 | 原样（`2024-05-16 20:52:02+08:00`） |
| `QuickTime:CreateDate` | UTC | 换算成本机时区，并带上本机偏移 |

容器里的 `CreateDate` 没有拍摄地时区，只有 UTC；把它原样当墙钟会让东八区晚上拍的片子落到
前一天（与 ADR 0033 决策 3 同一类错误）。换算成本机时区后，本机拍的照片与视频在面板和分组里
是同一个说法。`0000:00:00 00:00:00` 这类导出工具填的零值一律当「没有」。

### 3. GPS 同时进面板与索引

- `WANTED_TAGS` 加 `QuickTime:GPSCoordinates`、`QuickTime:Make`、`QuickTime:Model`。
- `exif.quicktime_coordinates` 解析两种形状：exiftool 默认输出的度分秒
  （`31 deg 13' 49.44" N, ...`）与文件里存的 ISO 6709（`+31.2304+121.4737/`）。
- 索引的 `extract` 与面板的 `build_report` 都走同一个解析；有坐标的视频因此和照片一样
  参与按城市 / 省州 / 国家分组（`place_pass` 的反查不需要额外接线）。
- 相机也用 `QuickTime:Make` / `Model` 回退，于是「按相机分组」对视频同样成立。

### 4. 视频只读：编辑按钮灰着，复制放开

面板底部的「编辑元数据」对视频禁用，理由写在 tooltip 里（「视频的元数据是只读的」）；
写回那条路仍然只认图片格式（`writeback` 的 `is_supported_photo`）。「复制元数据」只是读，
对视频放开。

### 5. 没有 exiftool 时给「装 exiftool」而不是「文件读不出来」

视频没有 Pillow 降级路径（Pillow 解不了视频容器），所以面板读视频时
`read_metadata(..., allow_fallback=False)`：缺 exiftool 时给一句可行动的提示。
索引那边照旧：`PillowBatchSource` 对视频给一份空快照，避免每次切分组都重读。

## 后果

- 有拍摄时间 / GPS 的视频在面板、日历、按时间 / 地点 / 相机分组里的表现与照片一致。
- 微信导出、屏幕录制这类文件会显示「没有」；这是文件本身没有，不是软件读不到。
- 面板多了一组「视频」；照片的元数据读取与写回路径没有变化。
- 索引多存两栏（GPS、设备），`place_pass` 会为带坐标的视频反查一次城市。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 把 `CreateDate` 原样当墙钟 | 它是 UTC，东八区的晚上会落到前一天（与照片的墙钟口径也不一致） |
| 换算到「拍摄地时区」 | 容器里没有这个信息；`CreationDate` 有偏移时已经原样给出 |
| 只显示「文件」一栏 | 用户要看的正是拍摄时间与位置 |
| 让 GPS 只进面板、不进索引 | 那样「按城市分组」对视频还是空，面板与分组会各说一套 |
| 给视频写元数据 | 没有可写的 EXIF；写回那条路只认图片，视频是只读的 |
| 用 Pillow 读视频 | 它解不了视频容器；降级只对照片有意义 |
| 继续显示「视频没有可读的元数据」 | 有标签的文件被一句话盖掉，等于把已有信息藏起来 |
