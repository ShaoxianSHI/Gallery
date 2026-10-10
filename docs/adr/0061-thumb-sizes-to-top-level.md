# ADR 0061：缩略图档位上顶层 —— 一个纯计算模块不该住在页面包里

- 状态：已接受
- 日期：2026-10-10
- 相关 ADR：0059（顶层只留跨包共享的）、0060（拆 `panels/`）
- 相关代码：`src/gallery/ui/thumb_sizes.py`、`src/gallery/ui/window/state/thumbnail_size.py`

## 背景

ADR 0060 收口之后，`ui/` 的形状定了（三个页面包 + 两个零件包 + 一台机器 + 顶层共享模块）。
**它顺手立了一条判据**：顶层只许留被两个以上子包用的模块，而 `tests/test_guards.py` 里
有一张登记表守着。

复查的时候按**反方向**再扫一遍（「被两个以上包用、却不在顶层的模块」），只剩三处。
去掉组合根 `main_window.py` 之后是两处：

```
browser.model      <- ['browser', 'window']
browser.thumb_sizes <- ['browser', 'window']
```

`browser.model` 是**假警报**：它是网格的列表模型（`QAbstractListModel`），
`window/library_flow.py` 要它是因为**窗口建了那个模型**并把它连到网格上 ——
「谁拥有」与「谁属于哪个包」不是同一件事，`viewer.detail` / `calendar.page` 同理。

`browser.thumb_sizes` 不是。

## 判据

三条，都是量出来的：

1. **它不导入 Qt。** 220 行纯计算：档位表、档位↔边长的换算、列数搜索、手势累加器。
   `browser/` 剩下的六个模块**每一个都导入 Qt**（`grid` / `model` / `delegate` 是控件，
   `transition` / `anchoring` 挂在网格上）—— **它是那个包里唯一一个不画东西的东西。**
2. **它的消费者一半在包外**：`browser/` 三个 + `window/library_flow.py` +
   `window/state/thumbnail_size.py` + `main_window.py`。按 0060 那条判据，这就该在顶层。
3. **它逼出一条反向依赖**：`window/state/thumbnail_size.py` 为了拿 `edge_for_index` /
   `nearest_index` / `shifted_index` 三个函数，得从 `browser/` import ——
   **一个状态对象伸手进一个页面包的内部，而它自己并不是那个页面的东西**。
   这是 `window/` 唯一一条这样的边（其余 21 条顶层依赖全是共享基础设施）。

## 决策

`browser/thumb_sizes.py` → `ui/thumb_sizes.py`，登记进 `TOP_LEVEL_MODULES`。

**测试跟着搬**（`tests/ui/browser/test_thumb_sizes.py` → `tests/ui/test_thumb_sizes.py`）——
它不导入 Qt也不建窗口，本来就不测网格。

**顺带在 `thumb_sizes` 的文档里说清它与 `ui/thumbs/` 不是一回事**：那边是解码流水线
（生产），这边只是尺寸的算式。ADR 0060 刚把「`thumbs` 一个名字两个意思」这件事解决掉一半
（把日历那份改名 `thumb_store`），这里补上另一半。

## 加一条守护

0060 那条守的是「顶层不许留下只被一个包用的模块」，**反方向当时没人管** ——
于是一个跨包共享的模块可以舒舒服服地躺在某个包里，而没有人觉得不对。
本条守着反方向：**被两个以上子包用的模块，要么在顶层，要么在登记表里写明为什么留在那**。

`browser.model` 就是靠这一条豁免的，理由写在登记表里（它是网格的模型，
`library_flow` 建它是因为窗口拥有它）。

## 后果

- `window/state/` 不再伸手进任何页面包（那一条边消失）。
- `browser/` 从 7 个模块降到 6 个，**每一个都是控件或挂在控件上的**。
- 顶层从 14 个模块到 15 个，全部在登记表里。
- 纯移动 + import 更新，测试一处逻辑没改。

## 被否决的方案

- **把 `thumb_sizes` 拆成两半**（「档位表」留 `browser/`、「手势累加器」上顶层）——
  两半之间是 `RateLimitedAccumulator(StepAccumulator)` 的继承关系，
  拆开就是「子类在顶层、父类在包里」，比整体搬走更糟。
- **把 `browser.model` 一起搬上去** —— 量过了：它是网格的模型，
  `window` 要它是因为窗口拥有它（见背景里那条判据 3 的对照）。
  `viewer.detail` / `calendar.page` 也是同一形状。**「谁拥有」与「谁属于哪个包」
  不是同一件事**，这一条要写下来，否则下一次扫描还会把它们当成误报再查一遍。
- **连 `browser/grid.py` 里的 `PREFETCH_MARGIN` 一起搬** —— 那是网格自己的预取边距，
  网格与缩略图流水线都读它，但它**不是一个模块**，只是一份网格的布局参数。
  为一个常量开一条顶层依赖不值，而它已经在 `window/thumbnail_flow.py` 那一行写着出处。
