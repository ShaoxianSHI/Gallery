# ADR 0056：界面层的类型问题，以及「那 85 条」与三个口径

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0055（宿主耦合面那张表）、0051（后台任务那一根轴）
- 相关代码：`src/gallery/ui/window/jobs.py`、`src/gallery/ui/window/state/`、`src/gallery/ui/write_ops.py`、`tests/ui/window/test_host_contract.py`

## 背景

ADR 0055 量完宿主耦合面之后说「真正的类型检查还欠着」，并给了个数字：`mypy --strict src/gallery/ui/window` 是
**948 条**，其中 **863 条 `[attr-defined]`**，剩下 85 条是「真的」类型问题。**那 85 条里混着三种东西**，而它们要修的地
方完全不同 —— 动手之前得先说清哪个口径在数什么。

### 三个口径，各自数的是不同的东西

| 口径 | 命令 | 数的是 |
|---|---|---|
| **逐个 mixin** | `mypy --strict src/gallery/ui/window` | 21 个文件**各自**当目标。mixin 看不到别人，于是 `self._grid` 全都是 `attr-defined` |
| **组合根** | `mypy --strict src/gallery/ui/main_window.py` | **只有 `main_window.py` 自己**当目标 |
| **全覆盖** | `mypy --strict src/gallery/ui` | 73 个文件全是目标 —— 每一条都真的报出来 |

**组合根那个口径有个必须说清的性质**：mypy 只报**目标文件**里的错，被 import 的模块（全部 18 个 mixin）只是被跟进来做
类型推导，**它们身上的错一条都不报** —— 在 `filters.py` 里故意注入一条 `return "x"` 给 `-> int` 的错，
`mypy src/gallery/ui/main_window.py --strict` 仍然说 `Success`。

**所以「组合根 0 条」证明的是**：`main_window.py` 自己那 25 条真错（12 个多基类字段定义冲突 + 12 个赋值冲突 + 1 个
`union-attr`）修干净了，**那一层再也组装不起来**；它**不**证明 mixin 的函数体干净 —— 那是全覆盖口径的事（还有 1225 
条）。

### 那 85 条的真实成分

| 条数 | 是什么 | 是不是真问题 |
|---|---|---|
| 25 | 组合根报的那些（字段定义冲突 + 一个 `union-attr`） | **真**，已修 |
| ~52 | `has-type` / `no-any-return` / `unreachable` / `var-annotated` | **不是** —— 它们是 `attr-defined` 的下游：字段看不见 → 类型推不出来 → 「Cannot determine type of X」 |
| 18 | `self` 传给 `QMessageBox` / `QFileDialog` / `QAction(text, self)` | **真**，但要改十几处调用点，留着（见后果） |
| 6 | `Unused "type: ignore"` | **不是死的** —— 给 `_level_stack` 补上声明后，其中一条立刻变成「需要」。删掉等于给下一轮埋坑 |

## 决策

### 1. 字段声明提到**类级别**

`JobsMixin` 的 13 个任务槽与另外 13 个字段，声明从方法里提到类级别（只声明、不赋值，`_init_jobs` 仍然置空）。
**理由就一句**：声明写在方法里时 mypy 会把方法体内**第一次赋值**当成定义，而那些赋值散在别人的回调末尾 ——
`library_flow.py:160` 先写 `self._library = library`，mypy 就认定 `LibraryFlowMixin._library` 是 `Library`，而
`MainWindow.__init__` 里写的是 `self._library: Library | None = None`，于是组装时报
`Definition of "_library" in base class "LibraryFlowMixin" is incompatible`。**13 个 `*_job` 尤其荒唐**：七个 mixin 各
自在**回调末尾**写 `self._scan_job = None`（那是任务自己跑完把句柄放掉，ADR 0051 明确说过刻意留着），mypy 把那 28 句里
的**第一句**当成了「这个 mixin 把它定义成了 `None`」。这一改不是为 mypy 做的妥协 —— 它本来就该那样：
**类型由一处说了算**，而不是「谁先赋值谁定义」。

### 2. 组合根 25 → 0

| 条数 | 是什么 | 怎么修的 |
|---|---|---|
| 12 `misc` | 同一字段在两个基类里类型不一致 | 类级别声明（决策 1） |
| 12 `assignment` | `MainWindow.__init__` 的标注比 mixin 第一次赋值宽 | 同上 |
| 1 `union-attr` | **真 bug** | `flattened_entries` 返回类型改窄 |

### 3. 一条真的签名在说谎

`write_ops.flattened_entries()` 声明返回 `list[GridEntry]`，而它**运行时已经把 `GroupRef` 换成组员了**（函数体里就是
`entry.photos if isinstance(entry, GroupRef) else (entry,)`）。于是 `event_flow.py` 里 `entry.path` 在类型上不安全 ——
而那里全都直接取 `.path`。**签名在说谎，而签名的读者会照着它写代码**：改成 `list[PhotoRef | FolderRef]`。

### 4. 两条我自己在 ADR 0052 里写错的标注

- `FilterState.identities()` 声明 `-> list[str]`，而 `SearchCondition.identity` 返回的是 `tuple[str, str]`（字段 id + 原文），`level_key()` 的形参也正是 `Sequence[tuple[str, str]]`。
- `FilterState.restore()` 收 `tuple[object, ...]` 却往 `list[SearchCondition]` 里塞。

**运行时不报错**（`LevelKey = tuple[str, ...]` 照单全收），但它们一接上类型检查就是两条红。

### 5. 口径写进回归测试，但**不越界**

- `test_the_composition_root_is_type_clean`：盯**组合根必须 0 条**（默认跳过，`UI_WINDOW_MYPY_RECHECK=1` 时真跑）。**它守的是「那一层组装得起来」，不是「mixin 干净」。**
- `test_the_mypy_baseline_is_still_current`：锁定逐个 mixin 那个口径（921 / 869）。

**这条守护验过会红**：把 `_group_facet` 标成 `int`（与 `OrderingMixin` 声明的 `str | None` 冲突 —— 正是它要抓的那一
类），以及在 `MainWindow` 类体里放一条 `return "x"` 给 `-> int`。两次都变红。

## 后果

- **组合根在 `--strict` 下一条不报**，那一层再也组装不起来 —— 这是真的可查证据。
- **但别把它当成「界面层干净了」**：mixin 函数体里的错只有全覆盖口径才看得见，`mypy --strict src/gallery/ui` 现在是 **1225**（改之前 1279），其中 1087 条 `attr-defined` 仍然是 ADR 0055 那 489 条声明没做的直接后果；逐个 mixin 那个口径 948 → **921**。
- 18 条 `self` 当 `QWidget` 传给 Qt 的**留着**：运行时靠「`MainWindow` 是 `QObject`」成立，要静态上也成立得改那十几处调用点（`self` → 具名的 `self._as_widget()`，或让 mixin 声明宿主是 `QMainWindow`）—— 与 489 条声明是同一件活。

## 被否决的方案

- **把「组合根 0 条」写成「界面层没有类型问题了」** —— 是假的：mypy 不报被跟进模块的错，在 mixin 里注入一条真错它照样 `Success`。话说过头比不说更坏。
- **删掉那 6 条 `Unused "type: ignore"`** —— 「没用上」不等于「不需要」：它只是因为字段看不见、那条错压根没被报出来而已。
- **逐个 mixin 补齐 489 条宿主声明** —— 那是下一步（ADR 0055 已经量过与锁住），不是这一轮。
- **给 `attr-defined` 加 `# type: ignore`** —— 那 869 条每一条都加的话，等于把「这个模块和窗口耦合多深」这件事从代码里彻底抹掉，而 ADR 0055 刚刚把它量了出来。
- **只在 `pyproject.toml` 里把 `ui/` 加进 `mypy`** —— 会当场红 1225 条，而加进去的前提是逐个 mixin 声明宿主，那是下一轮的事。