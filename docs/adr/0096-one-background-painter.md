# ADR 0096：整块窗口只在窗口上铺一次底，其余各层不铺

- 状态：已接受
- 日期：2026-10-25
- 修改：[0005](0005-one-background.md) 决策 3 与后果里「逐控件下发底色」那一半 —— 颜色仍逐控件下发，
  但**铺**它的人只剩窗口；[0035](0035-input-and-popup-colours.md) 的前提不变（输入面还是那个颜色）。
- 相关 ADR：0005（整个窗口共用一块底色）、0029（横幅是浮层）、0035（输入面与浮层配色）、
  0089（分区式标题必须不透明）
- 相关代码：`src/gallery/ui/theme.py`、`src/gallery/ui/main_window.py`、
  `src/gallery/ui/browser/grid.py`、`src/gallery/ui/browser/transition.py`、
  `src/gallery/ui/browser/delegate.py`、`src/gallery/ui/viewer/detail.py`、
  `src/gallery/ui/viewer/metadata.py`、`src/gallery/ui/calendar/painting.py`、
  `src/gallery/ui/calendar/time_view.py`

## 背景

ADR 0005 之后，窗口里**七处**各自铺一次同一个颜色：网格视口（调色板 ``Base``）、网格切档过渡帧、
分区式标题行、大图页的 ``QGraphicsView`` 背景刷、元数据面板的 ``paintEvent``、日历那四层的
``painting.begin`` 与周视图表头、以及菜单栏（调色板 ``Window``）。

七处都读同一个来源，所以今天看不出接缝。但下一步要在窗口上铺一张**生成出来的浅色背景**：
每一处各铺一次就都会把它盖住，而各自算一次坐标又会在接缝上差出一点。**铺底这件事只能有一个主人。**

## 决策

### 1. 铺底只有一个调用点：窗口的 ``paintEvent``

``theme.paint_background(painter, rect)`` 是唯一铺底的地方，``MainWindow.paintEvent`` 调它。
换一种背景（渐变、纹理、图片）只改这一个函数，所有页一起换。

### 2. 各层用 ``theme.mark_see_through(widget)`` 声明「我不铺」

一句声明做三件事，缺哪一件都会在某个时序里失效：

| 设的东西 | 挡住的是什么 |
|---|---|
| 控件属性（动态属性） | ``apply_theme`` 每轮遍历整棵控件树重设调色板，不记这一笔就被盖回不透明 |
| ``WA_NoSystemBackground`` | Qt 自己按调色板铺那一下；它是控件属性，换样式表重新 polish 也还在 |
| ``autoFillBackground(False)`` | ``QScrollArea::setWidget`` 会把内容区设成 ``AutoFillBackground`` |

**必须声明，不能让控件在 ``changed`` 里自己补一遍** —— 补的那一遍跑在 ``apply_theme`` 前面还是
后面就成了排序问题，而这一类缺陷在界面上没有症状，只有接缝。

### 3. 少数「必须真的盖住东西」的地方继续铺

切档过渡那一帧要擦掉上一帧的旧格子，分区标题要挡住上一段最后一行照片（ADR 0089）——
这两处仍然铺，但走 ``theme.paint_background``：视口那份调色板现在**是透明的**，拿它铺等于没铺。

### 4. 菜单栏跟着透明，独立顶层窗口不跟

菜单栏（``QMenuBar``）露出的也必须是窗口那一块。**菜单、对话框、弹出列表不动**：它们是独立的
顶层窗口，看不到主窗口底下那层，而且它们整块都是文字，需要一个实底。

## 后果

- 底色与将来的背景都只有一个来源；「换了颜色某一层没跟上」这一类缺陷从结构上消失。
- 代价是重绘：非不透明子控件每刷一帧，Qt 都要把它底下那块父窗口一起重画。窗口的
  ``paintEvent`` 因此从「一次 ``fillRect``」变成「一次 ``fillRect`` + （将来）一次 ``drawPixmap``」，
  按 ``event.rect()`` 裁剪后只画脏区。
- **测试要跟着改口径**：控件单拉出来 ``grab()`` / ``render()`` 量到的是一片空白。量它画了什么，
  得先替窗口把底色铺上，而且 ``QWidget::render`` 默认带 ``DrawWindowBackground``，会把调色板
  那一层也画上去 —— 两份都在 `tests/ui/rendering.py`。
- **大图页的淡出变了一点**：淡出层那张快照是视口抓的，视口透明，于是旧照片四周（原来是一块底色）
  在淡出的 160ms 里露的是新照片。底色是一整块纯色时几乎看不出来，换成背景之后那才是对的观感。
- 视频页仍恒为黑底：播放要的是影院黑，不属于「窗口那块底」。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 每个控件各拿一份「铺底」函数自己铺 | 那是把 ADR 0005 那七处换成七次 ``paint_background``：背景图要在七处各算一次窗口坐标，接缝迟早差一档 —— 正是 0005 要消灭的事 |
| 把应用调色板设成透明 | 菜单、对话框、输入框一起变透明，字压在照片上读不出来；0005 决策 3 也不许动应用调色板 |
| 先抓一张窗口截图当背景 | 每次换外观都得重拍，等于把界面冻结在那一刻 |
| 把背景塞进 ``QPalette`` 的 brush | 调色板不认「窗口坐标系」，每层各铺各的、各偏各的 |
| 只留窗口铺底、其余层靠 ``autoFillBackground(False)`` | 不够：视口、滚动区的内容区、``QGraphicsView`` 都会自己铺一层，原因各不同（见决策 2 那张表） |
