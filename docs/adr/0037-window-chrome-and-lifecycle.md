# ADR 0037：标题栏双击铺满窗口，⌘W 关窗与 ⌘Q 退出是两件事

- 状态：已接受
- 日期：2026-10-11
- 相关代码：`src/gallery/ui/main_window.py`、`src/gallery/ui/app.py`、
  `src/gallery/ui/dialogs/metadata_edit.py`
- 相关：[ADR 0025](0025-rename-in-the-title-bar.md)（标题栏那一行归我们画，决策 2 说**真全屏**时它收起来）、
  [ADR 0026](0026-search.md)（决策 8：搜索胶囊摆在标题栏那一行上）、
  [ADR 0034](0034-external-app-launch.md)（外部按钮的显隐判据只有 `is_available()` 一处）、
  [ADR 0014](0014-context-menu.md)（决策 5：菜单栏不是右键菜单的唯一入口）

## 背景

需求有两条：点标题栏那里要把窗口铺满 / 还原；⌘W 要能关窗口（但不退出应用）。两条看着是两件事，
其实是同一件：**那一段标题栏与那一扇窗口，此前都不是我们的**。内容铺到标题栏底下之后
（ADR 0025 决策 1），系统不再替我们画底色也不再替我们画字 —— 于是**它原先替我们做的两件事也跟着
没了**：拖窗口是我们自己发起的（`MainWindow.mousePressEvent` 里的 `startSystemMove`），
而双击标题栏压根没有实现。第 2 条另有**三处**各自独立的问题：

| 现象 | 原因 |
| --- | --- |
| 按 ⌘W 什么也不发生 | 压根没有这一条动作 |
| ⌘Q 同样按不动 | `QKeySequence.StandardKey.Quit` 在 macOS 上被 Qt 解析成**空序列**（实测 `count() == 0`） |
| 元数据编辑器那条「⌘W 关闭」也按不动 | 那一行写的是 `StandardKey.Close`，Qt 在 macOS 上把它解析成 **⌘F4** |

第三条最值得记：注释与 README 都写着「⌘W 关闭」，而它之所以**看起来**成立，只是因为主窗口上压根没有
⌘W —— 一个按不到的键，被另一个不存在的东西遮住了。**两条 `StandardKey` 在 macOS 上都不能直接用**，
这是决策 5 要硬编码的理由。

## 决策

### 1. 双击标题栏 = **铺满**（缩放），不是 macOS 的真全屏

`MainWindow.mouseDoubleClickEvent` 里落在标题栏那一段的左键双击，调 `_toggle_filled()`
（`showMaximized()` / `showNormal()`，还原时回到铺满之前那一份几何）。要的是「保留 dock 栏和上面菜单栏，
窗口只铺满中间的这部分」：

| | 窗口占的地方 | 菜单栏 / Dock | 是不是另一个 Space |
| --- | --- | --- | --- |
| **铺满**（缩放，本 ADR） | 菜单栏与 Dock 之间那一块 | **都在** | 否 |
| 真全屏 | 整个屏幕 | 都收起来 | 是 |

Qt 上就是 `showMaximized()`：它在 macOS 上落到 `[NSWindow zoom:nil]`（Qt 的 cocoa 插件里能查到
`zoom:` / `isZoomed` / `windowShouldZoom:toFrame:` 这几个选择器），而缩放落到屏幕的 `visibleFrame`
—— 屏幕减去菜单栏、再减去没有自动隐藏的 Dock；写成 `showFullScreen()` 就把菜单栏与 Dock 一起收起来了。
`tests/ui/test_title_bar.py::test_double_clicking_the_title_bar_fills_the_window_and_back`
**两行都断言**（`isMaximized()` 与 `not isFullScreen()`）：只断第一行的话，换回 `showFullScreen()`
那条照样绿。**不读系统设置里「双击标题栏」那一档**（可选「缩放 / 填满 / 无操作」）：那一段已经是
我们的了（ADR 0025 决策 1），而三档里只有「缩放」是我们要的 ——「填满」会盖住菜单栏与 Dock，
「无操作」等于双击什么也不发生。固定做缩放，行为不随系统那一档变。

**来来回回都要能**：铺满只是窗口变大，标题栏那一段照旧在，于是第二次双击仍然落在标题栏上
（`_is_title_bar` 的判据是 `isFullScreen()`，**不是** `isMaximized()`）。少了这一半就是
「进得去、出不来」。**真全屏那一段不是标题栏**：`_is_title_bar` 在 `isFullScreen()` 时一律返回假 ——
那是我们**自己不再走**的一条路，但用户自己能走进去（把绿按钮设成「进入全屏幕」），那时退出得靠绿按钮
或 ⌃⌘F，把那一块算成标题栏只会给退出多一条走不通的路。

### 2. 拖窗口按**移动距离**发起（不是「等过一个双击周期」）

`startSystemMove` 一发起就是系统的窗口拖动会话，那一串鼠标事件被它整个忽略掉 —— 双击标题栏于是永远
到不了 `mouseDoubleClickEvent`。所以拖动不能在第一下按下时就发起；但「什么时候算拖动」的判据是
**鼠标动了多远**，不是**等了多久**。于是按下只在 `mousePressEvent` 里记一个记号（按下位置），
`mouseMoveEvent` 里移动超过 :data:`~gallery.ui.window.title_bar_flow.DRAG_THRESHOLD` 才真的发起；
松手立刻作废（拖窗口得按住并动起来，点一下什么也不该发生）。双击那一点手抖远到不了这个距离。

**这条线要小（现在是 3 点），不能拿拖放（DnD）那条 ``QApplication.startDragDistance``
（macOS 上 10 点）来用**：窗口拖动要立刻跟手，而 10 点的死区读起来就是「拖之前光标得先动一会儿」。
**也不能改成「等过一个双击周期」**（按下排一个 `QTimer`，间隔取
`QApplication.doubleClickInterval()`，超时才发起），那有两个看不见的问题：

- **`startSystemMove` 从定时器回调里调会根本不动手**：它取的事件要么是**当前**的
  `NSApp.currentEvent`（Qt 6.5 ~ 6.10：只在它是鼠标事件时才动手，否则直接返回假），要么由 Qt 按
  当前指针位置现合成一个（Qt 6.11 起），定时器回调里哪一种鼠标事件都没轮到。
- **用户得先按住不动到双击周期过去，窗口才开始跟手**：正常的一拖在这个时限之前就松手了，
  `mouseReleaseEvent` 把前置条件一撤，窗口一步都没动。

macOS 自己的标题栏也是按距离分的（动过就是拖，没动才算双击），差不多 2~3 点就动了 ——
没有「先等半秒」这一步。

**改到一半一律让开**（`_press_belongs_to_the_title_bar`）：那一段上正摆着输入框，拖走它等于把正在
改的名字甩到屏幕外，铺满则会让它当场收起来。拖与双击用同一句判据，不会出现「拖不动却能双击」
这种半截状态 —— 与 ADR 0025 决策 6 同一类让位。

### 3. ⌃⌘F 是铺满的键盘那条路

`视图 → 铺满窗口 / 还原窗口`，文案跟着此刻的状态走。⌃⌘F 正是 macOS 系统给缩放/铺满的键，写成
`QKeySequence("Ctrl+Meta+F")` —— **⌘ 在 Qt 里写作 `Ctrl`，⌃ 写作 `Meta`**；写成 `"Ctrl+Ctrl+F"`
会被 Qt 并成 ⌘F，与「查找」撞在同一个键上。这一条与双击是**同一件事的两条路**，少一条就只剩
「进得去、出不来」；**「铺满与否」本来就记在会话里**（`_save_session` 存的
`WindowGeometry.maximized`），所以下一次启动回到铺满的状态不必另写一份。

### 4. 窗口状态一变，窗口顶边那一行重摆一次

`changeEvent` 里认 `WindowStateChange`，重跑 `_layout_title_bar` 与 `_layout_banner`。
**铺满时其实什么都不用动**（那只是窗口变大，安全区与标题栏照旧在），而**真全屏要动**：标题栏、搜索胶囊
与外部按钮整个收起来（否则盖在照片上，ADR 0025 决策 2），**内容区那 12 点空档也要还回去**（那是平时
给标题文字与胶囊腾出来的，ADR 0026 决策 8；不还回去的话，照片区顶上压着一条什么都没有的白边）。
外部按钮的显隐在这里要**重新问一次** `is_available()`：它是真全屏那一支藏起来的，而退出真全屏不经过
`_update_action_states`，不补这一句它就没了，判据仍然只有 `is_available()` 那一处（ADR 0034）。
**判据不能只挑真全屏那一路来**：一处分支、两种状态都在同一个 `WindowStateChange` 里，分开判要多写
一处「什么也不用动」，而那一处将来最容易走错。

### 5. ⌘W 关窗、⌘Q 退出，**两件事各归一半**

- **⌘W** → `MainWindow.close()`。与「退出」挨着摆在「文件」菜单末尾（访达与文本编辑都是这个位置）。
- **⌘Q** → `MainWindow.quit_app()`：**先 `close()`，再 `app.quit()`**。顺序不能反：会话落盘、监听器停掉、
  后台任务结束全在 `closeEvent` 里，而 `QApplication.quit()` 压根不发 `closeEvent` —— 直接调它，
  那三样一步都不走。**两者的键都显式硬编码，不抄 `StandardKey`**（`Ctrl+W` / `Ctrl+Q`）：上面那张表里的
  两处就是这么错的。

### 6. 应用允许「一个窗口都没有」，并叫得回来

`gallery.ui.app.keep_running_without_windows` 关掉 `quitOnLastWindowClosed`，`WindowHost` 在应用回到
前台而屏幕上一个窗口都没有时重新开一个。**关掉的那个是真关掉的**：`closeEvent` 已经停掉文件监听、
收了后台任务、开销归零。叫回来时开的是**新一个窗口**，不是把旧的复活 —— 复用它等于造一个「库是空的、
网格是上一层」的开局；会话里记着上次打开的库，于是照片又回来了（README 承诺过的那一条）。
**少任何一半都不成**：只关不退出，⌘Q 按了没反应；只退不关，⌘W 会顺手把应用带走。

## 后果

- 标题栏那一段现在管三件事：显示名字（ADR 0025）、拖窗口、铺满窗口（本 ADR）。后两件在
  `MainWindow` 自己身上 —— Qt 按 MRO 找第一个实现，拆进 mixin 就会有一处不生效。
- **⌘W 关掉之后，⌘Q 只在有窗口时按得到**：那条动作挂在窗口上，而 macOS 上没有窗口就没有菜单栏，
  回到窗口的路是 Dock 上那枚图标。
- 元数据编辑器里那条「⌘W 关闭」从此真的按得到了（此前是 ⌘F4）。**框开着的时候主窗口不会被一起
  带走**：模态（框用 `exec()` 开）挡住主窗口的快捷键，而框自己那一条让 ⌘W 在框里有事可做。
- 回归测试：`tests/ui/test_title_bar.py`（移动够远才发起拖动、手抖不算、松手作废、双击铺满与还原、
  铺满时顶边那一行留在原处、内容区双击不越权、⌃⌘F 的文案、真全屏那一支仍然成立、改名时让位）、
  `tests/ui/test_shortcuts.py`、`tests/ui/test_app.py`（其中一条**在子进程里真跑一遍事件循环**：
  ⌘W 之后进程还活着、⌘Q 才退）。