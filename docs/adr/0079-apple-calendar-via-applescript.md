# ADR 0079：系统「日历」也能开——只能靠 AppleScript，年映射到月

- 状态：已接受
- 日期：2026-10-24
- 相关 ADR：0034（用外部应用打开）、0033（日历视图）、0067（大图页那一列图片应用）、
  0075 / 0076（地图那一组两项、Apple 在前）
- 相关代码：`src/gallery/core/external/apple_calendar.py`、
  `src/gallery/core/external/apps.py`、`src/gallery/core/external/launch.py`、
  `src/gallery/ui/main_window.py`、`src/gallery/ui/banners.py`、
  `src/gallery/ui/window/widgets/external_button.py`

## 背景

日历页上已经有「用外部应用打开」，但只有 Google 日历一项。地图那一组是两项（系统「地图」+
Google 地图，Apple 在前），日历这一组也就该有系统「日历」。**能不能定位到某一天**要先验：

| 做法 | 实测结果（macOS 26.6.2 / Calendar 16.0） |
|---|---|
| `Info.plist` 的 `CFBundleURLTypes` | 只有 `webcal` 与 `ical`（订阅 / 导入），**没有日期 scheme** |
| `open "calshow:0"` | `kLSApplicationNotFoundErr`（-10814）—— macOS 上根本没注册 |
| `open "ical://"` | 只能把应用带到前台，不带日期 |
| AppleScript `view calendar at` | **可用**，配 `switch view` 还能同时指定视图 |

脚本字典（`Contents/Resources/iCal.sdef`）里正好有这两条命令：`switch view`（`goToViewCommand`）
与 `view calendar at`（`goToDateCommand`）。实测数据：

- 首启（含自动化授权框）4.3 s；之后冷启 1.0 s；热路径 0.18–0.37 s。
- **不新开窗口**：跑前跑后 `count windows` 相同，是在现有窗口里导航。
- **不会自己到前台**：不带 `activate` 时前台仍是别的应用，所以脚本里必须显式 `activate`，
  否则用户会以为按钮没反应。
- 生效证据：`defaults read com.apple.iCal` 的 `last calendar view description` 跟着
  `week view` / `month view` 变，`CalFirstVisibleDate` 落在请求的那一天。

## 决策

1. **加一项「日历」，靠 AppleScript 定位，不加任何 scheme。** 它没有日期 URL scheme，
   生成 `.ics` 打开等于往用户日历里塞一条没发生过的记录，EventKit 又只能读数据、不能导航界面。
2. **日期用数字分量拼，不用 `date "…"` 字面量。** 字面量跟着系统语言走，换个语言就解析不出来。
   先把日号归 1 再改年月（31 日改到 2 月会溢出），时间定在中午（跨夏令时也不会落到前后一天）。
   应用按 bundle id 寻址（`com.apple.iCal`），名字只进菜单与横幅。
3. **年映射到月视图。** 脚本字典的 `view type` 只有 day / week / month，写 `year view`
   是语法错误（-2740）——界面上的年视图不在脚本能碰到的范围里，于是取最接近的一层：
   落在锚点那一天的月。**这是与 Google 那一项唯一不一致的地方**（Google 有 `/r/year/…`）。
4. **排在 Google 日历之前。** 与地图那一组同一个次序（Apple 在前），于是日历页左半那一下
   与它的图标都是系统「日历」；没装（或不是 macOS）就自动落到 Google 那一项。**这是一处行为
   变更**：以前日历页左半是 Google 日历。
5. **整个动作进后台线程，超时给到 120 秒。** 冷启动一秒上下，而**第一次点击会先弹自动化授权框**，
   用户什么时候点「好」说不准。沿用 `COMMAND_TIMEOUT_SECONDS`（10 秒）会在用户点之前就把
   `osascript` 杀掉、框也一起消失 —— 第一次点击必然失败。
6. **打不开就照实说，并且只有这一条路。** 拒绝授权是 -1743，提示要给到
   「系统设置 › 隐私与安全性 › 自动化」；-1712 是没响应，-1728 / -10814 是找不到应用。
   这里**没有降级目标**（不像 Google 那条能退回浏览器），所以文案必须能照着做。
7. **图标走 ADR 0034 决策 6**：问系统要 Calendar.app 画好的那一张，取不到才画中性轮廓。

## 后果

- 日历页的下拉框最多两项：**日历、Google 日历**；左半是前一个可用的，于是装了系统「日历」
  时左半是它、图标也是它。别的页一项都不摆（与 Google 那条同一条判据）。
- 第一次使用会弹一次「Gallery 想控制“日历”」。**这条授权是应用级的**（拿到就能读写全部日历
  数据），但代码只跑这一条只读导航脚本。
- 授权被拒绝时那一项仍然在，点下去给的是「去系统设置里允许」。
- `.app` 的 `Info.plist` 需要 `NSAppleEventsUsageDescription`；打包现在是 ad-hoc 签名
  （`codesign --sign -`），**每次重打包 cdhash 都会变，自动化授权可能要重新给一次**。
  以后上 hardened runtime / 公证还要补 `com.apple.security.automation.apple-events`。
- 这一层**不碰用户的照片**，也不写日历：`view calendar at` 只改「看哪一天」。

## 被否决的方案

- **`ical://` / `calshow:`**：前者不带日期，后者在 macOS 上没注册（见上表）。
- **`open -a Calendar` 再模拟按键**：要辅助功能授权，且按键序列跟着界面语言与版本走。
- **用 System Events 点「显示 › 年」菜单**：多一层辅助功能授权，比 AppleScript 更脆。
- **生成一条 `.ics` 让日历打开**：会往用户的日历里加事件，属于写用户数据。
- **只给年视图摆 Google**：同一项在年视图上忽然消失，比「年看到月」更难理解。
