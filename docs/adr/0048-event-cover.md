# ADR 0048：事件封面 —— 封面是绑定里的一张照片，存在 `events.cover_path`；表结构升版改走迁移链

- 状态：已接受
- 日期：2026-10-08
- 相关 ADR：0043（事件体系：独立的 `events.db`、绑定按路径记、改路径要跟着搬）、
  0028（相册封面：命令挂在照片上、封面不跟着改名走）、0045 / 0046（第二类事件与搜索字段前缀）
- 相关代码：`src/gallery/core/events/{kinds,schema,store,models,conversions,orphans}.py`、
  `src/gallery/core/library/{photos,groups,facade}.py`、`src/gallery/core/library/search.py`、
  `src/gallery/core/metadata/{rename,transfer}.py`、`src/gallery/ui/window/event_flow.py`、
  `src/gallery/ui/panels/event_edit.py`、`src/gallery/ui/browser/model.py`

## 背景

事件分组卡片现在的封面是**组内第一张**（`GroupRef.cover`），而组内顺序取决于当前的排序字段
—— 于是「按相机分组」时京都旅行的封面是那台相机拍的第一张，「按文件名分组」时又换成另一张。
用户想要的是「这次旅行就用这张」，而这个愿望今天没有任何地方能表达。

相册封面（ADR 0028）已经解决过同一个问题，姿态是「命令挂在照片上，一按就是」，本 ADR 沿用。
但事件这边有三件相册那边没有的事，于是不能照抄：

1. **封面的指向会失效。** 相册封面存的是**相对库根**的路径（ADR 0028 决策 2），而事件的绑定按
   **绝对路径**记 —— 于是「这张照片还在不在」这件事对事件是真实存在的（ADR 0043 决策 8 已经为
   绑定处理过一次：改名与移动要跟着搬）。封面是一条指向照片的路径，就继承这件事的全部后果。
2. **封面必须与绑定一致。** 卡片只能画**组内**那一张照片（缩略图流水线拿的是 `PhotoRef`，不是路径），
   所以封面若指向组外的照片，要么每次现造一个引用（一次 `stat`），要么画不出来。**让封面恒为
   「被这个事件绑定的一张照片」**把这一整类问题一次消掉。
3. **事件库是唯一一份重建不出来的数据。** 现在 `attach()` 里版本对不上就 `DROP` 全部表
   （ADR 0043 决策 1 的取舍：索引可以重建，事件不能）。**加一列就等于清空全部事件与绑定** ——
   这是本 ADR 的真正重量所在，而它与「封面」本身无关。

## 决策

### 1. 封面存在 `events` 表的一列 `cover_path`，不变式是「封面 ∈ 该事件的绑定」

```sql
ALTER TABLE ev.events ADD COLUMN cover_path TEXT;   -- 可空；NULL = 用组内第一张
```

**为什么可以空**：卡片**永远有一张封面**（ADR 0028 决策 4 的同一条取向）。所以「指定的那张读不出来」
「解绑了」「照片被删了」都不是错误状态，只是退回组内第一张。把封面做成可空的一列，而不是
「必须有一张」，是为了让所有失效路径都有一个统一的、降级的答案，而不必各自报错。

**不变式**：`cover_path` 非空时，那一行 `photo_events` 里必须有 `(cover_path, id)`。它由三处保证：

| 场合 | 保证方式 |
|---|---|
| 设封面 | 入口只对**已绑定的**照片开放（见决策 6），写之前再查一次绑定 |
| 解绑 / 孤儿清理 | 同一批里把指向那些路径的封面清空（决策 5） |
| 改名 / 移动 | 同一个事务里把封面一起搬（决策 4） |

判据只有这一句，不散在别处再写一遍。**不变式破了不会崩**：渲染时找不到组内那一张就退回第一张
（决策 7），所以即便手工改过数据库，症状也只是「封面没生效」。

**为什么存绝对路径**（与 ADR 0028 决策 2 相反）：那一边的理由是「`config.toml` 要跟着库走、
可能进版本控制」。这一边封面与 `photo_events.path` 是**同一套路径空间**，两者必须能直接比较
（渲染时拿 `cover_path` 去组里找那一张、解绑时要知道清掉哪一个事件的封面）；存相对路径于是
每次比较都要转换一次，而库挪位置时绑定一样全断 —— 那是 ADR 0043 层面的既有取舍，不在这一条里解决。

### 2. 封面是**声明式公共字段 + 一个控件标记**，不是结构列，也不是手写的一列

`kinds.COMMON_FIELDS` 多一条：

```python
EventField("cover", "封面", "cover_path", control="cover")
```

于是下面这一串**一个字都不用改**，因为它们全是由字段定义生成的（ADR 0043 决策 3）：

| 谁 | 怎么就自动了 |
|---|---|
| 建表 | `schema._event_columns()` 从 `COMMON_FIELDS` 拼列 |
| 插入 / 更新 | `store.create` / `update` 的 `written` 与 `assignments` 都按 `COMMON_FIELDS` 拼 |
| journal 前像 | `conversions._field_changes` 遍历 `(*COMMON_FIELDS, *kind.fields)` |
| 撤销 / 恢复 | `_rewrite_event` / `_restore_event` 按同一份清单写回 |
| 读出来 | `_event_from_common` 把公共列收进 `values`，于是 `event.cover_path` 是一个属性 |

**反过来，手写成 `_EVENT_TAIL` 里的一列要在七个地方各写一次**（建表、insert、update、
`_field_changes`、`_rewrite_event`、`_restore_event`、`_event_from_common`），而漏掉哪一处
**都不报错** —— 最典型的就是漏掉 `_field_changes`：改封面不进 journal，撤销会把它连同别的字段一起
写回旧值。这正是 ADR 0043 决策 3 与 ADR 0026 那条教训要防的东西，所以这里选生成。

**`control` 标记只做一件事**：告诉面板这一格不是输入框（`choices` 已经有同款先例）。
搜索那一族**不受影响** —— `_event_fields()` 的公共两条是手写的（事件类型 / 事件），
每类独有的字段才从 `kind.fields` 生成，所以封面**不会**变成一个可搜字段（那会给出
`#封面 /Users/…/海边-01.jpg` 这种没人会敲的条件）。这一条要有守护（见后果）。

**面板因此不做挑选器**（v1）：`control="cover"` 那一行显示当前封面的**文件名**与一个「清除封面」，
新建事件时**整行禁用**并说明「建好之后再设」。理由是诚实的：新建时「哪些照片会被记进去」要到
面板关掉、`_bind_the_new_one` 跑完之后才定下来（ADR 0043 决策 4），那时候给一份不完整的候选列表
比不给更坏；而要一份完整的候选列表就得给面板一次整个事件的照片列表，那是几千次 `stat` 加几千次
解码 —— 而用户在网格里已经看得见这些照片（决策 6）。

### 3. 表结构升版改走**迁移链**；认不出的版本宁可不挂事件库

`SCHEMA_VERSION` 从 1 升到 2，而**升版不许重建表**。`attach()` 改成：

```python
version = …                                     # 仍是 ev.user_version（ADR 0043 决策 1）
if version > SCHEMA_VERSION:                    # 比这份代码新 = 用户降级了应用
    logger.info("事件库的版本比这个版本新，这次不挂它")
    return None
connection.executescript(_EVENTS_SCHEMA)        # 不存在才建（新库直接就是最新结构）
…其余建表语句…
_migrate(connection, version)                   # 逐级补齐缺的列
connection.execute(f"PRAGMA ev.user_version = {SCHEMA_VERSION}")
```

三条要点：

- **迁移步按「加一列」写，且幂等**（`PRAGMA ev.table_info` 里没有才 `ALTER`）。于是**顺序不重要**：
  新库走完 `CREATE TABLE` 已经有这一列，迁移那一步看见它在就跳过；旧库的表已经存在，
  `CREATE TABLE IF NOT EXISTS` 什么都不做，迁移那一步把列补上。
- **版本对不上不再 `DROP`。** 认不出的版本（更高、或链条中间缺一级）一律**不挂事件库**：
  `attach` 返回 `None`，界面据此让「记录为」那一行不出现、事件分组为空（ADR 0043 决策 1 的降级）。
  **用户的事件一个字节都不动**，而这比「重建出一个空库」更接近真相：应用读不了它不等于可以删它。
  将来真的要重建某一张表，那一步写成链条里的一条显式语句（里面可以带 `DROP`），
  **不再由「版本对不上」这个隐式条件触发**。
- **不用 `executescript` 跑迁移**：它会隐式提交，于是链条断在半路时库停在中间状态；
  迁移必须在 `with connection:` 的同一个事务里。

**这一条改的是一个已有守护的语义**：`tests/core/events/test_store.py` 里那条
「版本对不上时重建的是事件库自己的表」现在要改成「1 → 2 只加列、数据还在」。它原来守的那件事
（重建脚本要列全事件库的表名）仍然由索引那边的守护守着（`test_events_and_index.py`：
索引重建不许碰 `ev.*` 的表名）。

### 4. 改路径跟着走：封面由 `follow_moves` 在**同一个事务**里搬

`EventStore._apply_moves` 现在只改 `photo_events`，加上 `events` 那一列：

| 输入 | 绑定 | 封面 |
|---|---|---|
| 单张照片 | `SET path = ? WHERE path = ?` | `SET cover_path = ? WHERE cover_path = ?`（可以命中多行：几个事件的封面都在这一张上） |
| 一个相册 | 按前缀 `substr` 改写后缀 | 同一句前缀改写 |

**五条调用点一个字都不用加**（它们已经汇到 `follow_moves`，ADR 0043 决策 8）：应用内改名、
应用内移动到相册、撤销这两样、监听器报上来的外部改名与移动。撤销自动反向 —— 它就是同一条语句
朝另一个方向再跑一次。

**这一条与 ADR 0028 决策「相册封面不跟着改名走」相反，而那一边的否决理由在这里不成立**：
0028 的问题是「`config.toml` 是第二个真相源，撤销时要把配置也改回来，两处要同步」。这里封面与绑定
在**同一个库、同一个事务、同一条语句**里，撤销走的是同一条路 —— 没有第二份状态，也就没有需要同步的东西。
更根本的是：绑定**本来就得跟着走**（ADR 0043 决策 8），所以让封面跟着一张已经跟着走的照片走，
边际成本是两条 SQL；而**不跟**的话，事件卡片会在用户改一次名之后悄悄换成另一张照片 ——
而那正是相册封面早就有的毛病。

### 5. 解绑与孤儿清理顺带清封面；移到废纸篓**刻意不动**

| 操作 | 绑定 | 封面 |
|---|---|---|
| 从这次事件移出（`unbind`） | 删 | **同一事务里清空**指向那些路径的封面（不变式，决策 1） |
| 孤儿清理（`forget_bindings`） | 删 | **同一批里清空**；`vanished` 那一档要用户点头，与绑定同一把尺子 |
| 移到废纸篓 | **不动** | **不动** |
| 删掉整个事件 | 三张表各删各的 | 随那一行走；撤销按前像恢复（决策 2 让它是白得的） |

**废纸篓那条刻意不动**，与绑定完全一致：移到废纸篓之后那行绑定就成了孤儿，由开库时的孤儿检查
（ADR 0043 决策 9）连同它的三档确认一起处理。在删除那条路上顺手清封面，等于让**同一次删除走两套
清理语义**（一条当场清、一条要用户点头），而用户看到的现象完全一样。

封面临时指向一张已被移走的照片时不报错，卡片退回组内第一张（决策 7）。孤儿清理的那句横幅
不必改：`forget_bindings` 的返回值仍然只是「清掉了几条绑定」，而封面退回第一张是用户看得见的结果。

### 6. 入口挂在**照片**上（ADR 0028 决策 1 的同款姿态）

| 落点 | 菜单里那一行 |
|---|---|
| **恰好选中一张照片**（网格或大图页），且它属于至少一个事件 | 设为事件封面 →（子层列出它所属的那些事件） |
| 多选 / 分组卡片 / 不属于任何事件 | **不出现**（不是置灰） |
| 事件分组卡片，且真设过封面 | 清除事件封面 |

- **子层是必要的**：一张照片可以同时属于一次演出与一次旅行（ADR 0043 背景第 2 条），
  「设为哪一个事件的封面」这个问题是真实的。子层的形状与「记录为 → 旅行 → 京都五日」同款，
  清单由 `events_for_photos` 给（`_leaving_events` 已是同一条路的同款写法）。
- **不在菜单栏摆**：都要「当前这一格」才有对象（ADR 0014 决策 5，与相册封面同一处置）。
- **不给快捷键**：与相册封面同一处置（⇧⌘C 已经是「拷贝路径」）。
- **入口写在 `ui/window/event_flow.py`**，不写 `ui/write_ops.py`：它一个用户的文件都不碰
  （与 ADR 0043 决策 4 同一理由），所以红线 1 那份按文件名找的守护不需要动，
  `WRITE_METHODS_ON_LIBRARY` 也不必加方法。
- **换封面可 ⌘Z**：它走 `EventStore.set_cover` / `clear_cover`，那两条与 `update` 一样
  **记 journal**，所以 `⌘Z` 能把封面换回去，横幅可以如实说「⌘Z 可以撤销」。
  这一条与相册封面（不可撤销）是相反的，理由是：封面是**事件那一行**的一部分，
  而事件本体本来就在撤销栈上（ADR 0043 决策 5）；而 ADR 0028 那边不肯上栈是因为
  `config.toml` 要当成第二份状态来同步（决策 4 的那一段）。

### 7. 卡片怎么画：`GroupRef` 多一个 `pinned_cover`，`cover` 照旧退回组内第一张

```python
@dataclass(frozen=True, slots=True)
class GroupRef:
    facet: str
    key: str
    label: str
    photos: tuple[PhotoRef, ...]
    #: 显式指定的封面，**已经解析成组内那一张**（由 build_groups 做，见下）。
    #: ``None`` 就是「用组内第一张」—— 相册、分组、事件的空封面都走这一支。
    pinned_cover: PhotoRef | None = None

    @property
    def cover(self) -> PhotoRef | None:
        return self.pinned_cover or (self.photos[0] if self.photos else None)
```

**为什么存 `PhotoRef` 而不是路径**：调用方（缩略图流水线的 `_thumbnail_target`、模型的 `_cover_of`、
委托）拿到的都是 `PhotoRef`，而流水线的在途记账按 `(路径, 档位)` —— 存路径就得在**每次读封面时**
在组里线性找一遍（`data(DecorationRole)` 每帧每张卡片都读一次），一屏组卡片就是几万次比较。
`build_groups` 手里已经有这一组的成员，解析一次是 O(1)。

**`build_groups` 怎么知道封面**：走已有的 `event_values`，**不新增参数、不发新查询** ——
`EventRef` 多带一个 `cover_path`（`events_for_photos` 那条 SQL 加一列，同一次 JOIN）。
分组时只认**组键对得上**的那一条引用：

```python
for event_ref in events.get(ref.path, ()):
    if event_ref.cover_path and event_ref.group_key == key:   # 组键 = 起始日期#事件 id
        covers[key] = …                                       # 现成的不变式，不解析字符串
```

用 `EventRef.group_key` 与组键比对而不是从组键里把 id 解析出来，是复用 ADR 0043 决策 7 已经写死的那条
「组键取 `起始日期#事件 id`」—— 两个来源于是不会各自漂。

**于是流水线、模型、委托、`_cover_of`、`shows_path` 一个字都不用改**：卡片要的仍然是
`entry.cover`，只是它现在是钉住的那一张。`GroupRef` 是 frozen dataclass，`pinned_cover` 参与 `==`，
所以换封面会让 `_reorder_level` 末尾那句「顺序没变就不重画」判为变了（整层重画，代价可接受）。

**换完封面不需要清任何缩略图记忆**（与 ADR 0028 决策 6 相反）：那一段的两个坑是「相册卡片的键就是
相册自己的路径，它不知道封面换了人」与「空相册永远停在文件夹图标上」。事件这边卡片的内容由
`GroupRef` 带着，而旧封面**仍然是组内一张正常的照片**（摊开那一层时还要用它）——
把它从 `_pixmaps` 里清掉反而会让那一层重新解码。所以只走 `_after_events_changed()`（它会重排、
重排会整层重画、重画会按新封面去取图）。

### 8. 搜索里不出现「封面」

`_event_fields()` 只把**每类独有的字段**（`kind.fields`）生成成搜索字段，公共那两条是手写的，
所以封面天然不在候选里。**这一条要写成守护**：将来有人把公共字段也生成一遍，
`#封面 /Users/…` 就会出现在候选列表里，而它是用户永远不会敲的东西。

## 后果

- **`core/events/schema.py`**：`SCHEMA_VERSION` = 2；新增 `_add_column_if_missing` 与 `_migrate`；
  `_drop_script` **删掉**（没有任何隐式路径会重建表了；要重建就写成链条里的一条）。
  `store.attach` 按决策 3 重写版本处理。
- **`core/events/kinds.py`**：`EventField` 多一个 `control: str = ""`；`COMMON_FIELDS` 多「封面」一条。
- **`core/events/store.py`**：新增 `set_cover` / `clear_cover`（**只写那一列**，不拿整份草稿去
  `UPDATE` —— 那会把草稿里缺的字段写成 NULL，是本设计最容易犯的一处错）；`unbind` 与
  `forget_bindings` 顺带清封面；`_apply_moves` 搬封面；`events_for_photos` 的 SELECT 多一列。
  `models.Event` 多一个 `cover_path` 属性（从 `values` 取，空串 → `None`）。
  `models.EventRef` 多一个 `cover_path: str | None = None`（在末尾，位置参数调用点一个字不改）。
- **`core/library/groups.py`**：`GroupRef` 多 `pinned_cover`；`build_groups` 解析封面（决策 7）。
- **`core/library/facade.py`**：新增 `set_event_cover` / `clear_event_cover` 两个入口
  （**不进** `WRITE_METHODS_ON_LIBRARY`：它们不碰用户文件，与 ADR 0028 决策 5 同一处置）。
- **`ui/window/event_flow.py`**：两条常驻动作（照片那一侧带子层、卡片那一侧清除）、
  `_cover_candidate_events`（复用 `events_for_photos`）、写完走 `_after_events_changed()` +
  `_refresh_undo_label()`；`ui/menus.py` 的 `MenuActions` 多两个可空字段。
- **`ui/panels/event_edit.py`**：`control="cover"` 那一行（文件名 + 「清除封面」，新建时禁用）；
  **`_values()` 必须把封面带上** —— 漏了就是「打开面板按一下保存，封面没了」。
- `ui/browser/{model,delegate}.py`、`ui/thumbs/pipeline.py`、`ui/window/thumbnail_flow.py`
  **一字不改**（决策 7）。`core/metadata/{rename,transfer}.py` **一字不改**（决策 4）。
- **搬封面与清封面都要作废 `Library.all_events()` 那份会话缓存**（`_changed()`）。原先 `_changed()`
  只在 create / update / delete / undo 四处调，因为只有那四处会改事件本体；现在封面是**第四类**
  会改那一行的操作，漏掉的症状是「库里已经是新路径、面板里那个封面还指着上一个名字」，
  而用户没有任何办法把它刷新掉（除非切库或改点别的）。
- **界面上那个方法叫 `event_cover_candidate`，不叫 `cover_candidate`** —— `WriteOpsMixin`
  已经有一个同名的那份（相册封面，ADR 0028），而它在 `MainWindow` 的基类列表里排在
  `EventFlowMixin` **前面**：同名的那一个永远不会被调用，**而没有任何报错**。症状是
  「菜单里压根没有这一条」，查起来像是判据写错了。为此加了一条守护
  （`tests/test_guards.py`）：`MainWindow` 的两个 mixin 不许有同名方法。
- **测试**（每条对应上面某个「否则会静默出事」）：

  | 用例 | 守的是什么 |
  |---|---|
  | `test_adding_the_cover_column_keeps_the_events` | 1 → 2 只加列：那一行事件与它的绑定**都还在** |
  | `test_a_newer_events_file_is_left_alone` | 版本更高时不挂、也不删（决策 3） |
  | `test_the_cover_column_is_in_no_kind` | 封面不是任何一类的输入字段（决策 2） |
  | `test_the_cover_is_not_a_search_field` | 候选列表里没有「封面」（决策 8） |
  | 参数化的改名 / 移动到相册 / 撤销 / 相册改名 / 监听器外部改名 | 绑定与封面**一起**搬（决策 4） |
  | 解绑 / 孤儿清理（含 `vanished` 要点头） | 封面随绑定一起清，方向与档位都对（决策 5） |
  | 组封面：指定的那张 / 指定的不在组里 / 空组 | 退回组内第一张（决策 7） |
  | `test_saving_the_panel_does_not_clear_the_cover` | 面板带上封面那一份（决策 2 最后一行） |
  | 菜单：没事件时不出现、列出所属事件、卡片清除 | 落点与 ADR 0028 同一张表（决策 6） |
  | 事件库不可用时 | 封面那一列同样不出现（`events_available` 那条既有判据） |
| 「两个 mixin 不许有同名方法」 | MRO 只认第一个实现，同名的那个**静默**不生效（决策 6 末尾那条） |

- **性能**：`events_for_photos` 多带一列（同一次 JOIN，不多发查询）；`build_groups` 每个成员多几次
  字典查找；渲染路径零变化。
- `README.md` 的事件那节补一句：封面是事件的一列，存在 `events.db` 里。
- v2 可做（各有各的坑，不在本次范围）：面板里的缩略图挑选器（要给几千次 `stat` 与懒加载）、
  封面用「复制到 `.gallery/`」的做法（那是缓存，红线 5 的原罪）、日历格子上标出那周的事件。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| **沿用 ADR 0028 的做法：封面存 `config.toml`，相对路径，不跟着改名走** | 与绑定的路径空间不同源，每次比较都要转换；更要命的是「不跟着走」在事件这边是**用户天天撞的**（分组卡片的封面会随排序字段变），而相册那边只是「换回来又生效」。决策 4 说明了为什么这一边跟得上 |
| 封面存**缩略图副本**（`.gallery/封面/`） | 那是缓存：删掉就得重新生成，而「用户选了哪一张」是数据不是缓存（红线 5、ADR 0028） |
| 封面进 `index.db` | 那是元数据快照，清缓存 / 升版时整表重建（ADR 0043 决策 1） |
| 封面放进 `photo_events`（多一列 `is_cover`） | 一张照片可以同时是两个事件的封面（它既在演出里、也在旅行里），那一列表达不了「两个都是」；而「每事件至多一张封面」正是 `events` 上那一列的含义 |
| 把 `SCHEMA_VERSION` 加一，**照旧重建表** | 用户填的全部事件与绑定当场消失，而它们没有任何一条能从照片重扫出来（ADR 0043 决策 1）。这是本 ADR 里唯一一条「做错了就是数据损失」的改动，所以配一条守护 |
| 版本更高时也重建（今天的隐式条件） | 应用读不懂一份文件，不等于可以删掉它。宁可不挂事件库 |
| 封面可以是**任意一张**照片（不必在这个事件里） | 组卡片只能画组内那一张（流水线拿的是 `PhotoRef`），于是要么画不出来、要么每次现造引用；而且「这个事件的封面是它没有的一张照片」在语义上就不成立 |
| 封面字段进 `kind.fields`（每类一张扩展表） | 封面与类型无关，进扩展表就得 N 张表各一列，而「公共问题不该为每类各写一遍」（ADR 0043 决策 2） |
| 面板里做缩略图挑选器（v1） | 新建事件时候选列表是不完整的（哪些照片会被绑上要到面板关掉才定）；而完整列表意味着几千次 `stat` 加几千次解码，而网格里已经看得见这些照片 |
| 换封面**不**上 ⌘Z（同相册封面） | 事件本体就在撤销栈上（ADR 0043 决策 5），而封面与绑定同库同事务，没有第二个真相源要同步 |
| 移到废纸篓时顺手清封面 | 那会让**同一次删除走两套清理语义**（一条当场清、一条要用户点头），而现象完全一样（决策 5） |
| 换完封面清掉旧封面的缩略图记忆（同 ADR 0028 决策 6） | 旧封面仍是组内一张正常照片，摊开那一层时还要用；卡片的内容由 `GroupRef` 带着，整层重画就够了 |
| 把「封面」做成可搜字段（`#封面`） | 值是绝对路径，没有人会敲；而公共字段将来若被生成进搜索，守护会拦住 |