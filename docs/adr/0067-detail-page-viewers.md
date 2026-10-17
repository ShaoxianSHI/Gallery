# ADR 0067：大图页那一列图片应用——手写的白名单，按 bundle id 认装没装

- 状态：已接受
- 日期：2026-10-17
- 相关 ADR：0034（用外部应用打开的入口）、0066（QSpace 那一项）、0021（大图页的当前项）、
  0014（「在默认应用中打开」那条右键命令）
- 相关代码：`src/gallery/core/external/apps.py`、`src/gallery/ui/desktop.py`、
  `src/gallery/ui/main_window.py`、`src/gallery/ui/window/widgets/external_button.py`

## 背景

标题条那个入口在大图页上给的两项（访达 / QSpace）回答的都是「**这一张在磁盘上的哪儿**」。
大图页上还有另一件顺理成章的事：**用哪个软件看它**。右键菜单里已经有「在默认应用中打开」，
但那是系统为这个类型设定的那一个（本机实测是 ImageGlass），挑不了。

系统里「能打开一张 jpg」的应用是一份查得到的名册（Launch Services 的 `open with` 名单）。
本机实测那一份是：**ImageGlass、预览（Preview）、ColorSync Utility、Chrome、Safari、QuickTime
Player** —— 后四个是噪声，得挡掉。

## 决策

1. **只在大图页出现。** 网格页没有「正在看的那一张」；用某个软件打开**选区**是右键菜单那一组
   回答的问题，两处的对象不是同一个。
2. **一份手写的白名单，不问 Launch Services 要名单**：`预览` + `ImageGlass`
   （`core.external.apps.VIEWERS`）。系统那份名单里的 ColorSync Utility / Chrome / Safari /
   QuickTime 也声明能开图，递一张照片过去得到的却是别的东西（浏览器里开一个标签、一帧画面、
   一个色彩工具），摆成按钮只会让人以为软件坏了。
3. **装没装按 bundle id 认**（`com.apple.Preview` / `com.duongdieuphap.imageglass`），**没装就
   不摆这一项** —— 与 QSpace 同一条判据。这也意味着**显示名与判据是分开的**：
   名字换发行版、换系统语言都可能变。
4. **递给 `open -a` 的永远是名册查到的应用包路径，显示名只进菜单与横幅。** `open -a` 不认中文
   显示名：`open -a 预览 <图>` 直接退出 1（`Unable to find application named '预览'`），
   而 `open -a /System/Applications/Preview.app <图>` 正常。文案里是中文、命令里是路径，两者
   不许混。
5. **递过去的是照片自己的路径**，不是它所在的目录 —— 用户要看的正是这一张。
6. **起不来就退回系统默认应用，并且说出来**（与 QSpace 那一条同一个理由）：用户以为照片开在
   ImageGlass 里、看到的却是别的软件，比明说换过更难查。
7. **QSpace 与图片应用合并成一个模块**（`core/external/qspace.py` → `core/external/apps.py`）：
   两件事的机制完全相同 —— 按 bundle id 问名册、从应用包里读图标。机制只留一份，
   `QSpace` 与 `VIEWERS` 都只是那份名单上的条目。

## 后果

- 下拉框里最多四项，次序固定：**访达、QSpace、预览、ImageGlass**（装了哪几项就摆哪几项）。
  **左半那一下打开的仍然是最前面那一项可用的**（访达）—— 新来的几项都排在后面。
- 装没装**只在启动时问一次并缓存**：会话中途去装了 ImageGlass，要重开 Gallery 才看得到。
- 这一层**不碰用户的照片**：只读应用包的 `Info.plist`、只起一个 `open` 进程。
- 首次点击可能弹「Gallery 想控制 …」之类的系统询问，属于「用户可修复」。
- 非 macOS 上 `VIEWERS` 里那两项都不出现（`ImportError` → 当作没装），QSpace 同理。

## 被否决的方案

- **动态问 Launch Services 要「能打开这一张的名单」**：省事，但会把上面那四个噪声一起列出来；
  而且谁前谁后也得自己排（本机上 ImageGlass 的 `LSHandlerRank` 只是 `Alternate`）。
- **放进右键菜单**：那里已经有一条「在默认应用中打开」，再加几条同名不同义的，用户说不清
  按下去开的是哪一个；下拉框那一列本来就是「同一个动作换个软件」的地方。
- **网格页也摆**：那一页没有「正在看的那一张」，得先定义「选区里的每张都用它开」，而多选时
  `open -a` 收一串路径会开出多少个窗口取决于那个应用自己 —— 那是另一件事，没需求不做。
- **把 Photos.app 也放进去**：它 `Info.plist` 里声明了 `public.image`，但递一张照片过去等于
  往它的库里**导入一份拷贝** —— 与「只增不改」不是一回事；而且它注册的是自己的库类型
  （`com.apple.photo.library` 等），本来就不在那份 `open with` 名单里。
- **按 `.app` 的名字认（`/System/Applications/Preview.app`）**：换系统语言就落空，而且是静默落空
  （下拉框里少一项，没有别的症状）——「按名字认」在 ADR 0034 决策 2 里已经吃过一次。
