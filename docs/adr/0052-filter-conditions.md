# ADR 0052：筛选 / 检索这一族收成一根轴

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0051（状态不止一根轴，本条是第二根）、0040（筛选的第二个作用域）、0041（筛选不入栈但随帧存）
- 相关代码：`src/gallery/ui/window/state/filters.py`（`FilterState`，新增）与 `window/filters.py`
  （只读面）、`search_flow.py`、`navigation_flow.py`、`ordering.py`

## 背景

ADR 0051 把这一族点名为「**会漂移的下标**」：`_editing_condition` 是 `_conditions` 的**下标**，删掉第 `i` 枚之后后面全部要前移。

| 字段 | 写 | 读 | 横跨 |
|---|---|---|---|
| `_conditions` | 6 | 20 | 4 |
| `_editing_condition` | 7 | 12 | 3 |
| `_search_scope` | 3 | 6 | 3 |
| `_search_total` | 5 | 2 | 3 |

四个字段的写散在三个文件里，而真正的不变式只有一条，它现在**靠 `_on_condition_removed` 里那三行手工挪**：

```
editing == position        → 置 None（正在改的那一条没了）
editing is not None and > position → 减一（序号前移）
```

另外三处动条件的地方各写各的：`pop_last_condition` 与 `clear_search_conditions` 把编辑态置
`None`，而 `_leave_search_frame` 与 `_teardown_library` 还要顺手把 `_search_total` 也置 `None`。
**「动条件要连带做哪几件事」全靠人记**，漏掉的后果是静默的：`_editing_condition` 粘在旧下标上，
下一次回车就地替换掉**另一条**条件。

## 决策

### 1. 四个字段住进 `FilterState`，写入口是它的方法

`FilterState` 是纯 Python（不碰 Qt），`FiltersMixin` 只提供四个**只读**属性与派生判据。**属性只读**：
留着 setter 就等于把 ADR 0051 决策 4 那一刀架空 —— 写入口必须是具名的方法，而 `self._x = …` 不是方法。

### 2. 「挪下标」只有 `remove(position)` 一处

其余三处（`pop_last` / `clear` / `submit`）各自该连带做什么是一次性的，现在写在各自的方法里：

| 入口 | 连带做的事 | 为什么 |
|---|---|---|
| `submit` | 清编辑态 | 回车提交完就没有「正在改的那一条」了 |
| `remove(position)` | **挪编辑态下标** | 唯一会改变后面那些条件下标的地方 |
| `pop_last` | 清编辑态 | 撤的是最后一条，不可能是正在改的那条 |
| `clear` | 清编辑态 + 清命中数 | 三处调用点（弹搜索帧 / 清搜索框 / 换库）原本都写这两句 |

`restore(conditions)` 是第四个入口：**装回某一帧自己那份筛选**（ADR 0041 决策 2），不碰编辑态与命中数。

### 3. 派生判据跟着字段走

`_is_library_search` 与 `_is_filtering` 从 `SearchFlowMixin` 搬进这一族：它们问的正是这四个字段的
函数，而**全窗口只该有这一处**（七个判据各自问一次 `self._conditions` 是 ADR 0040 决策 1 的原话）。

### 4. 回归测试

`tests/ui/window/test_filters.py` 钉结构（写入口与下标的三种归宿），`tests/test_guards.py` 加一条扫
`ui/` 的**形状**回归：四个字段的赋值与改长度只许出现在 `window/filters.py`，**这条守护自己必须验过一次**。

## 后果

- **写入口从 21 处收成 4 个方法名**，而属性变成只读之后，任何还想 `self._conditions = …` 的地方会
  当场 `AttributeError` 而不是悄悄改掉 —— 这正是本条要的。

## 被否决的方案

- **给四个属性加 setter** —— 写入口必须具名（决策 1）。
- **把 `_editing_condition` 换成「正在改的那一条本身」**（存 `SearchCondition` 而不是下标）—— 面包屑
  与日历工具条那两处要**位置**（`editing=position` 传给控件画「正在改这一枚」），换成对象就得多一次反查。
- **把 `_search_total` 拆出去**（只显示一个数字）—— 它与条件同生共死：条件清空时命中数必须跟着清，否则面包屑会写「搜索结果（128）」而其实一条都没有。