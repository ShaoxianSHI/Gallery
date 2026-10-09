# ADR 0052：筛选 / 检索这一族收成一根轴

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0051（状态不止一根轴，本条是第二根）、0040（筛选的第二个作用域）、
  0041（筛选不入栈但随帧存）
- 相关代码：`src/gallery/ui/window/filters.py`（新增）、
  `src/gallery/ui/window/search_flow.py`、`navigation_flow.py`、`library_flow.py`、`ordering.py`

## 背景

ADR 0051 把这一族点名为「**会漂移的下标**」：`_editing_condition` 是 `_conditions` 的**下标**，
删掉第 `i` 枚条件之后后面全部要前移。四个字段的写散在三个文件里：

| 字段 | 写 | 读 | 横跨 |
|---|---|---|---|
| `_conditions` | 6 | 20 | 4 |
| `_editing_condition` | 7 | 12 | 3 |
| `_search_scope` | 3 | 6 | 3 |
| `_search_total` | 5 | 2 | 3 |

真正的不变式只有一条，而它现在**靠 `_on_condition_removed` 里那三行手工挪**：

```
editing == position        → 置 None（正在改的那一条没了）
editing is not None and > position → 减一（序号前移）
```

而另外三处动条件的地方各写各的：`pop_last_condition` 与 `clear_search_conditions` 顺手把
编辑态置 `None`（对 —— 撤的不是正在改的那一条时不该置，可它们分不清，于是无条件置了），
`_leave_search_frame` 与 `_teardown_library` 还要顺手把 `_search_total` 也置 `None`。
**「动条件要连带做哪几件事」是一份靠人记的知识**，而漏掉的后果是静默的：`_editing_condition`
粘在旧下标上，下一次回车就地替换掉**另一条**条件，而界面上只表现为「刚敲的那条没生效」。

## 决策

### 1. 四个字段住进 `FilterState`，写入口是它的方法

`FilterState` 是纯 Python（不碰 Qt），`FiltersMixin` 只提供四个**只读**属性与派生判据。
**属性只读**：留着 setter 就等于把 ADR 0051 决策 4 那一刀架空 —— 写入口必须是具名的方法，
而 `self._x = …` 不是方法。

四个只读属性是给读点与测试留的（44 处读一个字都不改），**写点全部改成方法调用**。

### 2. 「挪下标」只有 `remove(position)` 一处

其余三处（`pop_last` / `clear` / `submit`）各自该连带做什么是一次性的，现在写在各自的方法里：

| 入口 | 连带做的事 | 为什么 |
|---|---|---|
| `submit` | 清编辑态 | 回车提交完就没有「正在改的那一条」了 |
| `remove(position)` | **挪编辑态下标** | 唯一会改变后面那些条件下标的地方 |
| `pop_last` | 清编辑态 | 撤的是最后一条，不可能是正在改的那条 |
| `clear` | 清编辑态 + 清命中数 | 三处调用点（弹搜索帧 / 清搜索框 / 换库）原本都写这两句 |

`restore(conditions)` 是第四个入口：**装回某一帧自己那份筛选**（ADR 0041 决策 2），
它整体换掉列表，因而不碰编辑态与命中数 —— 原本也是这么写的。

### 3. 派生判据跟着字段走

`_is_library_search` 与 `_is_filtering` 从 `SearchFlowMixin` 搬进这一族：它们问的正是
「有没有条件」加「作用域是哪一头」，是这四个字段的函数。两个方法各只有一句，
而**全窗口只该有这一处**（原来七个判据各自问一次 `self._conditions` 是 ADR 0040 决策 1 的原话）。

### 4. 回归测试

`tests/ui/window/test_filters.py` 钉结构（哪几条写入口、编辑态下标的三种归宿），
`tests/test_guards.py` 加一条扫全 `ui/` 的**形状**回归：四个字段的赋值与改长度只许出现在
`window/filters.py`。**这一条自己必须验过一次**。

原有的 `tests/ui/test_filter_flow.py::test_only_three_things_can_take_a_condition_away`
把「三处改动条件的写法」列成一张按源码行写的清单。那张清单**原样搬到新模块**，
判据不变（仍是「只有那几件能带走一条条件」），只是位置从三个文件收成一处。

## 后果

- **`main_window.py` 少四行声明**，多一句 `_init_filters()`。
- `search_flow.py` 的 `_on_condition_removed` 从 12 行降到 4 行（挪下标那三行进了轴）。
- **写入口从 21 处收成 4 个方法名。**
- 属性变成只读之后，任何还想 `self._conditions = …` 的地方会当场 `AttributeError` 而不是
  悄悄改掉 —— 这正是本条要的。
- 测试里那 4 处直接赋值（`test_theme` 两处、`test_calendar_flow` 一处、`test_filter_flow` 一处）
  改成调轴的方法；**它们量的是同一件事**，行为不变。

## 被否决的方案

- **给四个属性加 setter** —— 写入口必须具名（决策 1）。
- **把 `_editing_condition` 换成「正在改的那一条本身」**（存 `SearchCondition` 而不是下标）——
  看着更安全，但面包屑与日历工具条那两处要的是**位置**（`editing=position` 传给控件，
  控件自己画「正在改这一枚」的记号），换成对象就得多一次反查那条现在在哪。
- **把 `_search_total` 拆出去**（它只在面包屑上显示一个数字）—— 它与条件同生共死：
  条件清空时命中数必须跟着清，否则面包屑会写「搜索结果（128）」而其实一条都没有。