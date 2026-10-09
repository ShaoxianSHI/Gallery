# ADR 0057：按表逐间补宿主声明 —— 第一间（`library_flow`）

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0055（宿主耦合面那张表）、0056（三个口径）
- 相关代码：`src/gallery/ui/window/library_flow.py`、`src/gallery/ui/window/ordering.py`、
  `src/gallery/ui/main_window.py`、`tests/ui/window/test_host_contract.py`

## 背景

ADR 0055 把 mixin 的宿主耦合面量成一张 489 条的表，并定了路线：**按表逐间补类型声明**。
ADR 0056 把三个 mypy 口径分清了，也把「组合根 0 条」这件事说准了。

这一轮做**第一间**：`library_flow.py`，表里 73 条。它是表里最宽的一间，
拿它开路是因为**最坏情况先试** —— 如果这条路在某一样东西上走不通，
换一间也一样走不通，早知道比晚知道好。

## 决策

### 1. 字段用类级注解，方法用 `TYPE_CHECKING` 桩 —— 两者不能互换

ADR 0055 说「489 条，各带真实类型」，但没说**怎么写**。实测下来分两种，混用会出事：

- **字段**（20 个）：类级注解，`_library: Library | None`。
- **方法**（31 个 + 4 个 Qt 方法）：`if TYPE_CHECKING:` 里的**方法桩**，签名逐字复刻真定义。

**为什么方法不能用类级 `Callable` 注解**：`Callable[..., None]` 与真定义对不上，
组合根立刻报 `Definition of "_show_banner" in base class "LibraryFlowMixin"
is incompatible with definition in base class "BannerFlowMixin"` ——
**正是 ADR 0056 刚修掉的那类多基类冲突，从另一个方向回来了**。

**桩必须逐字对**，包括三样容易漏的：

| 漏了什么 | 报什么 |
|---|---|
| `*`（关键字限定） | `_start_job` 与 `JobsMixin` 不一致 |
| `@property` | `_group_view` / `_current_dir` / `_drill_photos` 与 `LevelStackMixin` 不一致 |
| `setGeometry` 的**重载** | 与 `QWidget` 不一致（还得多写一个实现行，否则 `no-overload-impl`） |

### 2. 桩写错的后果是**当场报红**，不是悄悄放过

这是这套复制品能站住的关键。实测把 `_show_banner` 的 `text` 从 `str` 写成 `int`，
组合根立刻在三个调用点报 `arg-type`；写松的 `setGeometry` 则直接与 `QWidget` 冲突。
**桩与真定义是两处知识，而它们对不上时是吵的** —— 这与 ADR 0056 里
「注册表与字段是两处知识，少一个都红」是同一条理由。

### 3. 补声明时**找出两个真问题**

补声明不是纯搬运，它把两处**本来就该被发现**的问题顶了出来：

1. **`_sort_facet` 声明成 `str | None`，而它永远不是 `None`。**
   三处赋值都保证了非 `None`：初值是 `DEFAULT_SORT_FACET`、`set_sort_facet`
   对 `None` 直接 return、`resolved_sort_facet()` 交回的一定是可用值。
   而 `LibraryUIState.with_ordering` 的形参是 `sort_facet: str`（它的文档写着
   「要真字段 id：菜单里没有『不排序』」）—— 类型系统把这条不一致顶了出来。
   改成 `str`（三处声明同步）。

2. **`_session_base` 是 `Path | None`，不是 `Path`** —— 照着「窗口几何都存 `Path`」
   的印象写就会错。这个是声明写宽了被 mypy 顶回来，不是发现的 bug。

### 4. 4 处「`self` 当 `QWidget`/`QObject` 传」用 `cast`

ADR 0056 把这一族（当时 18 条）记为「留着」。这一间里它们已经**挡住了补完**，
所以顺手做了：`cast("QWidget", self)` / `cast("QObject", self)`，
写法与仓库既有cast 一致（`theme.py` / `app.py` / `search_field.py`）。

**为什么只能 cast**：静态上 `self` 是这个 mixin，运行时是 `MainWindow`。
要让它静态上也成立，得给每个方法标 `self: _Host`（441 个方法）——
ADR 0055 已经否决过一次（`level_stack_mixin.py` 里那个 `_StackHost` 写了一半就停了）。
cast 是**局部、显式、不新增耦合**的做法，代价是那 4 处的断言由 review 负责。

## 后果

- **`library_flow.py` 在 `--strict` 下一条不报**（116 → 0）。表里 73 条全部落成声明。
- `mypy --strict src/gallery/ui/window`：921 → **805**（`attr-defined` 869 → 762）。
- `mypy --strict src/gallery/ui`（全覆盖）：1225 → **1109**。
- 组合根仍然 0 条，且**没有因为这 100 多行声明而重新变脏** —— 这一点每一步都验过。
- 新守护 `test_the_finished_modules_stay_type_clean`：盯 `TYPED_MODULES` 清单里
  每一间都必须 0 条，**清单只增不减**。验过会红，两种注入：
  ① 删掉一个宿主字段声明；② 把一个桩的签名改错（同时也会顶红 mypy 基线那条）。
- ADR 0055 那张表**没有变**。这一轮没新增也没减少任何一条宿主依赖 ——
  变的只是「现在这些依赖在类型上说得清了」。

## 还剩什么

- **其余 17 间**：`context_menu_flow` 56 条、`ordering` 45、`navigation_flow` 44……
  同一套做法接着推即可，`TYPED_MODULES` 一间一间加。
- **`ui/window` 剩下的 762 条 `attr-defined`** 就是那 17 间，与本 ADR 无关。
- **18 条 `self` 当 `QWidget`**：这一间清了 4 条，其余 14 条分布在别的间，
  到那一间时同样用 cast 清掉。
- **`_current_row` 与网格当前项仍是两份记账**（ADR 0053 明确不修），与本轮无关。

## 被否决的方案

- **方法也用类级 `Callable` 注解**（读起来更短）—— 与真定义对不上，直接在组合根
  报多基类冲突。实测过。
- **给每个方法标 `self: _Host`** —— 441 个方法逐个改。ADR 0055 已否决，
  `_StackHost` 是这条路走过一半的痕迹。
- **让 mixin 继承一个宿主 Protocol** —— 实测能让那一间少报几条，但 Protocol 的成员
  同样是手抄的签名，且 `Protocol` 作基类会让 `MainWindow.__mro__` 里多出一层，
  与 ADR 0055 那张表「从 MRO 取 mixin」的判据打架。
- **把剩下 17 间一次性全做完** —— 每一间都可能冒出新问题（新发现的真 bug、
  与 Qt 的签名冲突）。一间一间做，改一条基线、验一次守护。