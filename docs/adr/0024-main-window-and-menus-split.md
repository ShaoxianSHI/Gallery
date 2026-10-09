# ADR 0024：`main_window.py` 与 `menus.py` 拆成四份，界面层按「答哪个问题」分家

- 状态：已接受
- 日期：2026-11-14
- 相关代码：`src/gallery/ui/main_window.py`、`src/gallery/ui/write_ops.py`、
  `src/gallery/ui/banners.py`、`src/gallery/ui/menus.py`、`src/gallery/ui/menu_style.py`
- 相关决策：[0006](0006-package-layout.md)（按领域分包）、[0014](0014-context-menu.md)（右键菜单）、
  [0016](0016-menu-stylesheet.md)（菜单样式表）

## 背景

两个文件在回答**好几个互不相干的问题**，而它们的判据（ADR 0006：改了它会不会牵连
另一个包）都被自己违反了：

- `menus.py` 1148 行，同时答「菜单里该有哪些条目」（产品行为，ADR 0014 / 0018 / 0021 /
  0023）与「菜单长什么样」（样式表、快捷键列、`ShortcutColumnMenu` 自绘的箭头与分隔线）。
  改一个分组顺序要读一整段 `paintEvent`，改一次圆角要去数 `_add_*` 有几处。
- `main_window.py` 3584 行。除了装配，还塞进了两个本可以独立的模块：
  七个**一行 Qt 都没有**的横幅措辞函数；以及 32 个（约 550 行）**会动用户文件**的入口。

后者尤其要命。约束 1 写着「绝不删除、移动用户照片，重命名是唯一被豁免的操作」——
但在三千多行的装配代码里，那句话只能靠人记。`grep rename_entry src/gallery/ui/` 的结果
散落在七个地方，没有一处能一眼看全。

## 决策

### 1. 界面层按「它答哪个问题」分家，全部留在 `ui/` 顶层

```
ui/
├── main_window.py   装配：建控件、连信号、管生命周期、管线程
├── write_ops.py     什么时候动用户文件（粘贴 / 新建 / 复制与移动 / 改名 / 废纸篓 / 撤销）
├── banners.py       动完之后横幅上那一句中文（无 Qt）
├── menus.py         菜单里该有哪些条目（产品行为）
└── menu_style.py    菜单长什么样（外观，与 theme.py 同类）
```

`menus.py` **没有**搬进 `browser/` 或 `viewer/`：菜单栏、右键菜单、菜单里的子菜单
**全都是同一副样子**，而那张菜单还被 `browser/` 与 `viewer/` 同时用到
（`viewer/` 那一张是 ADR 0021）。按判据它就该留在顶层 —— 与 `navigation.py` 同理。
之前看起来像位置选错，其实是**两个模块挤在一个文件里**。

### 2. `write_ops` 收走所有改用户文件的调用点，约束 1 变成回归测试

`Library` 上那几个真的会改文件的方法（`rename_entry` / `move_to_trash` / `copy_into` /
`move_into` / `create_album` / `undo_last_write`）**只许在 `write_ops.py` 里被调用**。
两条回归测试锁定它：那些方法只在那一个文件出现；`write_ops.py` 自己不许打开文件
（判据复用现有的「ui 不许 `open()`」那一份）。

### 3. 它是 mixin，依赖清单写进模块文档

`write_ops` 41 处 `self.`，其中约二十处是 `MainWindow` 自己的状态（`_grid` /
`_model` / `_library` / `_runner` / 各级缓存…）。抽成独立对象要把这些一个个改成构造
参数，那是一次**行为**改动；收成 mixin 只动「方法在哪个文件里」，调用点一个都不用改。
依赖清单写进文档，是为了不让它隐形。

### 4. `banners.py` 零 Qt，因此能单独测

那七个函数只把 `core` 的结果对象翻成一句话。留在 `main_window.py` 顶部时，测一句措辞
要造一个 `MainWindow` —— 要 Qt、要一个真的库、要把事件循环转起来，而「记账失败时那句
提示必须出现」是一条纯粹关于措辞的规矩。抽出来之后才有
`tests/ui/test_banners.py` 那十四条断言。

## 后果

- **约束 1 在代码里可查**：`grep -n "rename_entry\|move_to_trash\|copy_into" src/gallery/ui/`
  的结果全落在 `write_ops.py`，改坏的地方一定会让回归测试变红。
- **`main_window.py` 3584 → 2854 行**，剩下的是装配、生命周期、线程、信号 ——
  一份「窗口就该做的事」清单。段落标记（`# ---- XXX ----`）已逐段核对过实际内容，
  不再有几段指向空段落（拆完之后它们漂过一次，顺手改了）。
- **菜单那一半变好守了**：`menus` / `menu_style` / `desktop` 三个模块都不许 import core
  （ADR 0014 那条回归测试的名单从两个变三个）。一个**只画的**模块比一个既画又拼的模块更明显
  地不该去碰 core。
- **代价**：`write_ops` 是个 mixin，跨 mixin 调 `_show_banner()` / `_reload_level()` 这类
  共享方法在类型上看不出来（`mypy --strict` 只覆盖 `core/`）。用文档里的依赖清单补偿。
- `menus.py` 1148 → 492 行，`menu_style.py` 567 行 —— 都还在 `ui/` 顶层，但各答一个问题。

## 被否决的替代方案

- **把 `menus.py` 搬进 `browser/`**（网格是菜单的主场景）。否决：菜单栏与大图页的菜单
  都在用，搬进 `browser/` 会让 `viewer/` 反向依赖 `browser/`。
- **把 `write_ops` 抽成独立对象而不是 mixin**。否决：那是一次行为改动（41 处 `self.`
  要逐个改成构造参数与信号），而这次拆分的理由（约束 1 可查）已经达到了。
- **顺手把 `main_window` 剩下的按功能再拆**（扫描、监听、元数据面板、层级导航各有
  100~250 行）。没做：它们与装配耦合得同样紧，收益不如 `write_ops` 明确。
  留着，等下一次真的被绊到时再说。
