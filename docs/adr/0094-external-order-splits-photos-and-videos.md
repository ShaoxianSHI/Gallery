# ADR 0094：外部应用的次序分成图片与视频两份，合计四个位置

- 状态：已接受
- 日期：2026-10-25
- 修改：[0080](0080-external-app-order.md) 决策 1 的「三个位置」—— 增至四个（大图页拆成
  图片 / 视频）；[0067](0067-detail-page-viewers.md) 决策 2 的白名单次序—— 内置次序改成
  预览、Picview、ImageGlass、地图、Google 地图、访达、QSpace；[0095](0095-quicktime-player-on-the-video-page.md)
  给视频页最前加了 QuickTime Player。
- 相关 ADR：0034（用外部应用打开）、0067（图片应用白名单）、0080（外部应用次序）、
  0081（拖动排序对话框）、0093（IINA 固定在视频页第一位）
- 相关代码：`src/gallery/core/library/session.py`、`src/gallery/core/external/apps.py`、
  `src/gallery/ui/main_window.py`、`src/gallery/ui/window/settings_flow.py`、
  `src/gallery/ui/window/widgets/external_button.py`、`src/gallery/ui/dialogs/external_order.py`

## 背景

ADR 0080 把「用外部应用打开」拆成三个位置：大图页 / 网格页 / 日历页。大图页上既能看照片、
也能看视频（ADR 0091），而两类的应用集合几乎不相交：图片应用（预览、Picview、ImageGlass）
打不开视频，IINA 也不打开照片。放在同一份次序里，用户排「最常用的图片应用」时得先绕过 IINA
（ADR 0093 还把它固定在第一）；视频页上又会先排出几个点下去没反应的条目。新加 Picview 之后
这一列更长，问题更明显。

## 决策

### 1. 四个位置：图片 / 视频 / 网格 / 日历

`EXTERNAL_PAGES` 变成 `detail` / `video` / `grid` / `calendar`。`detail` 的含义收窄成
「大图页上的照片」，视频用新键 `video`。老会话文件里没有 `video` 就是内置次序
—— **不用升 `SESSION_VERSION`**，解析照旧只认已知的键（ADR 0080 决策 1）。

### 2. 按正在看的那一项挑次序

`MainWindow._external_page_kind` 在大图页上问 `_detail_photo_ref()`：视频走 `video`，其余走
`detail`。翻图时选区变化本来就会重跑 `_update_action_states`，于是从照片翻到视频时左半与
菜单跟着换，不必另加一条「翻到视频了」的通知。

### 3. 条目按页登记

IINA 只属于 `video`；图片应用只属于 `detail`；地图、Google 地图、访达、QSpace 两页都算
（视频也有坐标，见 ADR 0093）；网格照旧是访达 / QSpace；日历是两个日历应用。

### 4. IINA 的「固定第一」跟着搬到 `video`

`order_for_page` 只在视频页补这一条。ADR 0093 的规则不变，只是页面换了：用户存下的次序里
没有 IINA 时补在最前，用户自己排过就以存下的为准。

### 5. 图片页的内置次序

**预览、Picview、ImageGlass、地图、Google 地图、访达、QSpace。** 前三个回答「用哪个软件看
这一张」，地图排在文件管理之前是因为大图页上它更常用；访达与 QSpace 回答「这一张在磁盘上的
哪儿」，放在最后。

### 6. 设置对话框四页

图片 / 视频 / 网格 / 日历各一张可拖动的列表（ADR 0081 的交互不变）。`SettingsFlowMixin`
照旧按 `EXTERNAL_ORDER_LABELS` 的先后建页。

## 后果

- 老的自定义 `detail` 次序继续生效在图片页；里面若还留着 `iina`，认不出来就按「没提到」
  处理（ADR 0080 决策 3）。
- 视频页的默认次序是 **QuickTime Player、IINA、地图、Google 地图、访达、QSpace**（装了哪几项
  就摆哪几项，前两项见 ADR 0095）。
- 用户多排一份次序；换来的是图片页第一项永远是一个图片应用，视频页也不必绕过三个打不开
  视频的条目。
- 数据安全边界没变：这一层只写 `state.json`，用户的照片一个字节都不碰。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 视频留在 `detail` 里，只靠 `enabled` 过滤 | 次序还是同一份：IINA 会挤在图片应用中间，用户排图片页时仍要绕过它，排视频页时仍会碰见图片应用 |
| 按文件类型动态生成次序 | 次序是用户的偏好，不该随翻到哪一张重排；「恢复默认」也需要一份固定的参照 |
| 视频另开一个设置入口 | 四份列表在同一个对话框里才能一起看见；分成两个入口只会让人不知道另一份在哪儿 |
| 把 IINA 放进 `VIEWERS` | 那是图片应用的白名单；放进去会让它在照片页上也出现（ADR 0093 已经否过一次） |
