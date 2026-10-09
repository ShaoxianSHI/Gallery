# ADR 0055：把 mixin 的宿主耦合面量出来

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0050（映射只有一个编排点）、0051（状态分轴）
- 相关代码：`tests/ui/window/test_host_contract.py`（新增）、
  `src/gallery/ui/window/`（各 flow mixin）、`src/gallery/ui/write_ops.py`

## 背景

`MainWindow` 继承 18 个 mixin，而它们之间**没有任何接口声明** —— 每一个 mixin 都在
`self.` 上用着别的 mixin 与 MainWindow 提供的东西，编译器一句都查不出来：

- `ui/window` 开 `mypy --strict`：**948 条**，其中 **863 条是 `[attr-defined]`**
  —— 「`OrderingMixin` has no attribute `_grid`」这一类；
- 剩下 85 条是**真的**类型问题（`QAction(text, self)` 那个父对象传错了、
  `has-type` 二十八条、`no-any-return` 十一条）。

`level_stack_mixin.py` 里那个 `_StackHost` Protocol 是这条路的**尝试**，写了之后
**一处也没用过** —— 用 Protocol 表达「mixin 需要宿主提供什么」要么给每个方法标注
`self`（441 个方法），要么走类型标注的类级声明（489 条，各带真实类型）。

而在做那件事之前，先有一件更便宜、也更能立刻兑现的事：**把耦合面量出来**。
这件事原先**没有任何地方写着**。

## 决策

### 1. 先量，不先改

`tests/ui/window/test_host_contract.py` 从 `MainWindow.__mro__` 取出真正继承的那 18 个
mixin（**不是扫全树** —— `PhotoGrid` 也有两个 mixin，而它们的宿主是网格），
逐个算出「用到的 `self.X` 里，哪些不是 Qt 基类给的、也不是自己定义的」。

结果是一张 489 行的表，按需求从多到少排：

| mixin | 宿主成员数 |
|---|---|
| `window/library_flow.py:LibraryFlowMixin` | 73 |
| `window/context_menu_flow.py:ContextMenuFlowMixin` | 56 |
| `window/ordering.py:OrderingMixin` | 45 |
| `window/navigation_flow.py:NavigationFlowMixin` | 44 |
| `window/calendar_flow.py:CalendarFlowMixin` | 39 |
| `write_ops.py:WriteOpsMixin` | 38 |
| `window/search_flow.py:SearchFlowMixin` | 35 |
| `window/filters.py:FiltersMixin` | **1** |
| `window/focus.py:FocusMixin` | **1** |

**合计 489。** 最深的那几间（`library_flow` 73、`context_menu_flow` 56）也正是最该定期
问一句「这一间还该存在吗」的地方 —— 对比之下 ADR 0052/0053 收完的那两根轴各只要 1 个：
**收完之后耦合面降到一个可数的程度**，这件事本身就是那两条的收益之一。

### 2. 三道回归测试

| 用例 | 拦什么 |
|---|---|
| `test_the_pinned_surface_matches_the_code` | 用了新的宿主成员而不更新表 —— 顺便把「新增的是哪几条」打出来 |
| `test_the_surface_does_not_grow` | 总量从 489 涨上去 |
| `test_the_widest_mixins_keep_their_ceiling` | 总量不变而**某一间独涨**（那正是「它该拆了」的信号） |

那四间的上限就是表里的实际值（73 / 56 / 45 / 44）。

**为什么不是 mypy**：它知道那些成员**被用到了**，不知道它们的**类型**，也不检查方法体。
换来的是「耦合面可见 + 不可静默增长」，而后者正是 mixin 那种写法唯一无法自证的东西。

**表里的 489 与 mypy 的 863 不是同一个数** —— 表数的是**不重复的成员**，
mypy 报的是**使用点**且按它自己的口径逐类去重（同一片依赖三处数出来三个数：
1290 个使用点 / 489 个成员 / 863 条错误）。**所以不做交叉断言** ——
那等于声称两个数必须相等，而它们本来就不该相等。基线那两个数字只回答
「比上次多了几条」。

**它自己必须验过一次**：往 `focus.py` 里注入一句 `self._grid.setCurrentRow(0)`，
第一条立刻红并打出 `新增 ['_grid']`。

### 3. Qt 基类那份清单**问 Qt 要**，不手抄

手抄一份的话，加一个 Qt 方法就会开始误报；而误报的回归测试最后一定是被
「顺手放宽判据」弄坏的 —— 那等于没有。判据是 `dir(QMainWindow) | dir(QWidget) |
dir(QObject)` 加四个 Qt 自己的属性（`parent` / `children` / `objectName` / `tr`）。

## 后果

- **489 条耦合第一次有了数字。** 它是往后所有「要不要拆一个 mixin」的判断依据。
- 那 85 条真类型问题**这一轮不修**（见下），但 `mypy --strict` 的基线（948 / 863）
  **钉进了回归测试**（带 `UI_WINDOW_MYPY_RECHECK=1` 时真跑一遍），
  于是「多了几条」有人问得到。
- 回归测试多 5 条，全部在 `tests/ui/window/test_host_contract.py`。

## 真正的类型检查为什么这一轮没做

工作量是量过的，不是估的：

1. **863 条 `[attr-defined]`** 要消掉，就得给 489 个宿主成员写上真实类型并声明在
   489 个地方（每个 mixin 声明自己要的那些）。这是本仓库里最大的一次类型标注。
2. **85 条真问题**里有一类值得单独说：`ordering.py` 两处
   `QAction(text, self)` —— `self` 在 mixin 里静态上是 `OrderingMixin`，
   而它在运行时是 `QMainWindow`。**那两行只有因为 MainWindow 是 QObject 才成立**，
   而这一点静态上完全看不出来 —— 这正是 mixin 那种写法藏起来的东西之一。

**先量后改**：489 条在表里锁着之后再补类型，改一处表就红一次，
不会出现「补到一半、不知道补全了没有」的状态。

## 被否决的方案

- **直接给 `self` 标注宿主 Protocol**（`def foo(self: _Host) -> None`）——
  441 个方法逐个改，而 `level_stack_mixin.py` 里那个 `_StackHost` 说明这条路
  上一次走了一半就停了。表先立起来，标注可以按表逐间做。
- **把表写在每个 mixin 模块里而不是测试里** —— 那 489 行会散进 18 个文件，
  而「一共有多大」这个问题恰恰需要一个能一眼看到的地方。
- **只报总数不钉明细** —— 明细才是能回答「多出来的这一条依赖合不合理」的东西。