# ADR 0043：事件体系 —— 事件存在 Gallery 自己的库里，绝不写进照片

- 状态：已接受
- 日期：2026-10-06
- 修改：[红线 5](../README.md) 里「`.gallery/` 中除 `config.toml` 外都是缓存」那一条 —— 事件数据是
  它的第一个例外
- 相关 ADR：0009（分组与排序的 facet 机制）、0026（`photos` 文件表与检索）、0038（东西只放两个位置、
  不开 WAL）、0031（元数据编辑：一个对话框只写一次）、0032（拍摄时间与时区）、0040（筛选的两种作用域）
- 相关代码：`src/gallery/core/events/{kinds,store}.py`、`src/gallery/core/library/index.py`（`attach` 挂钩）、
  `src/gallery/core/library/{groups,search,facade}.py`、`src/gallery/core/metadata/facets.py`（事件那个字段）、
  `src/gallery/core/metadata/{journal,undo,rename}.py`、`src/gallery/ui/panels/event_edit.py`、
  `src/gallery/ui/window/event_flow.py`、`src/gallery/ui/window/ordering.py`（背景那一趟顺带取事件）

## 背景

照片除了「什么时候拍的、在哪拍的、什么相机」之外，还常常**属于某件具体的事**：一趟旅行、一次演出、
一场聚会、一次体检、一次马拉松。这些是别的相册软件用「日记 + 相册」两种形态承载的：给一段文字记录
配几张图，或给几张图配一段描述。Gallery 现在只有元数据，没有这一层，于是「去年那次京都」只能靠
文件夹或关键词硬凑，而文件夹搬家就散、关键词要一个个敲。

要加这一层，有三件事必须先想清楚，而它们都不是界面问题：

1. **事件的字段每一类都不一样**（旅行有目的地与同行者，演出有场次与场馆，医疗有科室与结论），
   所以它天然不像 facet 那样一张扁平的表。
2. **一张照片可以同时属于几件事** —— 同一场音乐节既是一次演出，也在一次旅行的范围里。
3. **事件是用户手写的数据，而 `.gallery/` 里除 `config.toml` 外全是缓存**（红线 5）：删掉之后
   必须能靠重新扫描恢复等价状态。事件恢复不出来。

第 3 条是本 ADR 的主要约束，下面决策 1 与决策 2 都是它推出来的。

## 决策

### 1. 事件落在独立文件 `.gallery/events.db`，索引连接时把它 `ATTACH` 进来

事件数据不进 `index.db`。理由有两条，第二条是硬的：

- `FacetIndex` 的表结构版本一对不上就**整表重建**（`index.py` 的 `_open()`：`DROP TABLE IF EXISTS
  facets; DROP TABLE IF EXISTS photos;`），而事件没有任何一条能从照片重扫出来 —— 它只能重建出
  一张空表。`SCHEMA_VERSION` 将来每升一次（facet 加字段就要升），用户的全部事件就没了。
- 绑定的存放位置有唯一可行的答案：**独立的连接表**。而 `photos` 表不能放 —— `store_photos` 写的是
  `INSERT OR REPLACE INTO photos (path, size, mtime_ns, name, folder, suffix)`，`REPLACE` 覆盖**整行**，
  不在列清单里的列一律回默认值。于是文件一改（大小或 mtime 变），下一轮 `store_photos` 就把绑定抹成
  NULL。这不是理论风险，是那句 SQL 的字面意思。

于是 `.gallery/events.db` 是第二个 SQLite 文件。它在 `FacetIndex` 建连接时被 `ATTACH` 成 `ev`，于是
**SQL 层仍然是一张库**（`#目的地 京都` 能走子查询，见决策 6），而**生命周期上仍是两个文件、两份
`user_version`**。

`ATTACH` 有三件必须分别处理、否则会静默出事的事：

| 事项 | 不这么做的后果 |
|---|---|
| `PRAGMA user_version` 与 `journal_mode` 都是**每个库各一份**，事件那份必须写成 `PRAGMA ev.user_version` | 裸写会读写索引的版本号，事件表的版本检查每开一次都「碰巧通过」或整表重建一次 |
| events.db 是**数据**不是缓存，不跟着 `_relax_durability` 降级 `synchronous`；但仍钉 `journal_mode=DELETE` | 跟着降级 → 一次断电丢事件；用 WAL → `-wal` / `-shm` 跟着用户的库在云盘上互相覆盖（ADR 0038 决策 5 的理由同样成立） |
| 挂不上时只记一条日志，索引其余功能照常，「记录为」那一行随之不出现 | 配置目录建不出来（只读卷 / SMB）时整个浏览功能瘫掉 |

`.gallery/` 建不出来时 events.db 同样建不出来，所以事件功能整体不可用，而那正是红线 7 说的
「自动降级 + 横幅说明」，不是弹窗。

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

`id` **必须 `AUTOINCREMENT`**：`INTEGER PRIMARY KEY` 就是 rowid 的别名，SQLite 用 `max(rowid)+1`
取新值，于是「删掉最大的那个 → 新建事件拿到同一个 id → ⌘Z 恢复删除时把刚建的那个覆盖掉」是一条
真实可走的损坏路径。`AUTOINCREMENT` 靠 `sqlite_sequence` 保证 id 单调不复用。

日期存 ISO 文本而不是拆成年月日三列：范围查询直接可比较，且不像 facet 那样要重建整个索引。

### 3. 事件的定义由一处描述符生成，面板与列名都不许手写

`core/events/kinds.py` 里 `EventKind`（id / label / table / fields）与 `EventField`
（id / label / column / hint / required）。输入面板**由 `fields` 生成**，SQL 的列名**由 `column`
拼出来**。

这是 ADR 0026 教训的正面写法：那一轮把拍摄时间的列名写成了 `f.captured_at`（那是 `PhotoFacets` 上的
属性名，列名其实是 `captured`），解析层与生成 SQL 两层都察觉不到，SQLite 直到执行才说
`no such column`，界面上表现为「一条都搜不出来」——与「条件太严」长得一模一样。所以每加一个字段要
配一条守护：**拿真 schema 把每个声明字段的列跑一遍**。

### 4. 事件数据**绝不写进照片文件**

绑定与事件定义都只落在 Gallery 自己的库里，不写 XMP、不写 IPTC、不起 exiftool。

「记录为」是一个高频、一次性、可撤销的整理动作，让它去动用户的原文件是本末倒置：一次写回要起一到
两次子进程、要 journal、要回读校验、要在没装 exiftool 时降级成只读，而且要处理「这张照片正被
iCloud 占着」这类失败。可撤销性反而更差 —— 撤销一次「记录为」要不要再去动一次照片？而写在数据库里
撤销就是几行 INSERT。

代价是**换软件或清掉 `.gallery/` 就带不走这些事件**（红线 6 让步的地方仅此一处：新写的文件仍在
`.gallery/` 内）。这一条将来若要改，必须单开 ADR 并按红线 4 的全套要求做。

### 5. 事件本体可 ⌘Z，绑定关系不进栈；删除要确认框

- **编辑与删除**上撤销栈。事件是唯一一份不可重建的数据，误删没有退路。删除记录的前像**含绑定清单**：
  `ON DELETE CASCADE` 会连带删掉几十条 `photo_events`，撤销只恢复事件本体的话，事件回来了、照片关联
  没了，而用户看不出来（要等到某天按事件筛才发现少了一堆）。
- **创建**不上栈，与 `ACTION_CREATE`（新建相册）同款：撤销它就是删，而「新建」那一刻用户还没往里放
  东西。
- **绑定 / 解绑**不上栈：反向动作同样是一键（再点一次「记录为 → 无」），量大且不必留凭据。
- **删除要确认框**。`move_to_trash` 不确认的理由写在它的 docstring 里 ——「废纸篓本身就是那层保险，
  ⌘Z 再是一层」。事件两样都没有。

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

`search.py` 只多两类 `SearchField` 与 `_clause` 的一个分支。`#目的地` 与 `#事件` 分开是有意的：
后者是所有类型统一的「叫什么」，前者是旅行特有的「去了哪」；将来加演出就多一组它自己的字段，字段表
由 `EventKind.fields` 生成。

`#事件` 与 `#目的地` 因此都是子串匹配 —— 与现有的文本一族字段一致（SQLite 的 `LIKE '%q%'` 一律全表
扫，铺索引也用不上）。

### 7. 分组：`FacetInput` 多带一份事件，而不是给照片多一个标签

「按事件分组」**不走 `PhotoFacets` / `WANTED_TAGS` / 索引表那一套**。那套是「从照片读出来的元数据」，
事件不是；把用户手写的数据塞进可重建的缓存表，等于让它被下一次索引重建抹掉（决策 1 的同一条理由）。

接法是 `FacetInput` 多一个带默认值的字段（其余 21 个 facet 一字不改），`build_groups` 多一个
`event_values` 映射参数（**算法本身一行不改** —— 它多归属的内层循环本来就是为关键词写的）。
组键取 `起始日期#事件 id`：`_date_group_order` 本来就把键里的数字全取出来拼成一个数，于是日期在前、
同日期按 id 兜底，一个字都不用改；没填日期的那一组用一个专属 `group_order` 让它沉底。

`needs_metadata`（facets.py）要为它开口子：判据是「不在 `FILE_FACET_IDS` 里就要读 EXIF」，而事件
既不在文件上也不在 EXIF 里 —— 照现状，用户点「按事件分组」会先起一个后台任务把整库 EXIF 重读一遍 +
报一条「正在读取 N 张照片…」，而事件值只是几毫秒的 SQL。

事件的数据分两层：**事件本体只有几十条，整个缓存在会话里**（菜单的第二三层、面板的初始值、分组标签
全从它来，零 IO）；**绑定表只走 SQL**，而且必须跟着 `_start_facet_read` 那个后台任务一起取 ——
分组是在主线程同步算的，不能在那里现发一次随层大小线性增长的查询。

### 8. 改名要把绑定一起搬

红线 1 豁免的那一次改名会改路径，而绑定是按 `path` 记的。facets / photos 现在靠 `(size, mtime_ns)`
让 facet 行自动过期、`forget_paths` 清孤儿行，所以改名丢索引对用户无感；**事件绑定是用户一份心意，
不能这么兜**。所以 `core/metadata/rename.py` 在同一次改名里把 `photo_events` 里那一行搬过去 ——
纯写我们自己的库，不碰用户文件，红线 1 管不到这里。守护测试钉住「改名之后绑定还在」。

### 9. 「记录为」的三级菜单：三层 QMenu 先全建出来再填

右键菜单「记录为 → 旅行 → 京都五日」看起来是普通的懒填子菜单（照 `build_lazy_album_picker`），但
有一个坑：`menu_style.apply_style()` 是在菜单建好时**一次性**递归给所有子菜单下发样式表的。
所以三层菜单必须**先全建出来**（可以都是空的），下发样式之后才在 `aboutToShow` 里填内容 ——
建完再补第三层，那一层会是一张系统默认的白底菜单。

某类型下零事件时那一层 `menu.hide()`（与相册清单一支同款：空框点开只会让人以为软件坏了）。

## 后果

- **红线 5 有了一个例外**，且写成可执行的断言：清缓存与索引重建之后，事件与绑定还在
  （`clear()` 与 `_open()` 的 `DROP` 语句都点名表名，不点名就不会误删；守护测试盯的是这一点，
  而不是「代码里写了 DROP IF EXISTS」）。红线 6 不变：新写的文件仍在 `.gallery/` 内。
- `core/library/index.py` 的 `FacetIndex.__init__` 多一个 `attach` 钩子。**`index.py` 不认识 events** ——
  接线在 `Library.facet_index`（facade）那一处，钩子不传就没有事件，于是 `index.py` 的既有测试一字不改。
- `core/library/search.py` 的 SQL 引用了 `ev.*`，所以它的守护测试要拿一个**挂着 events.db 的真连接**
  去跑（现成的范式：`test_every_searchable_field_survives_the_real_schema`）。
- `ui/write_ops.py` **不新增**会动用户文件的入口（事件不动用户文件），但 ⌘Z 那一条要走它 ——
  `undo_last_write` 是撤销栈唯一的入口。事件的其它入口在 `ui/window/event_flow.py`。
- 事件分组卡片上多两行菜单（「编辑事件…」「从这次事件移出」），只在 `group.facet == 'event'` 的卡片上
  出现。组键里带着事件 id，解析出来就能问会话里那份缓存要那条事件。
- 「未归入事件」那一个组照现有机制出现（与「时间未知」同款）。几千张未归入的照片堆成一张卡片是必然的，
  为它改 facet 机制要连带动关键词、拍摄时间等每一个 facet 的守护。
- 分组与日历继续互斥（`ordering.py` 里那道闸），事件分组沿用同一道，不新增风险。
- v1 之外（各有各的坑，不在本次范围）：从别的软件导入、日历上标出那周的事件、格子角上的事件徽标。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 给 `photos` 表加 `event_kind` / `event_id` 两列 | `store_photos` 的 `INSERT OR REPLACE` 只写那六列，**每次扫描把绑定抹成 NULL**（决策 1）；而且它是缓存 |
| 一张照片只能属于一个事件 | 「同一场音乐节既是演出、也在一次旅行的范围内」是成立的；限制成一个的话第二次「记录为」要么静默覆盖要么先取消，而改绑在两种形状下都是一键，多归属的成本几乎为零 |
| 每类事件一张表，从零开始（无公共脊柱） | 「所有事件」「这张照片属于谁」要 UNION N 张表，而 N 随类型增长。公共问题不该为每类各写一遍 |
| 一张 `events` 表 + JSON 列 | `json_extract` 能不能用要写成守护测试；按字段筛走不了索引；改字段名之后旧数据没人清理 |
| `.gallery/events/*.toml`，事件定义人可读 | 定义人可读确实是好事，但绑定是高频读写且要被 SQL 查的东西。混合方案（定义用文件、绑定用库）等于两套读法 |
| 事件走 `PhotoFacets` / `WANTED_TAGS` / XMP | 决策 1 与决策 4：它会被索引重建抹掉，而写进照片要付全额写回的代价与更差的撤销性 |
| 事件与绑定一起放进 `index.db` | 一个文件很省事，但用户数据与可重建缓存同处一处，将来任何一次「顺手清一下缓存」的改动都会误伤它，而那时没有任何测试会红 |
| 筛选在 Python 层取两个库的交集，索引层零耦合 | 「事件 + 其他条件」的组合查询要两步走，大结果集慢。ATTACH 换来的是一行 SQL，代价只是 `index.py` 外面多一个钩子 |
| 旅行的成员关系**由日期范围推导**，不存绑定 | 表达不了「这 200 张里只有 37 张是这趟的」，而那正是旅行的实际情形。改成「区间 + 例外表」是两套机制并存的复杂度 |
| 绑定按内容哈希而不是路径 | 一次绑定要读整个文件算哈希，而绑定是高频操作；改名搬迁（决策 8）便宜得多 |
| 新建事件也上 ⌘Z 栈 | 撤销它就是删。栈上会出现「建 → 改 → 撤 → 变回没建过」的绕路，与新建相册的既有取舍一致 |
| 删除事件不确认，靠 ⌘Z 兜 | 废纸篓那支能这么干是因为废纸篓本身就是一层保险，事件没有 |
| 让「未归入事件」那组默认不出现 | 要改 facet 机制本身（多一个「空组要不要出现」的开关），而它影响每一个现有 facet；杂物堆难看，但它是既有行为、零新代码 |
