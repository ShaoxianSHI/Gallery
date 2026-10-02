# ADR 0006：按领域把 core 与 ui 分成子包

- 状态：已接受
- 日期：2026-10-06
- 相关代码：`src/gallery/core/`、`src/gallery/ui/`

## 背景

`core/` 与 `ui/` 各自把十几个模块平铺在一个目录里：

- `core/` 17 个模块，从「缓存目录布局」到「EXIF 字段取值」到「重命名」混在一起，
  光看 `ls` 判断不出模块归属，只能逐个打开；
- `ui/` 19 个模块同理，`zoom.py`、`thumb_queue.py`（纯计算）和 `main_window.py`（装配）
  看起来是同一类东西，实际职责完全不同。

结果是「新模块该放哪」没有答案，只能随手丢进包里。可读性的损失不是某个文件太长，
而是**目录不再传达任何信息**。

## 决策

### 1. 按领域分组，留在顶层的是跨域共享的基础设施

```
core/
├── constants.py  errors.py  atomic.py  paths.py  formatting.py   ← 跨域基础设施
├── library/    facade(config photos scanner session)
├── media/      decode(thumbs)
├── metadata/   exif(journal rename)
└── places/     countries(geonames)

ui/
├── app.py  main_window.py  theme.py  tasks.py  imaging.py
├── navigation.py                                                   ← 跨区域共享
├── browser/   grid(model rename_edit thumb_sizes)
├── viewer/    detail(zoom)
├── panels/    breadcrumb(metadata welcome)
└── thumbs/    pipeline(queue)
```

分组的判据是**「改了它会不会牵连另一个包」**，不是文件大小：

- `media` 独立成包，是因为解码策略（HEIC、EXIF 方向、缓存 key）是 ADR 0001 钉死的一条独立轴；
- `metadata` 把「读」和「写」放在一起，是为了红线 1 与红线 4 能被一眼看见 ——
  真正改动用户文件的只有 `rename`，且必须经由 `journal`；
- `places` 是离线数据 + 反查算法，与相册领域无关。

### 2. 每个子包的 `__init__.py` 就是它的公开接口

再导出对外名字（`from gallery.core.library import Library`），
调用方不必记住子模块路径；**领域内部**一律从具体子模块导入
（`from gallery.core.library.photos import PhotoRef`），避免包之间形成导入环。

### 3. 测试目录与源码层级一一对应

`tests/core/library/`、`tests/ui/browser/` 这样镜像排布。
守护性测试仍按 `core/**`、`ui/**` 递归扫描，子包化不会削弱它。

### 4. 本次只搬文件，不改行为

模块内容、类名、函数签名一律不动，只改导入路径与文档引用。
重命名的少数几个模块（`images→media.decode`、`metadata→metadata.exif`、
`grid_view→browser.grid` 等）只是为了消除 `library.library`、`metadata.metadata`
这类叠词，不带任何语义变化。

## 后果

- 看目录就知道模块归属，新增模块有明确落点：`core` 里挑一个领域包，
  跨领域共享的才放顶层；`ui` 里同理，先判断它属于哪个界面区域。
- `main_window.py` 仍是 1200 多行的装配代码。它是**唯一**明显偏大的文件，
  但拆分它属于行为改动，与搬目录混在一次提交里会让回归定位变难，留作单独一次重构。
- 代价是 import 路径变长了三四段。考虑到这些路径只在模块顶部出现一次，可以接受。

## 被否决的替代方案

| 方案 | 否决理由 |
|------|----------|
| 按技术分层拆（`io/`、`models/`、`widgets/`） | 目录只说「这是什么类型的代码」，不说「这块代码管什么」；一个功能改动会横跨好几个包 |
| 一次拆到叶子（每个文件一个包） | 目录层级比代码本身还深，改一行要改三处路径 |
| 保持平铺，只在 `__init__.py` 里分组注释 | 注释能说明分组，但不改变 `ls` 的结果；文件一多仍然要逐个打开确认 |
| 顺带拆分 `main_window.py` | 与搬目录混在一次提交里，回归时无法区分是「移动错了」还是「重写错了」 |