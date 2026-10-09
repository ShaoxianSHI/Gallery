# ADR 0054：横幅文案这一族收成一根轴

- 状态：已接受
- 日期：2026-10-09
- 相关 ADR：0051（状态不止一根轴，本条是第四根）、0029（横幅覆盖在内容上）、
  0010（开库时解析地点）
- 相关代码：`src/gallery/ui/window/banner_text.py`（新增，取代 `notice.py`）、`banner_flow.py`、
  `ordering.py`、`library_flow.py`、`search_flow.py`、`calendar_flow.py`、
  `navigation_flow.py`、`thumbnail_flow.py`

## 背景

横幅**只有一个槽**，而后台进度有三条、错误提示有好几条，于是「谁占着它」是一份要记账的事。
ADR 0051 把这一族点名为「7 个字段喂给 `_show_banner` 一个输出」：

| 字段 | 写 | 读 | 是什么 |
|---|---|---|---|
| `_progress_notice_text` + `_progress_notice` | 4 | 2 | 「正在读取元数据…」自己写过哪一句 |
| `_place_notice_text` | 4 | 3 | 「正在解析照片的地点…」 |
| `_index_notice_text` | 3 | 3 | 「正在建立检索索引…」 |
| `_level_notice` | 6 | 2 | 「这一层是空的」是不是我们说的 |
| `_scan_issue` | 4 | 5 | 扫描本身出过问题（**进度不许抢**） |
| `_thumbnail_failures` | 2 | 1 | 这一轮解码失败了几张（那句话里的数字） |
| `_banner_sticky_until` | 2 | 2 | 用户刚触发的那条要让后台消息让位到什么时候 |

**26 处写、14 处读、横跨 7 个文件**。其中最要紧的一条不变式写在 `banner_flow._clear_banner`
的注释里：

> 只 `hide()` 不清账的话，下一条进度会以为「上面已经写着我的字」而继续让着，
> `_update_level_notice` 还会收掉一条它并不拥有的横幅。

也就是说**「清屏」与「清账」必须同时做**，而 `_clear_banner` 现在是唯一一个同时做的地方 ——
换句话说其余六处「换层 / 换库」各自手写了 `_level_notice = False` 加几个 `= None`，
而它们**漏掉哪一个都不会立刻看出错**：漏 `_level_notice` 时 `_clear_banner` 之后那一句
「这一层是空的」再也说不出来，漏 `_scan_issue` 时进度会去抢那句「哪些内容没读到」。

**`NoticeSlot` 已经把三族的记账收成一处**（`notice.py`），但它读写的是**窗口上的属性，
属性名是传进去的字符串**（`NoticeSlot(self, text_attr="_place_notice_text")`）——
所以字段仍然在窗口上，仍然有 7 个，而「一个槽」这个概念本身没有对象，
而这一族是四根轴里**唯一能脱离窗口单测**的一根（它要的只是三个回调与一个时钟）。

## 决策

### 1. `BannerText` 是那一个槽，三个 NoticeSlot 住在它里面

`NoticeSlot` 改成**自己持有 `text`**（不再是 `getattr/setattr` 窗口属性），三个实例
（progress / place / index）是 `BannerText` 的字段；「这一层是空的」那一族**不占槽** ——
它那句每次都不一样（有内容 / 在分组 / 在相册视图），所以只留一个布尔记号。
窗口上那七个字段**七个都消失**，横幅本体（`BannerOverlay`）与「显示一句话」这个动作
仍在 `banner_flow.py`。

**`NoticeSlot` 不再需要 `owner`**：它要的三样东西变成三个回调（显示 / 现在写着什么 /
收掉），由 `BannerText` 转发给窗口 —— 而 `BannerText` 是纯 Python 的，**不碰 Qt**，
所以这一族能脱离窗口单测（时钟也是注入的）。

### 2. 「谁准说话」的判据放进槽，不放在调用点

「扫描出过问题时进度不许抢」原先散在三四个调用点（`library_flow` 两处、`search_flow` 一处、
`ordering` 一处），漏一处是静默的。`NoticeSlot` 因此带一个 `allowed` 回调，
**地点与索引两族接上门闩，「读元数据」那一族不接** —— 它说的是用户正在等的东西
（ADR 0030）。

### 3. 三种「清」各有名字，混用会静默出错

| 方法 | 什么时候 | 清什么 |
|---|---|---|
| `clear_all()` | 翻照片、退层 | **三个槽的账 + 让位期**（`_clear_banner` 现在做的事） |
| `reset_level()` | 换层 / 换库 | 三个槽的账 + 扫描门闩（**不动让位期**：那是时间，与层无关） |
| `note_scan_issue()` | 扫描出问题 | 把「进度不许抢」那个门闩插上 |

原先换层那六处手写的正是 `reset_level()` 的内容，而它们**没有一处顺带清让位期** ——
保留这个差别，因为 `_clear_banner`（`step_detail` 换照片时也走）与 `reset_level` 的差别
是真的：换照片不该让用户刚做的动作失去让位期。用户刚在检索结果页删掉三张、随即退一层时，
那句确认背后的两秒仍然该有效 —— 这条有专门的回归测试。

### 4. 派生判据跟着走

「此刻谁占着横幅」由三个 `is_ours` 加一个 `level_notice` 合成，而原先 `notice.py` 里那段
「抢之前先看一眼、写上去之后记住自己写的是哪一句、收尾时只收自己那一句」的三件事
**一字不改** —— 它本来就是对的，缺的只是把状态从窗口属性搬进对象。

## 后果

- **窗口上少 7 个字段**，`main_window.py` 的声明少 7 行。
- `NoticeSlot` 的属性名参数（`text_attr=` / `flag_attr=`）**消失**，模块本身也没了
  （四个调用点只剩 `self._banner_text.<族>` 一次属性访问）—— 那是本条最实在的收获：
  `NoticeSlot(self, text_attr=...)` 写错一个属性名要等到运行时才发现，而
  `self._banner_text.place` 写错当场就是 `AttributeError`。
- **`_clear_banner` 与换层那六处的关系说清了**：前者是「清屏」（连让位期一起），
  后者是「这一层的账归零」。
- `tests/ui/test_group_flow.py` 有一处 `window._place_notice_text = None`（造一个
  「没占着横幅」的起点），改成调槽的 `clear()`。

## 被否决的方案

- **`_thumbnail_failures` 留在窗口上**（它看着像解码失败的计数而不是横幅文案）—— 它与
  `_scan_issue` 一样是「这句话该不该说」的数据源，留在窗口上就得让 `banner_text` 反过来
  问窗口。两个都是「这一轮的几句话该怎么写」的输入。
- **把 `NoticeSlot` 整个删掉，改成三个方法** —— 「抢之前先看一眼 / 写上去记住 /
  收尾只收自己那一句」那三件事有三份，删掉槽就是让它们再各抄一遍。
- **让 `BannerText` 继承 `QObject` 并带 `changed` 信号** —— ADR 0051 决策 5 说好了
  四根轴做完之前不接信号（`_sync_ui` 的次序是手写的一张表，见 ADR 0050 决策 3）。
  那时再统一接。