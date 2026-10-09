# ADR 0060：界面层目录的收口 —— 拆掉 `panels/`，补上 0059 承诺的那条守护

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0006（包布局）、0041（层级栈）、0059（界面层目录结构）
- 相关代码：`src/gallery/ui/`

## 背景

ADR 0059 按「谁牵连谁」重排了 `ui/` 顶层，方向是对的。**这一条是把它剩下的账结清**，
外加补一处它承诺过而没落地的东西。

量出来的现状（`ui/` 21007 行 / 81 模块，**import 环 0 个**）：

```
window/   10016 (48%)   26 mixin + state/(8) + widgets/(4)
<顶层>     3552         12 个模块
browser/    2290         7 模块
calendar/   2232        12 模块
panels/     2084         6 模块   ← 问题在这
viewer/      491         2 模块
thumbs/      342         2 模块   ← 名字有歧义
```

`browser` / `calendar` / `viewer` 是干净的：三个「页面」，依赖只朝一个方向，页面之间互不 import。
**这三个包不动。**

### 一、`panels/` 名不副实

它装的其实是五类不同的东西，而按仓库自己那条判据（AGENTS.md：「分组判据是『改了它会不会牵连
另一个包』」）逐个量，**它们之间一条牵连都不成立**：

| 模块 | 行数 | 实际是什么 | 消费者（实测） |
|---|---|---|---|
| `welcome` | 132 | **一个页面**（`PageRole.WELCOME`，进 `_stack`） | main_window, library_flow |
| `metadata` | 359 | **大图页右侧那一栏**（只进 `detail_layout`） | main_window, library_flow |
| `breadcrumb` | 268 | **网格页顶部那一行** + 条件胶囊 | main_window, **calendar/toolbar** |
| `metadata_edit` | 806 | **模态对话框** | write_ops |
| `event_edit` | 481 | **模态对话框** | window/event_flow |
| `layout` | 34 | Qt 拆控件的助手 | panels×3 + **calendar/toolbar** |

**四个页面（welcome / grid / calendar / detail）被拆散在三个包里**，而 `PageRole` 的四个值本来
是一一对应的。

### 二、`thumbs` 在仓库里有两个意思

- `ui/thumbs/` = **后台解码流水线**（生产）
- `ui/calendar/thumbs.py::ThumbStore` = **日历那一份图仓库**（消费，按 `(路径, 边长)` LRU）
- 第三份在 `ui/browser/model.py::_pixmaps`（网格那份，按内存字节 + 条数 LRU）—— **它连
  「thumbs」这个词都没有**

三份缓存三种淘汰策略。AGENTS.md 里那两条注意事项（「日历那份缩略图仓库不许因为换桶就清空」、
「换档不清空」）本质就是「它们不是一个东西」打出来的补丁。

### 三、三处按 0059 自己的判据该分而没分

1. `FilterState` / `FocusState` / `JobSlots` / `JobSpec` 仍与各自的 mixin 同住
   （`window/filters.py`、`window/focus.py`、`window/jobs.py`）。0059 决策 1 说的
   「依赖方向单向，分层之后在目录上看得见」在这一层还没建。
   **AGENTS.md 第 121 行因此与代码不一致** —— 它把 `filters` / `focus` 列在 `state/` 下。
   AGENTS.md 原话：「文档与代码不一致时，视为缺陷」。
2. `ShortcutColumnMenu`（`QMenu` 子类）在 `window/menu_style.py` 里，而 0059 决策 2 自己写的
   判据是「**QWidget 子类**与 mixin 是两种东西」，那四个控件因此进了 `widgets/`。
3. 顶层 `write_ops.py` 反向 import `window.menus` 与 `panels.metadata_edit` —— 这是 0059
   决策 4 的代价（那条守护按文件名找它），**记一笔，不动**。

### 四、0059 承诺的那条守护**不存在**

`8451df0` 的提交信息写着「新增守护 `test_ui_top_level_only_holds_shared_modules`……验过会红」，
0059 的后果里也列了它。实际：

- `git show 8451df0 --stat` 里**没有 `tests/test_guards.py`**
- 全仓库 `grep test_ui_top_level` → 0 处；`git log -S` 跨所有分支 → 0 处

**后果**：0059 的整个结论现在没有任何东西看着 —— 下一次有人加顶层模块，又回到「靠人记得」。

## 决策

### 1. 拆掉 `panels/`，每件按它真正的宿主走

| 现在 | 去处 | 判据 |
|---|---|---|
| `panels/layout.py` | **顶层** `layout.py` | 34 行，`calendar/toolbar.py` 也用 → 符合「顶层只许留跨包共享」 |
| `panels/breadcrumb.py` | `window/widgets/breadcrumb.py` | 窗口顶部那一行，与 `title_bar` 同级 |
| `panels/metadata.py` | `viewer/metadata.py` | 宿主是大图页，跟着 `detail.py` |
| `panels/metadata_edit.py`<br>`panels/event_edit.py` | 新包 `dialogs/` | 806 + 481 行，各够独立；两者都不碰文件，只把用户填的那份读成 dataclass 交出去 |
| `panels/welcome.py` | `panels` 的位置 → **`welcome.py`（顶层）** | 它是**页面**不是面板 |

**胶囊那一部分必须留一份共享的**：`build_chip` / `chip_style` / `ChipButton` 被
`calendar/toolbar.py` 用（ADR 0040 决策 9：两处摆胶囊的地方走同一个出口，否则过一阵就是
「日历上那几枚比网格上那几枚浅一档」）。搬 `breadcrumb` 时**最容易踩空的就是这里** ——
胶囊跟着搬走，条件就在日历上少了一排。

因此 `ChipButton` / `build_chip` / `chip_style` 留在**顶层** `chips.py`（两个包都用 → 跨包共享），
`window/widgets/breadcrumb.py` 只留 `BreadcrumbBar` 自己那部分。

### 2. `calendar/thumbs.py` → `calendar/thumb_store.py`

类名本来就叫 `ThumbStore`。改名之后 `thumbs` 在仓库里只指流水线那一个意思。
第三份缓存（`browser/model.py::_pixmaps`）**不搬**，只留一句指路。

### 3. 四个状态对象进 `window/state/`

`FilterState` / `FocusState` / `JobSlots` / `JobSpec`。**AGENTS.md 的清单同步改**。

### 4. `ShortcutColumnMenu` 进 `window/widgets/`

按 0059 决策 2 自己写的判据。`ShortcutColumns`（数据类）与样式解析留在 `menu_style.py`。

### 5. 补上 0059 承诺的那条守护

**顶层只许留跨包共享的模块** —— 判据是「有几个子包 import 它」。
三个例外，理由写在登记表里：

- `write_ops.py`：「只增不改」那条数据安全守护按文件名找它，搬了它就失效。
- `banners.py`：写操作结果的用户可见措辞，与窗口无关（`write_ops` 与 mixin 都调它）。
- `desktop.py`：访达与剪贴板桥，同上。

`app.py` / `main_window.py` 是**入口**，不进这条登记表（它们本来就不该被别人 import）。

### 6. 加第二条守护：`PageRole` 的四个值 ↔ 四个页面模块

拆完 `panels/` 之后，四个页面各住一处（`welcome` / `browser.grid` / `calendar.page` /
`viewer.detail`），而 `PageRole` 是**页面的唯一判据**（`state/level_stack.py`）。这两者一一对应
是可以在代码里断言的 —— 加一种页面而忘了建控件那条会红。

## 执行顺序

| 步 | 内容 | 为什么是这个顺序 |
|---|---|---|
| **0** | 补 0059 承诺的那条守护 | **后面每一步的判据来源**；不先补，搬完也没有东西看着 |
| **1** | `calendar/thumbs.py` → `thumb_store.py` | 一个文件，零风险，先把名字的歧义去掉 |
| **2** | 拆 `panels/`（五步纯移动，每件一次提交） | import 面最宽，但每一件都是纯移动 |
| **3** | 四个状态对象进 `window/state/` | 纯移动，**但 `tests/test_guards.py` 按文件名找 `filters.py` / `focus.py`**，路径要同步 |
| **4** | `ShortcutColumnMenu` 进 `window/widgets/` | 最后做，纯移动 |

**每一步单独提交**，每一步之后 `pytest` 与 `ruff` 必须干净。

## 后果

- `panels/` 这个包**不存在了**；四个页面各住一处，`PageRole` ↔ 页面模块一一对应且有回归测试。
- `thumbs` 在仓库里只有一个意思。
- AGENTS.md 的清单与代码一致（它此前把 `filters` / `focus` 错列在 `state/` 下）。
- `ui/` 顶层多两个（`layout` / `chips`），少一个包；顶层总数由 12 变 13，全部在登记表里。
- 顶层 `write_ops.py` → `dialogs/` 的反向依赖变成 `write_ops` → `dialogs`，与
  `write_ops` → `window.menus` 同类，**记在 `dialogs/__init__.py` 的文档里**。

## 被否决的方案

- **把四个页面合成一个 `pages/` 大包** —— `browser` / `calendar` / `viewer` 之间零 import、
  依赖单调，正是想要的样子；合起来只会让 6600 行挤进一个目录。
- **把三份缩略图缓存合并成一份** —— 三者的淘汰策略是**故意不同**的（网格按内存字节、
  日历按张数 × 档位），而 AGENTS.md 里那两条注意事项正是这些差别的后果。合并是行为变更，
  要单开一条 ADR 并逐条验那两条注意事项，本条不做。
- **动 `tests/ui/` 那 18 个 `*_flow.py`** —— 它们测的确实是跨包的整条链，
  硬塞进 `tests/ui/window/` 名字会骗人。
- **搬 `write_ops.py`** —— 同 0059 决策 4。
- **一次性全搬** —— 五步各自可验证、可回退；一次改三十个文件，出了问题定位不到是哪一步。
