# ADR 0053：焦点 / 当前行这一族收成一根轴

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0051（状态不止一根轴，本条是第三根）、0041（「我在哪」那条栈）、
  0021（大图页那一帧）
- 相关代码：`src/gallery/ui/window/focus.py`（新增）、`navigation_flow.py`、`viewer_flow.py`、
  `metadata_flow.py`、`watch_flow.py`、`library_flow.py`、`calendar_flow.py`、
  `src/gallery/ui/write_ops.py`

## 背景

ADR 0051 把这一族点名为「**唯一还活着的两份记账**」。`_current_row` 记的是「正看着第几行」，
而网格自己也有一个当前项 —— 代码里已经把这个危险写出来了（`write_ops.py`）：

> 大图页与网格从此必须指着同一格（`_current_row` 与网格当前项对不上时，⌘Z 取回来的照片会插到别处去）。

**22 处写、25 处读、横跨 8 个文件**，而写的内容只有两种：

| 字段 | 写 | 读 | 是什么 |
|---|---|---|---|
| `_current_row` | **11** | 18 | 大图页正看着第几行（`-1` = 没有） |
| `_level_key` | 2 | 3 | 模型里此刻摆的是哪一层 |
| `_focus` | 2 | 0 | 这一层第一次摆好时该选中哪一格 |
| `_focus_restored` | 2 | 2 | **上面那个已经摆过了没有** |
| `_pending_focus` | 2 | 2 | 重排之后要回到哪一格（分组重进） |

`_focus_restored` 是**只为另一个字段服务**的字段：`_focus` 必须在这一层只被用一次
（摆好之后就是用户自己的选择了，后台重排不该把它拽回去），而「用过了没有」这件事
现在靠一个布尔字段记着。它漏一处 —— 换层时忘了置 `False` —— 症状是**新的一层永远选不中**。

`_current_row` 的 11 处写里，**6 处是 `-1`**（换层、换库、换浏览方式、点日历格、
摊开日历格、收日历格），而那 6 处的语义完全一样：**这一层换了，没人正看着任何一张**。

## 决策

### 1. `FocusState` 装三件事，写入口是四个方法

`_focus` / `_focus_restored` / `_level_key` 是一组（**一次「到了新的一层该选中哪一格」
的记账**），`_current_row` 是另一件事但与它同生共死（换层时一个清零另一个重置）。
合起来放进 `FocusState`，写入口收成四个方法：

| 方法 | 干什么 | 替掉了原来的 |
|---|---|---|
| `arm(level_key, remembered)` | 记下到了新的一层该选中哪一格 | `_arm_focus` 里的两行赋值 |
| `take_focus()` | **取出并作废**（一层只给一次） | `_restore_focus` 里的守卫 + 取值 |
| `set_current(row)` / `clear_current()` | 大图页指着哪一行 | 11 处裸赋值 |
| `remember_pending(key)` / `take_pending()` | 重排之后回到哪一格 | `_pending_focus` 两处 |

**`take_focus()` 是「一层只摆一次」的收口**：它自己把 `_focus_restored` 置上并返回，
于是那个布尔字段从窗口上消失 —— 不是「记得每次都要置」，而是**想用两次就拿不到第二次**。

### 2. `clear_current()` 就是那 6 处 `-1`

它们散在 `_reload_level` / `_enter_calendar` / `_list_drill_photos` /
`_leave_calendar_cell` / `_activate_library` / `__init__`，语义一模一样。收成一个方法名之后，
「换层时忘了清 `_current_row`」这件事在代码里就长得不一样了（`clear_current()` 有名字，
`self._current_row = -1` 没有）。

### 3. `_level_key` 的判据跟着走

`_shown_entry_key()` 现在靠「`_level_key` 是 `None` 或与此刻的层标识不同 → 不是同一层」
判断，而 `_level_key` 的赋值在 `_arm_focus` 里。轴上给一个 `is_same_level(level_key)`，
那一处比较不再手写。

### 4. 属性只读，测试要读的那些一个字都不改

`_current_row` 有 22 处测试在读（`window._current_row`），`_focus_restored` / `_pending_focus`
各有 2 处。所以窗口上仍是**只读属性**；`_level_key` / `_focus` / `_focus_restored` /
`_pending_focus` 四个**测试一处都不碰**，那三个直接搬进轴。

## 后果

- **窗口上少 3 个字段**（`_focus` / `_focus_restored` / `_pending_focus`），
  `_level_key` 变成只读属性。`main_window.py` 的声明从 5 行降到 2 行。
- **`_restore_focus` 从 9 行降到 6 行**，而「一层只摆一次」不再靠那个布尔字段。
- `_current_row` 的 11 处写收成 2 个方法名。
- **ADR 0051 说的「两份记账」仍然在**（`_current_row` 与网格当前项是两个东西）——
  本条不修它，只把这一侧的写入口收干净。修它要动网格与监听器，是另一条 ADR 的事。

## 被否决的方案

- **让 `_current_row` 直接问网格的当前项**（消掉那一份记账）—— 大图页上网格选区停在
  打开大图的那一格，翻图只动 `_current_row`；让两边共用一个值就得让翻图去改网格选区，
  而滚动与选中高亮会跟着跳。
- **`_focus` 存 `EntryKey` 之外再加一个「已用」的包装类型**（`Used[T]`）—— 那是
  `take_focus()` 的全部内容，多一层包装类型换不来任何东西。