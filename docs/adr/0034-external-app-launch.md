# ADR 0034：用外部应用打开——靠改写 PWA 窗口的地址，不靠它自己的启动参数

- 状态：已接受
- 日期：2026-10-04
- 相关代码：`src/gallery/core/external/`、`src/gallery/ui/window/widgets/external_button.py`、`src/gallery/ui/main_window.py`、`src/gallery/ui/banners.py`
- 相关：[ADR 0025](0025-rename-in-the-title-bar.md)、[ADR 0006](0006-package-layout.md)、[ADR 0033](0033-calendar-view.md)、[ADR 0029](0029-banner-overlay.md)

## 背景

需求很小：日历那一页上，标题条里搜索胶囊左边放一个「用外部应用打开」的入口，默认是 Google 日历；
点一下把 Google 日历开到**与当前那一页一致的视图和日期**。难的是后半句 —— Chrome 的 PWA 在 macOS
上是一个真正的 `.app`，看起来应该有办法让它开到指定 URL。四条路逐条实测，**全部失败**：

| 做法 | 实测结果 |
|---|---|
| `open -a "Google 日历" <深链>` | 窗口开在 `https://calendar.google.com/calendar/r` |
| 直接执行包里的 `app_mode_loader <深链>` | 同上；URL 停在它自己的 argv 里，没有转交出去 |
| Chrome `--app-id=<id> <深链>` | 同上；这条路上启动的是 `--launched-by-chrome-process-id` |
| 改写 `Info.plist` 的 `CrAppModeShortcutURL` 并重新 ad-hoc 签名 | 同上 |

根因是**调用方**：这四条都在**启动时刻**只递了 `app_id`，URL 要么没递、要么是 Chrome 自己注册表
里的 `start_url`；而同一个 Mojo 接口（`com.google.Chrome.apps.<hash>`）在**调用时刻**递上 URL 时是
生效的 —— 工具栏上那个小芯片就是这么用的（实测标题栏读回「Google 日历 - 2026年6月28日这周」）。

## 决策

### 1. 不去启动 PWA，而是**改写它那个窗口的地址**

那个 PWA 窗口本来就挂在 Chrome 主进程里（它会出现在 Chrome 的 AppleScript 窗口列表中），既然能
被拿到，就能改写：

```applescript
tell application "Google Chrome" to set URL of tab 1 of window N to "<深链>"
```

冷启动（Chrome 原本没在跑）与热路径（已在跑，只改写不新开窗口）都验过。**这是本 ADR 最需要记住
的一条：它依赖的是 Chrome 未对外承诺的内部行为。** 它与「`--app-id` 不吃 URL」是同一枚硬币的两面 ——
启动路径不认 URL，页面内跳转却认。因此下面第 4 条的降级不是可选的礼貌，是必需品。

### 2. 「有没有装 PWA」按 `CrAppModeShortcutURL` 认，不按 `.app` 名字认

那个包在本机叫「**Google 日历**」—— 名字跟着 Chrome 的界面语言走，拿名字去问 LaunchServices 在别人
的机器上必然落空（英文系统上是 `Google Calendar`）。判据改成扫 `Chrome Apps.localized/*.app`、读
各自 `Info.plist` 里的 `CrAppModeShortcutURL`，hostname 等于 `calendar.google.com` 即命中。

### 3. 分层：纯逻辑进 `core/external/`，Qt 只剩一个控件

日期与 URL 的对应、PWA 检测、AppleScript 输出解析、启动决策全是纯逻辑，放 `core` 才能不造窗口就测；
`ui/` 那边只负责画一个 `QToolButton` 并把日历页的 `level()` / `selected_day()` / `anchor()` 原样递
下去。**视图不用映射表**：两边的 `year/month/week` 三层一一对应，日期取 `selected_day()`，没有
选中就取 `anchor()`；**不引入第三套星期口径** —— 两边的周都从周日起，周视图只要把那一天递过去就够。

### 4. PWA 路径失败就降级到浏览器，**并且说出来**

等 Chrome 起来最多二十秒、认窗口最多十二秒。任何一步落空，都退到 `open <深链>`（系统默认浏览器），
横幅照实说「未能启动，已改用浏览器打开」—— Chrome 大改版把那个内部行为改掉时，日历照样开得起来。

### 5. 整个链路进 `TaskRunner`

检测 + 启动 + 轮询最长二十多秒，绝不能在主线程跑。横幅文案进 `ui/banners.py`（一行 Qt 都没有，
因而能单独测），与 ADR 0029 同一份设施。

### 6. 不提交第三方商标素材，但**运行时**用系统/应用包里那一份

取了 PWA 就直接用它自己包里的 `Contents/Resources/app.icns`（永远是最新的），没装则画一个中性的日历
轮廓；访达那一项读 `/System/Library/CoreServices/Finder.app/Contents/Resources/Finder.icns`，取不到
（非 macOS）才退回自画的文件夹轮廓 —— 宁可画一个中性轮廓，也不画一个「像访达但不是」的。
**仓库里一个别人的图标都不落**（`resources/` 下只有应用自己的图标与城市库）—— 只**分发**自己的。

## 影响

- `core/external/` 是新的一层，**不碰任何用户文件**，约束 1 完全不涉及；`ui/` 也不自己 `open()` 文件（读 `Info.plist` 的事都在 core）。
- macOS 专属：别的平台一律走浏览器路径，界面与行为都不变。
- 首次点击会弹「Gallery 想控制 Google Chrome」—— macOS 的自动化授权，属于「用户可修复」，失败时给能照着做的中文提示，不说「打开失败」。
- 窗口识别是启发式（窗口名为空 + 单标签 + host 匹配）。**失配必须抛明确的中文错误**，绝不默默开错
  一个窗口 —— 那比开不起来更难排查。
