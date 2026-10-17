# ADR 0062：最近打开的库登记进系统那份「最近项目」—— Dock 右键菜单里也有一份

- 状态：已接受
- 日期：2026-10-17
- 相关 ADR：0038（东西只放两个位置）、0007（打包成 `Gallery.app`）
- 相关代码：`src/gallery/core/external/recent_documents.py`、`src/gallery/ui/app.py`、
  `src/gallery/ui/window/library_flow.py`、`src/gallery/macos_app.py`、`src/gallery/core/constants.py`

## 背景

VS Code 与 Obsidian 在 Dock 图标上右键都能看到最近打开过的文件夹，其中 Obsidian 那份
**在应用没运行时也在**。要的就是那个效果，而它背后是两件不同的事：

1. **自定义 Dock 菜单**（`QMenu::setAsDockMenu()` / `applicationDockMenu:`）：菜单由应用进程
   现给现画，AppKit 把它摆在系统那几项（显示全部窗口 / 隐藏 / 退出）**上面**。应用一退出就
   没人提供了 —— 那一块随之消失。
2. **系统那份「最近项目」**：`NSDocumentController` 记着，落盘交给 Launch Services
   （`~/Library/Application Support/com.apple.sharedfilelist/...ApplicationRecentDocuments/<bundle-id>.sfl3`）。
   Dock 是另一个进程，读它自己画 —— **应用没运行时照样画得出来**（只是不做增删）。

VS Code 与 Obsidian 用的都是第 2 条（Electron 的 `app.addRecentDocument` 就是
`noteNewRecentDocumentURL:`）。Qt 没有任何 API 碰得到 `NSDocumentController`，
`setAsDockMenu()` 只管第 1 条。

## 决策

1. **走系统那份列表。** 「没打开时也在」只有它能做到；条目样式与过滤也由系统负责。
2. **登记点只有一处**：`LibraryFlowMixin._rebuild_recent_menu`。它是「最近列表的投影」那一步，
   而系统那份正是同一份来源的第二次投影 —— 开库、启动恢复都经过这里。
3. **整份重建，而不是只增不减。** 先 `clearRecentDocuments`，再把**仍然存在**的库按
   **最旧的先登记**补回去。两个理由：系统那份只增不减（删掉的目录会一直挂着，点下去才报错）；
   Dock 菜单从图标往上弹、系统条数上限又从尾部截断，最新的必须最后登记（这条与 VS Code 的做法一致）。
4. **只在真正的 `Gallery.app` 里登记**：`NSBundle.mainBundle().bundleIdentifier()` 必须等于
   `BUNDLE_IDENTIFIER`。裸解释器跑起来时那个值是解释器自己那个包而不是 Gallery.app，登记进去
   等于往别人的最近项目里塞东西，而且 Dock 上没有那个包也看不见。`BUNDLE_IDENTIFIER` 因此
   从 `macos_app` 搬到 `core/constants.py` 做单一来源（两处都得问它）。
   代价是开发态（`uv run gallery`）看不到这个效果，得跑打包后的 `.app`。
5. **Info.plist 声明能打开文件夹**（`CFBundleDocumentTypes` + `LSItemContentTypes: public.folder`），
   `LSHandlerRank` 取 `Alternate`：访达右键的「打开方式」里多一条 Gallery，**双击仍归访达**。
6. **点回来那条路不需要 pyobjc**：系统发 Apple Event，Qt 把它转成 `QFileOpenEvent` 交给
   `QApplication`，`WindowHost` 接住 —— 窗口在就换库，窗口不在（刚启动或 ⌘W 之后）就开一个
   带上它。**这个事件可能在窗口还没建的时候到达**，所以接在窗口宿主那一层。
7. **pyobjc 作为新依赖**（`pyobjc-framework-Cocoa; sys_platform == 'darwin'`）。Qt 没有对应的
   API，这是唯一的理由；它只在 `core/external/recent_documents.py` 里出现，用 `importlib` 拿
   （pyobjc 没有类型桩，而 `core/` 是 `mypy --strict`）。

## 后果

- Dock 右键菜单多出一份最近打开的库，**应用没运行时也在** —— 那份列表是系统的、静态的。
- 「最近打开的库」从此有两份投影：`session.json` 是唯一来源，系统那份由它整份重建。
- 库目录被删掉之后，系统那份里那一条要等下一次重建才消失（点它会按打不开处理）；应用内那份
  菜单一直按「还在不在磁盘上」过滤。
- 访达里右键文件夹的「打开方式」会多出 Gallery；双击仍归访达。
- 系统那份的条数归系统设置（「最近使用的项目」，设成「无」就什么都没有）—— 我们只保证把最新的
  那几条登记在最前面。
- 这个功能**不写用户的照片目录、也不写 `.gallery/`**：那份列表是系统自己的文件（ADR 0038 的
  两个位置之外的东西都不是我们写的）。
- 开发态看不到效果；测试进程是裸解释器，因此**测试天然不碰本机那份列表**（有一条测试钉着）。

## 被否决的方案

- **只做自定义 Dock 菜单（`QMenu.setAsDockMenu()`）**：纯 Qt、不加依赖、条目随我们摆，
  但菜单由应用进程提供，**应用不在运行时那一块就没了** —— 而「没打开时也在」正是被要的效果。
- **自己维护一份列表文件让 Dock 去读**：Dock 只读 Launch Services 的共享文件列表，那是私有
  格式，不能自己写。
- **在开发态也登记**：写进去的是解释器那个包的最近项目，Dock 上又没有那个包 —— 只污染环境。
