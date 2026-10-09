# ADR 0059：界面层的目录结构 —— 按「谁牵连谁」重排

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0006（包布局）、0058（状态归位）
- 相关代码：`src/gallery/ui/` 顶层与 `src/gallery/ui/window/`

## 背景

ADR 0058 把状态收进了一组对象，于是 `ui/window/` 这个包的**身份变了**：
它现在同时装着两种不同的东西，而 AGENTS.md 的清单还只写「MainWindow 的各个 mixin」。

```
mixin（行为切片） 18 个
状态对象        8 个   ← ADR 0058 这一轮加的
```

同时 `ui/` 顶层有 20 个 `.py`，其中**一半只被 `window/` 用**。

## 判据：仓库自己那条

AGENTS.md 早就写着两条：

> 新模块先判断它属于哪个领域包；**只有确实跨领域共享的才放顶层**。
> 分组判据是「**改了它会不会牵连另一个包**」，不是文件大小。

本条只是**把这两条执行一遍**。下面每个判断都是量出来的，不是看出来的。

## 实测：谁牵连谁

「消费者」= 谁 `import` 它（`ui/` 内部）：

| 顶层模块 | 消费者 | 判据 |
|---|---|---|
| `imaging` | browser, calendar, thumbs, window | 跨包共享 → 顶层对 |
| `theme` | calendar, panels, 顶层 | 跨包共享 → 顶层对 |
| `scrollbar_style` | browser, calendar, panels | 跨包共享 → 顶层对 |
| `outline` | panels, 顶层 | 跨包共享 → 顶层对 |
| `rename_edit` | browser, 顶层 | 跨包共享 → 顶层对 |
| `tasks` | thumbs, window, 顶层 | 跨包共享 → 顶层对 |
| `banner_overlay` | window | 只被窗口用 |
| `external_button` | window | 只被窗口用 |
| `menu_style` | window | 只被窗口用 |
| `menus` | window | 只被窗口用 |
| `navigation` | window | 只被窗口用 |
| `search_field` | window | 只被窗口用 |
| `title_bar` | window | 只被窗口用 |
| `watch` | window | 只被窗口用 |
| `write_ops` | window | 只被窗口用 —— **但它不动，见下** |

## 决策

### 1. `window/state/`：状态对象与 mixin 分开

**理由不是「文件太多」** —— 是**依赖方向单向**：mixin 依赖状态对象，从不反过来。
分层之后这条方向在目录结构上就看得见；现在它只存在于 import 语句里，
而 8 个状态对象是 ADR 0058 才加进来的，目录结构还没跟上。

### 2. `window/widgets/`：四个控件从 mixin 里分出来

`banner_overlay` / `external_button` / `search_field` / `title_bar` 是 **QWidget 子类**，
与「MainWindow 的 mixin」是两种东西。它们只被窗口用，所以进 `window/`；
但不该和 mixin 混在一起，所以再往下一层。

### 3. `menu_style` / `menus` / `navigation` / `watch` 进 `window/`

只被窗口用、且不是控件。

### 4. `write_ops.py` **留在原地不动**

AGENTS.md 明写：

> `write_ops.py` 留在原地不动。`tests/test_guards.py` 是**按文件名**找它的，
> 把它改成包会让「只增不改」那条回归测试悄悄失效。

**「只增不改」是数据安全红线，而那条守护是按文件名找的。** 这一条压过所有整齐的考虑 ——
一个会悄悄失效的安全守护比一个放在错位置的模块糟得多。

## 三步的执行顺序

| 步 | 内容 | 为什么是这个顺序 |
|---|---|---|
| **1** | `window/state/` | 纯移动，风险最低，且**决定了后面两步的落点** |
| **2** | `window/widgets/` | 控件，四步里 import 面最宽 |
| **3** | 其余四个进 `window/` | 最后做，此时 `window/` 的形状已定 |

**每一步单独提交**，每一步之后 `pytest` 与 `ruff` 必须干净。

## 后果

- `ui/` 顶层从 20 个 `.py` 降到 **11 个**（移走 9 个）。
- `window/` 从「两种东西平铺」变成 `state/`（8）+ `widgets/`（4）+ 18 个 mixin，
  **每一种的职责在目录上就分得开**。
- **AGENTS.md 的模块清单要同步** —— 它本来就是索引，而索引漏了两次模块
  （`core/library/search.py`、`ui/search_field.py` 那一次，还有后来的四个）。
  清单与代码不一致**视为缺陷**（AGENTS.md 原话）。
- 新增一条守护：**顶层只许留跨包共享的模块** —— 判据就是上面那张消费者表。
  少一个顶层模块（有人从子包 import 它）就红。

## 被否决的方案

- **把 `write_ops.py` 也搬进去** —— 「只增不改」那条守护按文件名找它，搬了它就失效。
- **按「每个 mixin 一个子包」重排 `window/`** —— 18 个目录，深三层。判据是
  「改了它会不会牵连另一个包」，而这 18 个之间**互相牵连得厉害**（宿主耦合面 457 条），
  按 mixin 分包等于把「它们是一家人」这件事拆散。
- **一次性全搬** —— 三步各自可验证、可回退；一次改三十个文件，出了问题定位不到是哪一步。