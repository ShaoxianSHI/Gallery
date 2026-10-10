# ADR 0043：事件体系 —— 事件存在 Gallery 自己的库里，绝不写进照片

- 状态：已接受
- 日期：2026-10-06
- 修改：[约束 5](README.md) 里「`.gallery/` 中除 `config.toml` 外都是缓存」那一条 —— 事件数据是
  它的第一个例外
- 相关 ADR：0009（分组与排序的 facet 机制）、0026（`photos` 文件表与检索）、0038（东西只放两个位置、
  不开 WAL）、0031（元数据编辑：一个对话框只写一次）、0032（拍摄时间与时区）、0040（筛选的两种作用域）
- 相关代码：`src/gallery/core/events/{kinds,store}.py`、`src/gallery/core/library/index.py`（`attach` 挂钩）、
  `src/gallery/core/library/{groups,search,facade}.py`、`src/gallery/core/metadata/facets.py`（事件那个字段）、
  `src/gallery/core/metadata/{journal,undo,rename}.py`、`src/gallery/ui/dialogs/event_edit.py`、
  `src/gallery/ui/window/event_flow.py`、`src/gallery/ui/window/ordering.py`（背景那一趟顺带取事件）

## 背景

照片除了「什么时候拍的、在哪拍的、什么相机」之外，还常常**属于某件具体的事**：一趟旅行、一次演出。
别的相册软件用「日记 + 相册」两种形态承载这一层，Gallery 只有元数据，于是「去年那次京都」只能靠文件夹
或关键词硬凑。最大的约束不是界面问题而是数据：事件是**用户手写的数据**，而 `.gallery/` 里除
`config.toml` 外全是缓存（约束 5）—— 别的数据删掉之后重扫能恢复等价状态，事件恢复不出来。
决策 1 与决策 2 都是从这一条推出来的。

## 决策

### 1. 事件落在独立文件 `.gallery/events.db`，索引连接时把它 `ATTACH` 进来

不进 `index.db`：`FacetIndex` 的表结构版本一对不上就**整表重建**（`index.py` 的 `_open()`：`DROP TABLE
IF EXISTS facets; DROP TABLE IF EXISTS photos;`），而事件只能重建出一张空表 —— `SCHEMA_VERSION` 将来
每升一次（facet 加字段就要升），用户的全部事件就没了。绑定也不放进 `photos` 表：`store_photos` 写的是
`INSERT OR REPLACE INTO photos (path, size, mtime_ns, name, folder, suffix)`，`REPLACE` 覆盖**整行**，
文件一改（大小或 mtime 变）就把绑定抹成 NULL。

于是 events.db 是第二个 SQLite 文件，在 `FacetIndex` 建连接时被 `ATTACH` 成 `ev`：**SQL 层仍然是一张库**
（`#目的地 京都` 能走子查询，见决策 6），**生命周期上仍是两个文件、两份 `user_version`**。
`ATTACH` 有三件必须分别处理、否则会静默出事的事：

| 事项 | 不这么做的后果 |
|---|---|
| `PRAGMA user_version` 与 `journal_mode` 都是**每个库各一份**，事件那份必须写成 `PRAGMA ev.user_version` | 裸写会读写索引的版本号，事件表的版本检查每开一次都「碰巧通过」或整表重建一次 |
| events.db 是**数据**不是缓存，不跟着索引的 `_relax_cache_durability` 降级 `synchronous`；但仍保持 `journal_mode=DELETE` | 跟着降级 → 一次断电丢事件；用 WAL → `-wal` / `-shm` 跟着用户的库在云盘上互相覆盖（ADR 0038 决策 5 的理由同样成立） |
| 挂不上时只记一条日志，索引其余功能照常，「记录为」那一行随之不出现 | 配置目录建不出来（只读卷 / SMB）时整个浏览功能瘫掉 |

`.gallery/` 建不出来时 events.db 同样建不出来，事件功能整体不可用 —— 那正是约束 7 说的「自动降级 +
横幅说明」，不是弹窗。

### 2. 表结构：公共脊柱 + 每类一张扩展表 + 一张绑定表

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    started_on  TEXT,               -- 'YYYY-MM-DD'
    ended_on    TEXT,
    note        TEXT,
    created_at  INTEGER NOT NULL,
    modified_at INTEGER NOT NULL
);
CREATE TABLE event_trip (          -- 只放这一类独有的字段，与 events 一对一
    event_id     INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    destinations TEXT, companions TEXT, transport TEXT, lodging TEXT,
    cost         REAL, rating INTEGER
);
CREATE TABLE photo_events (        -- 绑定
    path     TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    PRIMARY KEY (path, event_id)
);
```

`id` **必须 `AUTOINCREMENT`**：`INTEGER PRIMARY KEY` 就是 rowid 的别名，SQLite 用 `max(rowid)+1` 取新值，
于是「删掉最大的那个 → 新建事件拿到同一个 id → ⌘Z 恢复删除时把刚建的那个覆盖掉」是一条真实可走的损坏
路径；`AUTOINCREMENT` 靠 `sqlite_sequence` 保证 id 单调不复用。日期存 ISO 文本而不是拆成年月日三列：
范围查询直接可比较，且不像 facet 那样要重建整个索引。

### 3. 事件的定义由一处描述符生成，面板与列名都不许手写

`core/events/kinds.py` 里 `EventKind`（id / label / table / fields）与 `EventField`（id / label /
column / hint / required）。输入面板**由 `fields` 生成**，SQL 的列名**由 `column` 拼出来**。
这是 ADR 0026 教训的正面写法：那一轮把列名写成了属性名 `f.captured_at`（列名其实是 `captured`），
解析层与生成 SQL 两层都察觉不到，SQLite 直到执行才报错。所以每加一个字段要配一条回归测试：
**拿真 schema 把每个声明字段的列跑一遍**。

### 4. 事件数据**绝不写进照片文件**

绑定与事件定义都只落在 Gallery 自己的库里，不写 XMP、不写 IPTC、不起 exiftool。「记录为」是一个高频、
一次性、可撤销的整理动作，让它去动原文件要付一次写回的全额代价（起一到两次子进程、journal、回读校验、
没装 exiftool 时降级、照片正被 iCloud 占着），而可撤销性反而更差 —— 撤销一次「记录为」要不要再去动
一次照片？写在数据库里撤销就是几行 INSERT。

代价是**换软件或清掉 `.gallery/` 就带不走这些事件**（约束 6 让步的地方仅此一处：新写的文件仍在
`.gallery/` 内）。这一条将来若要改，必须单开 ADR 并按约束 4 的全套要求做。

### 5. 事件本体可 ⌘Z，绑定关系不进栈；删除要确认框

- **编辑与删除**上撤销栈：事件是唯一一份不可重建的数据，误删没有退路。删除记录的前像**含绑定清单** ——
  `ON DELETE CASCADE` 会连带删掉几十条 `photo_events`，撤销只恢复事件本体的话，照片关联就没了。
- **创建**与**绑定 / 解绑**不上栈：撤销创建就是删，而「新建」那一刻用户还没往里放东西；解绑的反向动作
  同样是一键（再点一次「记录为 → 无」），量大且不必留凭据。
- **删除要确认框**。`move_to_trash` 不确认是因为「废纸篓本身就是那层保险，⌘Z 再是一层」，事件两样都没有。

journal 的 `WriteRecord` 因此加一个**通用**的 `payload` 字段（只有事件记录在用）：journal 不该长出
事件的领域知识，而事件的前像又是「一堆自己解释的键值」。

### 6. 筛选：事件字段的 `SearchField.column` 写子查询，查询构造器不动

```sql
-- #事件类型 旅行
EXISTS (SELECT 1 FROM ev.photo_events pe JOIN ev.events e ON e.id = pe.event_id
        WHERE pe.path = p.path AND e.kind = ?)
-- #目的地 京都
EXISTS (… JOIN ev.event_trip t ON t.event_id = pe.event_id
        WHERE pe.path = p.path AND t.destinations LIKE '%京都%' ESCAPE '\')
```

`search.py` 只多两类 `SearchField` 与 `_clause` 的一个分支。`#目的地` 与 `#事件` 分开是有意的：后者是所有
类型统一的「叫什么」，前者是旅行特有的「去了哪」；将来加演出就多一组它自己的字段，由 `EventKind.fields`
生成。两者都是子串匹配 —— 与现有的文本一族字段一致（`LIKE '%q%'` 一律全表扫，铺索引也用不上）。

### 7. 分组：`FacetInput` 多带一份事件，而不是给照片多一个标签

「按事件分组」**不走 `PhotoFacets` / `WANTED_TAGS` / 索引表那一套**：那套是「从照片读出来的元数据」，
把用户手写的数据塞进可重建的缓存表等于让它被下一次索引重建抹掉（决策 1 的同一条理由）。接法是
`FacetInput` 多一个带默认值的字段（其余 21 个 facet 一字不改），`build_groups` 多一个 `event_values` 映射
参数 —— **算法本身一行不改**，它多归属的内层循环本来就是为关键词写的。组键取 `起始日期#事件 id`：
`_date_group_order` 本来就把键里的数字全取出来拼成一个数，于是日期在前、同日期按 id 回退；没填日期的那
一组用一个专属 `group_order` 让它沉底。

两处配套：`needs_metadata`（facets.py）要为事件开口子（判据是「不在 `FILE_FACET_IDS` 里就要读 EXIF」，
而事件既不在文件上也不在 EXIF 里，不开口子就会为几毫秒的 SQL 重读一遍整库 EXIF）；**事件本体只有几十条，
整个缓存在会话里**（零 IO），**绑定表只走 SQL** 且必须跟着 `_start_facet_read` 那个后台任务一起取 ——
分组是在主线程同步算的，不能在那里现发一次随层大小线性增长的查询。

### 8. 改了路径就要把绑定一起搬 —— 四条路，一个入口

绑定是按 `path` 记的，而 facets / photos 靠 `(size, mtime_ns)` 让 facet 行自动过期、`forget_paths` 清孤儿
行，所以改路径丢索引对用户无感；**事件绑定是用户一份心意，不能这么兜**。纯写我们自己的库，不碰用户
文件，约束 1 管不到这里。会改路径的操作有四条，全部汇到 `EventStore.follow_moves`（一次事务、一批一起
搬、重复调用是空操作），而不是 `rename.py` 里那个私有的 `_follow_rename`：

| 路 | 谁报出来的 | 为什么必须接 |
|---|---|---|
| 应用内改名 | `core.rename` 同一次调用 | 约束 1 唯一豁免的那一次 |
| 应用内移动到相册 | `core.transfer` 同一次调用 | 同样是用户亲手触发、进撤销栈、改的是绝对路径 |
| 撤销上面两样 | `core.undo` | 撤销是一次改名 / 一次移动；不搬就把事件弄丢 |
| **访达里的改名与拖动** | **只有监听器知道** | 应用内那条路压根看不见它 |

**外部那一条接在 `_on_watch_changed` 里、延后前置条件之前**（`ui/window/watch_flow.py`）：延后只该推迟
「改界面」，不该推迟「修数据」。同理它**不能只依赖监听器**：`recursive = false` 时监听器只盯库根，
子相册里的移动不上报，所以应用内移动仍要在 `core.transfer` 里自己搬一次。**搬两次是安全的** ——
应用内移动不屏蔽监听器回环（新文件的显示靠它），第二次源路径上已经没有绑定，一条都不改。

**不在这一条里的**：应用**没开着**时发生的外部改动（监听器看不见），以及孤儿行怎么回收、要不要提示
用户。那要**删用户填过的数据**，是另一个决定。

### 9. 开库时查孤儿绑定，分「没了」「不知道」与「相册不存在」三档

决策 8 之后只剩一种孤儿：**照片被人在 Gallery 之外删掉或移走**。它不只是垃圾 —— 同一个路径日后被另一张
照片用上时，绑定会**静默附着到不相干的照片上**。所以这一条的全部重量在「凭什么断定它没了」：绑定按
`path` 记，而用户的照片文件夹常在云盘、外接盘、网络共享上，于是「这个路径不存在」至少有两种成因：

| scandir 的结果 | 含义 | 处理 |
|---|---|---|
| 成功 | 这个目录**可读**，是权威答案 | 不在清单里的绑定 = **孤儿，可删** |
| `ENOENT` | 目录确实不在 | **单独一档**，默认不删 |
| 其它 `OSError`（`EACCES` / `EIO` / `ESTALE`） | **不知道** | 不报、不删 |

**分开 `ENOENT` 与其它错误码是全部要点**：挂在**未挂载的外接盘**上的相册报的正是 `ENOENT`，与「相册被删了」
分不出来，必须单独一档、要用户点头 —— 混进「可以放心清」的那一档就是一键抹掉几千条用户自己填的数据。
「空集与 `None` 不是一回事」沿用 `naming.occupied_names` 那套（那里用 `None` 表示「读不了」，
`free_name_in` 整个逻辑架在它上面）。**按父目录分组、一个目录一次 `scandir`**，不逐个 `stat`：绑定分布在
D 个相册里就是 D 次系统调用（`naming.free_name_in` 那次实测 400 项 617 ms → 25 ms）。**库根读不了就整个
不查**；**一条绑定都没有就一个目录都不问** —— 绝大多数库开库时不该有任何磁盘开销。

**每次开库都查一遍**：要抓的正是「Gallery 没开着的时候」发生的改动，记一个「上次查过没有」的标记反而会
漏掉它（绑定表没有变更时间戳）。跑在后台线程里（`TaskRunner`），带 `generation` 前置条件；**切库与关窗
取消** —— 别让一个模态框在用户已经切到别的库之后才弹出来。**确认框一个，分两档说**，默认按钮是「先不清」；
**正文不许提 ⌘Z**（这里没有撤销，`delete_event` 那句在这里是反的），删的是**记录**，横幅里要写清「照片本身
没有动过」。**什么都没找到就彻底静默**：不弹框、不挂横幅、不记日志。

### 10. 「记录为」的三级菜单：三层 QMenu 先全建出来再填

`menu_style.apply_style()` 是在菜单建好时**一次性**递归给所有子菜单下发样式表的，所以三层菜单必须**先全
建出来**（可以都是空的），下发样式之后才在 `aboutToShow` 里填内容。某类型下零事件时那一层 `menu.hide()`
（与相册清单一支同款）。

## 后果

- **约束 5 有了一个例外**，且写成可执行的断言：清缓存与索引重建之后，事件与绑定还在（`clear()` 与
  `_open()` 的 `DROP` 语句都点名表名，不点名就不会误删；回归测试盯的是这一点）。约束 6 不变。
- **新的接口与前置条件**：`FacetIndex.__init__` 多一个 `attach` 钩子（`index.py` 不认识 events ——
  接线在 `Library.facet_index`，钩子不传就没有事件）；`EventStore.follow_moves` / `set_cover` 一类的
  事件写入；`ui/write_ops.py` **不新增**会动用户文件的入口（事件不动用户文件），但 ⌘Z 那一条要走它，
  `undo_last_write` 是撤销栈唯一的入口。`search.py` 的 SQL 引用了 `ev.*`，它的回归测试因此要拿一个
  **挂着 events.db 的真连接**去跑。
- **新的回归测试守护**：`tests/core/events/test_events_and_index.py` 断言索引重建与清缓存不许碰 `ev.*`
  的表名；`orphans.py` 的判定（读文件系统）刻意不放进只碰 SQL 与 journal 的 `store.py`，`Orphan` 类型
  反而放在 `store.py`（它是 `all_bindings()` 造出来的）。
- **界面落点**：删除事件的入口在编辑面板里（`EventEditDialog` 左下角，**只在编辑已有事件时才有** ——
  它给回一个专属的返回值 `DELETE_REQUESTED` 而不是发信号，信号从 `clicked` 同步发出会让确认框嵌套在
  对话框自己的 `exec()` 里面；颜色用主题 `error` 那一档）；菜单栏「编辑」多两项，两层都是**常驻**的，
  所以建出来时是空的、挂在 `aboutToShow` 上填（决策 10 同一理由），灰度判据只有一处
  （`_events_usable` / `_edit_event_usable`），刷新只放在 `_after_events_changed` 里。
- **一次开库多一个后台任务**：D 次 `scandir`，D 是「有绑定的相册数」，而绝大多数库一个绑定都没有。
- 「未归入事件」那一个组照现有机制出现（与「时间未知」同款）；分组与日历继续互斥（`ordering.py` 里
  那道前置条件）。
- v1 之外（各有各的坑，不在本次范围）：日历上标出那周的事件、格子角上的事件徽标。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 给 `photos` 表加 `event_kind` / `event_id` 两列 | `store_photos` 的 `INSERT OR REPLACE` 只写那六列，**每次扫描把绑定抹成 NULL**；而且它是缓存（决策 1） |
| 一张照片只能属于一个事件 | 「同一场音乐节既是演出、也在一次旅行的范围内」是成立的；限制成一个的话第二次「记录为」要么静默覆盖要么先取消，而改绑在两种形状下都是一键 |
| 每类事件一张表，从零开始（无公共脊柱） | 「所有事件」「这张照片属于谁」要 UNION N 张表，而 N 随类型增长。公共问题不该为每类各写一遍 |
| 一张 `events` 表 + JSON 列 | `json_extract` 能不能用要写成回归测试；按字段筛走不了索引；改字段名之后旧数据没人清理 |
| `.gallery/events/*.toml`，事件定义人可读 | 绑定是高频读写且要被 SQL 查的东西。混合方案（定义用文件、绑定用库）等于两套读法 |
| 事件走 `PhotoFacets` / `WANTED_TAGS` / XMP | 决策 1 与决策 4：它会被索引重建抹掉，而写进照片要付全额写回的代价与更差的撤销性 |
| 事件与绑定一起放进 `index.db` | 用户数据与可重建缓存同处一处，将来任何一次「顺手清一下缓存」的改动都会误伤它，而那时没有任何测试会红 |
| 筛选在 Python 层取两个库的交集，索引层零耦合 | 「事件 + 其他条件」的组合查询要两步走，大结果集慢。ATTACH 换来的是一行 SQL，代价只是外面多一个钩子 |
| 旅行的成员关系**由日期范围推导**，不存绑定 | 表达不了「这 200 张里只有 37 张是这趟的」。改成「区间 + 例外表」是两套机制并存的复杂度 |
| 绑定按内容哈希而不是路径 | 一次绑定要读整个文件算哈希，而绑定是高频操作；改名搬迁（决策 8）便宜得多 |
| 新建事件也上 ⌘Z 栈 | 撤销它就是删。栈上会出现「建 → 改 → 撤 → 变回没建过」的绕路（决策 5） |
| 删除事件不确认，靠 ⌘Z 兜 | 废纸篓那支能这么干是因为废纸篓本身就是一层保险，事件没有（决策 5） |
| 删除事件放在「记录为 → 旅行 → 京都五日」那一层里 | 那一层是「**选**这个事件」，末尾挂一个「删它」把两件事混在一起，且子菜单里没有一条该有的视觉提示说它是破坏性的 |
| 删除事件放在事件分组卡片的右键菜单上 | 要先「按事件分组」才看得到那张卡片，而多数时候用户是从别的路径找过来的 |
| 「删除事件…」用 `QDialog` 信号告诉外面 | 信号从 `clicked` 同步发出，确认框会嵌套在对话框自己的 `exec()` 里 —— 两个模态叠着，关闭顺序会乱 |
| 让「未归入事件」那组默认不出现 | 要改 facet 机制本身（多一个「空组要不要出现」的开关），而它影响每一个现有 facet；杂物堆难看，但它是既有行为、零新代码 |
| 孤儿绑定**静默清掉**，或把「相册不存在」那档并进可删那一档 | 这是唯一真正危险的简化。挂在未挂载外接盘上的相册报的正是 `ENOENT`，混进「可以放心清」就是一次不可逆的批量数据损失（决策 9） |
| 孤儿清理记 journal、进撤销栈 | 把绑定恢复到一张不存在的照片上没有意义；而 `unbind` 早就是同一个取舍（决策 5） |
| 按 `(size, mtime_ns)` 那种自动过期的办法处理 | 那条路只认得「这个文件变没变」，认不得「它还在不在」（决策 9） |