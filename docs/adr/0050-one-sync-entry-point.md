# ADR 0050：界面由状态算出来的映射只有一个编排点

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0041（「我在哪」是一条栈；画哪一页是栈的函数）
- 相关代码：`src/gallery/ui/window/state_sync.py`、`src/gallery/ui/window/`（其余各 flow mixin）、
  `src/gallery/ui/main_window.py`、`src/gallery/ui/write_ops.py`

## 背景

ADR 0041 把「我在哪」收成一条栈、把「画哪一页」收成一条纯函数 `page_role_of`，顺手修掉了
「日历页摆在前面、标题却写着大图页」——那个症状的成因是**两份记账**：页面由九处
`setCurrentWidget` 各自挑，而「我在哪」另有一份。

同一类毛病在**界面其余部分**原样存在，只是更安静。换层、换页、筛选生效、改名、翻图之后，
每个动作末尾都要手写一串「刷新尾巴」：

```python
self._sync_page_to_stack()
self._refresh_title()
self._update_action_states()
self._refresh_thumbnail_window()
```

这些函数**本身已经是纯映射** —— `_refresh_title` 读「在哪一页 + 那一格是谁」，
`_update_breadcrumb` 读条件与浏览方式，`_update_action_states` 读几乎全部状态。
问题不在它们不纯，而在于**没有一个统一的编排点**，于是「这一次该调哪几个、按什么次序」，
每个动作都要自己回答一遍。调用点散在十来个模块里（`_refresh_title` 10 处、
`_update_action_states` 25 处、`_update_breadcrumb` 9 处、`_sync_order_actions` 10 处）。

漏掉一个的症状与 0041 修的那个**一模一样**，而且更难发现：界面与状态对不上，不报错、
不崩溃，只是某一行字或某一条命令停在上一个状态。

## 决策

### 1. 编排点：`_sync_ui()`

那几份纯映射按**固定次序**串成一处（`StateSyncMixin._sync_ui`）：

```
_sync_page_to_stack → _refresh_title → _update_breadcrumb
                   → _update_action_states → _sync_order_actions
```

**次序不是随意的**：换页排第一，其余三份都读「现在在哪一页」，晚一步就按上一页摆。

各条路径只改状态，末尾叫这一声，不必自己挑该调哪几个。这是 ADR 0041 决策 7 的同构做法 ——
那里收的是「画哪一页」，这里收的是「把界面重算一遍」。

### 2. 回归测试钉结构，不钉行为

**一个函数里不许同时调两个以上成员**（`tests/ui/window/test_state_sync.py`）。
单独调一个不算违规 —— 那是那一处精确需要的那一份。唯一获准的那处是
`leave_detail_page` 提前调 `_sync_page_to_stack`（见决策 4）。

钉行为是钉不住的：这些映射的输出本来就「和状态一致」，测不出「这一条路径忘了调」。
能测出来的只有结构，而结构恰好就是病因。

### 3. 不收进来的三样，各有理由

- **`_refresh_thumbnail_window`** —— 它会**发起解码**，不是纯读；而且网格还没重排时调它会把
  排好的请求整份丢掉（`set_window` 是整份替换）。留在调用点。
- **`_layout_title_bar`** —— 摆的是**几何**（`setGeometry` + `raise_`），由 `resizeEvent` /
  `showEvent` / `changeEvent` 驱动，与「状态 → 显示内容」不是一回事。
  **但它并不是完全在编排点之外**：`_update_action_states` 末尾有一句会调它，因为**可见性归那份管**
  —— `_scope_toggle` 与 `_external_button` 的显隐在那一句里定，而 `_layout_title_bar` 要读
  `isVisible()` 才能算 `reserve_right` 与 `button_rect`。所以准确的说法是「不作为编排点**的一员**，
  由持有可见性的那一份在末尾触发，另加三个 Qt 事件」，而不是「收不到」。
  量过它有多贵：**0.010 ms，与库里多少张无关**（2000 张与 6000 张同值）。
- **`_refresh_undo_label`** —— 要**问一次 core**（`next_undo_label()`），是 IO 不是读状态。

把前两样塞进编排点会让「同步一次界面」变成「同步一次界面 + 可能触发一轮解码 + 多算一遍几何」，
第三样再加上「查一次库」。那么「什么时候该同步」就又变成了一个需要判断的问题 —— 而那正是
这一条要消掉的。**几何那一项不是为了性能才排除的**（它太便宜了，见上），而是因为
「谁持有输入」比「跑多少遍」更要紧。

### 4. 换页仍然可以在编排点之前显式叫

与 ADR 0041 决策 7 同一条约束：**换页的时机有时不能等编排**。

`leave_detail_page` 必须在 `_flush_pending_watch()` **之前**换页 —— 那一句判的是
「当前页是不是网格页」（`_defer_watch_until_idle`），晚一步这批暂存的外部变化会被再推迟一次，
症状是「照片变了但网格不更新，要切走再切回来才好」。

## 后果

- 十来个模块里那串手写尾巴换成一行 `_sync_ui()`；`open_library`、`_reload_level`、
  `_enter_calendar_drill`、`_back_to_calendar`、`leave_detail_page`、`_open_detail` 等
  收尾处各自要挑哪几个的问题消失。
- **编排点带来一次去重**：切库那一段原本除了末尾的尾巴之外还单独调了 `_sync_order_actions`
  与 `_update_breadcrumb`（因为它们跑在换栈之前），现在由末尾那一句一并重算。
- 选区变化**仍然只跑 `_update_action_states`**，不并进编排点：拖选时标题、面包屑与页面都不变，
  而 `_update_breadcrumb` 会把胶囊整排重建一遍。`_schedule_action_states` 那个 0ms 定时器
  就是为这件事存在的，它保留原样。
- **代价**：编排点被调到的次数比原先多（原先某些路径只调其中一两个），所以那几条路径现在会
  多付一次 `_update_action_states`。**量过了**（2000 / 6000 张、离屏、各 30 次取中位数）：

  | | 空选区 | 全选 |
  |---|---|---|
  | `_refresh_title` | 0.008 ms | — |
  | `_update_breadcrumb` | 0.009 ms | — |
  | `_layout_title_bar` | 0.010 ms | — |
  | `_update_action_states` | 0.048 ms | **4.86 ms**（2000 张）/ **15.1 ms**（6000 张） |
  | `_sync_ui` 全部 | 0.077 ms | 4.87 ms / 15.5 ms |

  结论有两条，都与直觉相反：
  1. **几何不是问题**：`_layout_title_bar` 恒为 0.010 ms，与库里多少张**无关**。原先担心
     「每次状态同步都重排一遍标题栏」是多余的 —— 那正是为什么它仍然由 `_update_action_states`
     末尾触发（决策 3），而没有为了省这点力气去改结构。
  2. **选区才是问题**：`_update_action_states` 随选区**线性**增长（2000 → 4.86 ms，
     6000 → 15.1 ms，约三倍行数对三倍时间），贵的是 `_selected_entries` 与
     `flattened_entries`。所以多出来的那一次调用**只在选区很大时才看得见**。
     受影响的是原先只调 `_refresh_title` 的那几条（改名之后、点条件胶囊、搜索框得失焦）——
     都是**一次性动作**而不是循环，所以即使全选也只是一次轻微的停顿；但它是这条决策明知的
     代价，不是「顺手就省了」。
