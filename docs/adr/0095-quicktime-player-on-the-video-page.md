# ADR 0095：视频页第一项是系统 QuickTime Player

- 状态：已接受
- 日期：2026-10-25
- 修改：[0093](0093-iina-video-external-player.md) 决策 3 的「IINA 固定第一」—— 视频页最前
  那一段现在是 QuickTime Player、IINA 两项；[0094](0094-external-order-splits-photos-and-videos.md)
  决策 4 与后果里那份视频页默认次序 —— 前面多一项 QuickTime Player。
- 相关 ADR：0034（用外部应用打开）、0067（图片应用白名单）、0080（外部应用次序）、
  0093（IINA）、0094（图片 / 视频两份次序）
- 相关代码：`src/gallery/core/external/apps.py`、`src/gallery/ui/main_window.py`、
  `src/gallery/ui/window/widgets/external_button.py`

## 背景

需求是「给视频的外部应用打开加一项『预览』，放在默认位置第一个」，理由是「预览应该能看很多
格式的视频」。这条前提在 macOS 上不成立，实测（本机 macOS 26）：

- **「预览」不收视频。** 它的 `Info.plist` 声明了 `public.data`，但把 mp4 / mov 递给它之后
  一个窗口都不开（同一台机器递 png 正常）。挂上下拉框只会得到一项点下去没反应。
- **访达里按空格唤起的是「快速查看」面板**，不是 QuickTime Player：开着它时进程表里只有
  `QuickLookUIService`（AVKit），QuickTime 没起来。
- **那个面板没有可用的启动入口。** 唯一的口子是 `qlmanage -p`，而它放视频会直接终止：
  `Setting highlightedTimeRanges is not supported in your app`（mp4 与 mov 各试一次）。
- 系统里能被 `open -a` 叫起来、又真的会播视频的只剩 **QuickTime Player**
  （`com.apple.QuickTimePlayerX`）。

## 决策

### 1. 新增 `apps.QUICKTIME`，只在视频页出现

`id="quicktime"`、显示名 `QuickTime Player`、bundle id `com.apple.QuickTimePlayerX`。
**不放进 `VIEWERS`**：那是图片应用的白名单（与 IINA 同一条理由）。`pages` 只有 `video`，
`enabled` 是「大图页上正看着一个视频，且本机装着它」，`available` 是「本机装着它」——
没装就整条不摆。

### 2. 内置次序里它排在 IINA 前面

视频页的内置次序变成 **QuickTime Player、IINA、地图、Google 地图、访达、QSpace**。
QuickTime 是系统自带的那个，先摆它符合「先系统、再第三方」的直觉；IINA 覆盖它放不了的
容器格式（MKV / AVI 等），排在第二位。

### 3. 交接之前先暂停应用里的播放器

与 IINA 同一套（ADR 0093 决策 2）：`VideoView.pause()` 之后走
`desktop.open_with_app([视频路径], app=名册查到的那条路径, name="QuickTime Player")`。
两条路共用 `MainWindow._open_video_in`，免得只在一处记得暂停。

### 4. 「固定第一」从一项扩成一段

`order_for_page` 原来只给 IINA 开例外，现在视频页最前那一段是
`VIDEO_LEADERS = (QUICKTIME, IINA)`：用户存下的次序里缺哪个就按内置次序补在最前，两个都在
就完全按存下的排。ADR 0080 决策 3 的「没提到的 id 排在后面」对其它页不变。

## 后果

- 装了系统播放器（macOS 上总是装了）的视频页，左半那一下就是 QuickTime Player；IINA 排第二。
- 存过自定义视频次序的会话：里面没有 QuickTime 的，它会被补到最前，直到用户在
  「设置 › 外部应用顺序」里动过一次（那次保存会把两项都写进次序）。
- 没有新依赖：QuickTime Player 是系统自带的；应用只按 bundle id 问一次名册、起一个 `open`
  进程。数据安全边界没变，这一层不碰用户的文件。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 照原样加「预览」 | 它打不开视频，点下去什么都不发生 —— 正是「不摆点下去没反应的条目」要避免的 |
| 用 `qlmanage -p` 起「快速查看」面板 | 本机放视频直接崩溃；而且它是个浮动面板，不是能长期留在前台的应用 |
| 用 AppleScript 让访达选中文件再按空格 | 依赖辅助功能授权，会先弹出访达窗口，失败路径说不清 |
| 只留 IINA | 用户要的是系统自带的那个播放器；QuickTime 不装也不折腾，IINA 仍在第二位 |
| 把 QuickTime 放进 `VIEWERS` | 那是图片应用的白名单，会在照片页上多出一项打不开照片的软件 |
