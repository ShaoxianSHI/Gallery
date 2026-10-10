# ADR 0024：`main_window.py` 与 `menus.py` 拆成四份，界面层按「答哪个问题」分家

- 状态：已接受
- 日期：2026-11-14
- 相关代码：`src/gallery/ui/main_window.py`、`src/gallery/ui/write_ops.py`、`src/gallery/ui/banners.py`、
  `src/gallery/ui/window/menus.py`、`src/gallery/ui/window/menu_style.py`
- 相关决策：[0006](0006-package-layout.md)（按领域分包）、[0014](0014-context-menu.md)（右键菜单）、
  [0016](0016-menu-stylesheet.md)（菜单样式表）

## 背景

两个文件在回答**好几个互不相干的问题**，而判据（ADR 0006：改了它会不会牵连另一个包）被它们
自己违反了。`menus.py` 1148 行，同时答「菜单里该有哪些条目」（产品行为，ADR 0014 / 0018 /
0021 / 0023）与「菜单长什么样」（样式表、快捷键列、`ShortcutColumnMenu`）；`main_window.py`
3584 行，除了装配还塞进七个**一行 Qt 都没有**的横幅措辞函数与 32 个（约 550 行）**会动用户
文件**的入口。约束 1（重命名是唯一被豁免的操作）在三千多行的装配代码里只能靠人记 ——
`grep rename_entry src/gallery/ui/` 的结果散落在七个地方。

## 决策

### 1. 界面层按「它答哪个问题」分家，全部留在 `ui/` 顶层

`main_window.py` 只做装配，`write_ops.py` 答「什么时候动用户文件」，`banners.py` 答「动完之后横幅上
那一句中文」（无 Qt），`menus.py` 答「菜单里该有哪些条目」，`menu_style.py` 答「菜单长什么样」。

`menus.py` **没有**搬进 `browser/` 或 `viewer/`：菜单栏、右键菜单、子菜单**全都是同一副样子**，
而那张菜单还被两个包同时用到（`viewer/` 那一张是 ADR 0021）—— 按判据它就该留在顶层。

### 2. `write_ops` 收走所有改用户文件的调用点，约束 1 变成回归测试

`Library` 上那几个真的会改文件的方法（`rename_entry` / `move_to_trash` / `copy_into` /
`move_into` / `create_album` / `undo_last_write`）**只许在 `write_ops.py` 里被调用**。两条回归
测试锁定它：那些方法只在那一个文件出现；`write_ops.py` 自己不许打开文件（复用「ui 不许
`open()`」那份判据）。

### 3. 它是 mixin，依赖清单写进模块文档

`write_ops` 41 处 `self.`，其中约二十处是 `MainWindow` 自己的状态（`_grid` / `_model` /
`_library` / 各级缓存…）。抽成独立对象是一次**行为**改动，收成 mixin 只动「方法在哪个文件里」。

### 4. `banners.py` 零 Qt，因此能单独测

那七个函数只把 `core` 的结果对象翻成一句话。留在 `main_window.py` 顶部时，测一句措辞要造一个
`MainWindow`，而「记账失败时那句提示必须出现」是一条纯粹关于措辞的规矩 —— 抽出来才有
`tests/ui/test_banners.py` 那十四条断言。

## 后果

- **约束 1 在代码里可查**：`grep -n "rename_entry\|move_to_trash\|copy_into" src/gallery/ui/`
  的结果全落在 `write_ops.py`，改坏的地方一定会让回归测试变红。
- **`main_window.py` 3584 → 2854 行**，剩下的是装配、生命周期、线程、信号；`menus.py`
  1148 → 492 行，`menu_style.py` 567 行 —— 都还在 `ui/` 顶层，但各答一个问题。
- **菜单那一半变好守了**：`menus` / `menu_style` / `desktop` 都不许 import core（ADR 0014 那条
  回归测试的名单从两个变三个）—— **只画的**模块比既画又拼的更不该碰 core。
- **代价**：`write_ops` 是个 mixin，跨 mixin 调共享方法在类型上看不出来（`mypy --strict` 只覆盖
  `core/`），靠文档里的依赖清单补偿。

## 被否决的替代方案

- **把 `menus.py` 搬进 `browser/`** —— 菜单栏与大图页的菜单都在用，搬进去会让 `viewer/` 反向
  依赖 `browser/`。
- **把 `write_ops` 抽成独立对象而不是 mixin** —— 那是一次行为改动（41 处 `self.` 要逐个改成
  构造参数与信号），而这次拆分的理由（约束 1 可查）已经达到了。
- **顺手把 `main_window` 剩下的按功能再拆**（扫描、监听、元数据面板、层级导航）：耦合同样紧，收益不如 `write_ops` 明确。
