# ADR 0058：状态归位 —— 把「一份状态一个主人」推广到整棵窗口树

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0041（层级栈，唯一来源）、0050（`_sync_ui` 单一编排点）、
  0051（后台任务那根轴）、0055（宿主耦合面那张表）、0057（逐间补声明）
- 相关代码：`src/gallery/ui/window/jobs.py`、`src/gallery/ui/main_window.py`、
  `src/gallery/ui/window/ordering.py` 及各 flow mixin

## 背景

ADR 0057 把 `library_flow` 一间的宿主声明补完了，也暴露出真正的问题：**那 73 条宿主成员
不是一个一个独立的需求，而是「19 个切片共享同一袋状态」的表征。**

实测（只算 `MainWindow` 这一棵树）：

```
共写 135 个字段
  ├─ 99 个单写者（有主人）    73%
  └─ 36 个多写者（无主）      27%   ← 问题全在这
```

那 36 个**已经自己聚成几簇**，不需要设计：

| 簇 | 字段数 | 写者 |
|---|---|---|
| 后台任务槽 | **11** | `jobs` + 恰好一个 flow |
| 排序选择 + 重排缓存 | **9** | `main_window` + `ordering`（+ `library_flow`） |
| 当前打开的库 | 4~5 | `library_flow` + `main_window` |
| `_staged` | 1 | **7 个**（最病） |
| 拖窗口 | 2 | `main_window` + `title_bar_flow` |
| 零散 | 3 | — |

## 这个仓库已经做过一次正确的版本

ADR 0041 把「我在哪一层」的 **11 个字段退成了投影属性**
（`_browse_mode` / `_current_dir` / `_group_view` / `_drill_photos` / `_calendar_frame` /
`_drill_label` / `_from_calendar` / `_search_frame` / `_is_in_search_frame` /
`_photo_frame` / `_page_role`），唯一的存储是那个不可变的 `self._level_stack`。

**本条不是发明新形状，是把那次已经验证过的做法推广到其余的簇。**

## 目标结构

```
MainWindow          只做四件事：搭界面、接线、Qt 覆写、closeEvent
│
├── ui/             控件引用集中一处
├── state/          状态树 —— 判据：每一份状态有且只有一个主人
│   ├── level_stack : LevelStack          ← 根（ADR 0041 已完成）
│   │   └── 投影（凡离开「当前这一层」就没意义的）
│   │       ├── filters  : FilterState    ✅ 已是对象
│   │       ├── focus    : FocusState     ✅
│   │       ├── ordering : OrderingState  ❌ 阶段 2
│   │       └── staged   : Staging       ✅ 阶段 5（假设被否，见下）
│   ├── opening : LibrarySlot             ❌ 阶段 3
│   ├── session : SessionState            ❌ 阶段 3
│   └── jobs    : JobSlots                ❌ 阶段 1
├── models/         photo_model : PhotoListModel
├── services/       library : core.Library · thumbs : ThumbnailPipeline · watch : WatchBridge
└── flows/          19 个用例切片 —— 不直接写字段，写必经主人
```

### 每层的规则

| 层 | 规则 |
|---|---|
| `state/` | 一份状态一个主人；其余全是投影 |
| `services/` | 重活在这儿，界面不碰文件（既有分层不动） |
| `flows/` | **不直接写字段**：`self._sort_facet = x` 改成 `self._ordering.set_facet(x)` |
| `ui/` | 控件引用只在这一层 |

第三条是关键：**flow 不是不能写状态，而是不能伸手进别人的状态袋。**
`ordering.set_sort_facet()` 照旧是 flow 的方法，变的只是它**写哪**。

## 七个阶段

| 阶段 | 内容 | 性质 | 风险 | 消掉的无主字段 |
|---|---|---|---|---|
| **1** | `JobSlots` | 纯重构 | 低 | 11 |
| **2** | `OrderingState` | 纯重构 | 低 | 9 |
| **3** | `opening` / `session` | 纯重构 | 低 | 8 |
| **4** | flows 改成经主人写 | 纯重构，触碰最广 | 中 | — |
| **5** | `_staged` 每帧一份 | **行为变更**，独立 ADR | 高 | 1（但消掉最病的） |
| **6** | `ui/` 集中控件引用 | 触碰测试最多 | 中 | 约 59 条契约 |
| **7** | 筛选双份合一 | **行为变更** | 高 | — |

**阶段 1~4 合计消掉 35 个无主字段**，且全是纯重构 —— 不改任何行为，可以随时停。

### 阶段 5 的假设被实测否掉了

原计划把 `_staged` 改成**每帧一份**（暂存属于它那一层）。动手前逐个看了七处写入点，
发现**那个前提不成立**：

```
扫描 / 收窄产出条目  →  _staged = [...]
排序算完顺序        →  _reorder_level 取走它并置 None
```

七个写入点是**同一条链上的先后步骤**，不是「每个切片以为自己在动自己那一层」。
界面是单线程的，一次只有一遍扫描在跑 —— 它们从来不会互相覆盖。

所以阶段 5 **不做那个行为变更**，改成 `Staging` 对象（`stage` / `take` / `peek` /
`extend` / `is_staging`），把这一格的**手递手两端写成具名方法** ——
「谁交给谁」在代码里看得见，而行为一字未改。

**一条没有证据支撑的「改进」就是行为变更**，而行为变更要的是另一套论证
（症状是什么、怎么复现、改完怎么验）。这一轮两样都没有。

### 阶段 1 顺带推翻的一条既有决策

ADR 0051 当初否决 `_finish_job` 的理由写的是「不换来任何东西」。**那个理由不成立了** ——
在「一份状态一个主人」这个目标下，它换来的是「`JobSlots` 是那 13 个字段的唯一写者」。

回调末尾的 `self._x_job = None` 因此改成经主人放掉（`self._finish_job("_scan_job")`）。

### 明确不碰的

- `write_ops.py` 仍是唯一能动用户文件的地方 —— **不拆成通用 `FileService`**
- `core/` 与 `ui/` 的分层不动 —— 不把索引 / EXIF 搬上来
- **不要 `AppSignals` 信号总线** —— 与 ADR 0051 决策 3 冲突（刷新时机仍手工选，
  `_sync_ui` 仍是唯一编排点）
- Qt 覆写留在 `MainWindow`（Qt 按 MRO 找第一个实现）
- **字段名一个字不改** —— `tests/ui/waiting.py` 有 59 处 `getattr(window, name)`，
  测试里另有 56 处直接摸 `window._scan_job`

## 过渡层：`MainWindow` 上保留同名 property

每个轴收成对象之后，`window._scan_job` 这类名字**不能消失**（测试与下游都摸它们）。
做法是在 `MainWindow` 上留一个 property 转发到轴对象 —— 状态真的搬走了，
property 只是**不破坏既有测试面**的过渡层，不是「形式上的解耦」：
`JobSlots` 内部只有一份存储，转发不产生第二份。

## 阶段 6 与 7：实测之后，两条原计划都不做

### 阶段 6（`ui/` 集中控件引用）—— 不做，理由是代价不对等

原计划把 59 条控件引用收进一个 `ui` 对象。动手前量了代价：`tests/` 里**265 处**
`window._grid`、**286 处** `window._library`、**86 处** `window._banner` 直接摸控件。

换来的是契约表少 59 条，付出的是几百处测试改动加一次「窗口控件全部要转发」的机械活。
**而控件引用本来就有主人**（`MainWindow.__init__` 建、那一处持有），它们不是无主状态 ——
契约表大是因为「这一间要用到哪些控件」这件事本来就长，逐条列出来才是有用的。

**判据是「一份状态一个主人」，控件引用不违反它。** 所以这条降级为「逐间声明」，
由 ADR 0057 那套办法处理（声明而不搬）。

### 阶段 7（筛选双份合一）—— 不做，理由是前提不成立

原计划说「`LevelStack.narrowing` 与 `FilterState._conditions` 是同一份数据的两个主人」。
逐个看过之后发现是**三份不同的东西**：

| 是什么 | 住在哪 |
|---|---|
| **此刻生效**的条件 | `FilterState._conditions` |
| 压搜索帧**那一刻**的条件（快照） | `SearchFrame.conditions` |
| 每一帧各自那份收窄条件 | `LevelStack.narrowing` |

`search_flow._sync_search_frame` 是「唯一同步点」，弹回搜索帧时
`restore(narrowing_top)` 把第三份抄回第一份。三份各有各的用处，代码里的 docstring
也把区别写清楚了。**把它们合成一份不是合并，是一次重新设计** —— 而它要回答的
问题（「筛选条件归栈还是归 `FilterState`」）在本轮没有被提出来，也没有症状可循。

**与阶段 5 同一个判断**：没有症状就不要动看起来可疑的结构。

## 后果

- 目标：`MainWindow` 自写字段 **135 → 约 25**；无主字段 **36 → 0**；宿主契约 **489 → 约 120**
- 阶段 7（筛选双份合一）仍是行为变更，要做的话自带一条 ADR 与独立验证

## 被否决的方案

- **直接套用「`ui / config / signals / actions / commands / tasks / models / services /
  controllers`」那套十层模板** —— 其中六项是别的产品的形状：本项目**没有文件夹树**
  （`QFileSystemModel`）、**没有标签**、撤销走 `core/metadata/journal` 而非 `QUndoStack`、
  数据库与 EXIF 在 `core/`（有测试锁分层），而 `files = FileService` 会**直接摧毁**
  「改用户文件的调用点只许在 `write_ops.py`」那条红线。值钱的只有「一份状态一个主人」
  这条原则，其余是脚手架。
- **一次性全量组合化**（19 个 mixin 全变对象）—— 2063 处测试改动，且 `write_ops`
  是红线所在。分七步，每步可停。
- **用 `AppSignals` 信号总线解耦 flow 之间** —— ADR 0051 决策 3 已否决，理由是
  「这一笔要不要刷只有写它的那一处知道」。
- **先补完 15 间的宿主声明再说** —— 那 611 行是给「19 个切片共享一袋状态」这个结构
  打补丁；结构动了，补丁要重写。