# ADR 0064：库文件被云盘换掉之后 —— 自愈、日志，以及同一来路的两处静默失败

- 状态：已接受
- 日期：2026-10-17
- 相关 ADR：0038（东西只放两个位置）、0043（事件体系）、0048（事件封面）、0051（后台任务的写入口）、
  0063（影片事件与导入）
- 相关代码：`src/gallery/core/sql.py`、`src/gallery/core/logs.py`、`src/gallery/core/paths.py`、
  `src/gallery/core/library/index.py`、`src/gallery/core/library/facade.py`、
  `src/gallery/core/events/store.py`、`src/gallery/ui/tasks.py`、`src/gallery/cli.py`

## 背景：一次真实的排查

一个库放在 OneDrive 的共享目录里。某次操作之后，导入影片**下载封面成功、事件却从来没建起来**，
横幅只说「出现了一个未预期的问题，详情已写入日志」，而用户手里既没有日志、也没有别的线索；
删掉的事件在界面上「还在」，重复导入还报「库里已经有了」；**关掉软件重开一次，同样的操作就好了**。

查下来是三件事叠在一起：

1. **云盘在运行中换掉了库文件。** `events.db` 被换成同名的另一份（新 inode），而 SQLite 那条连接
   还指着已经被 unlink 的旧 inode：**读照旧给旧内容，写一律失败**，报
   `attempt to write a readonly database`（`SQLITE_READONLY` / 换名时是 `SQLITE_READONLY_DBMOVED`）。
   封面是纯文件操作，所以成功；建事件是写库，所以失败 —— 而记账（journal）在写库之后才写，
   于是日志上一条记录都没有。**重开软件之所以好**：新连接指向磁盘上那个新文件。
2. **那句「详情已写入日志」是空话。** 从访达启动的 `.app` 没有终端，`logger.exception` 写进的
   stderr 等于丢进虚空 —— 用户手上什么都没有。
3. 同一来路还有两处**静默失败**：抓取层只认 `URLError` / `HTTPError`（限速掐连接抛的是
   `RemoteDisconnected` / `IncompleteRead` / `TimeoutError`，**都不是它的子类**），于是网络抖动也走
   那句「未预期的问题」；而删一个已经不在的事件会抛**裸 `KeyError`**，界面只接 `GalleryError`，
   于是「点了删除什么也没发生」（`gallery.core.events.store` 与 `ui/window/event_flow` 各一半）。

**为什么现在必须正面回答**：约束 6 之外还有一条更硬的 —— `events.db` 是 `.gallery/` 里唯一
重建不出来的东西（ADR 0043）。它住在用户的库目录里（ADR 0038 决策 1），而用户的库常常就在云盘上：
**「文件会被别的程序换掉」是这个设计必须面对的常态，不是意外。**

## 决策

### 1. 按**文件身份**判断，读写都先过一道，变了就重开连接

判据是 `(st_dev, st_ino)`（`core/sql.py::file_identity`），**不是 mtime** —— 内容变了（我们自己刚写完、
别的程序改了几行）该照常接着用，只有「已经不是我们打开的那一份」才重开。

两处各自看自己那份文件，互不认识：`FacetIndex` 看 `index.db`，`EventStore` 看 `events.db`
（事件库挂在索引那条连接上，但它是**另一份文件**，索引这边一点察觉都没有）。重开走
`FacetIndex.reopen()`：关掉再开、**锁不变**、**挂钩再跑一遍**（事件库跟着重新挂上），于是
`Library.events` / `Library.facet_index` 这两个「按连接现取」的属性自然交出新连接。

**读也要走**：换掉之后读一直给旧内容，症状正是「删掉的东西还在」「重复导入说库里已经有了」——
只修写的话，用户看到的还是假的。

### 2. 写失败那一次：按**错误码**重开一次再试一遍

`core/sql.py::is_stale_or_locked` 只认错误码（`SQLITE_READONLY` / `SQLITE_READONLY_DBMOVED` /
`SQLITE_BUSY` / `SQLITE_LOCKED`），**不认文案**（文案随 SQLite 版本与语言环境变）。
`Library` 的写入口套一层 `_retries_after_reopening`：判据成立就重开索引连接、把这一趟整个重跑一遍。

**只套单笔事务的写**（建 / 改 / 删事件、绑定、封面、`forget_paths`）。下载封面、粘贴、改名、移动到
相册那些**重跑一遍会多出一个文件**（约束 1「只增不改」），它们靠决策 1 那道前置检查自愈，
写失败就如实报错。换连接也**只能换在整段事务之外**：半途换会把同一笔事务的前半截留在旧连接上。

### 3. 日志落到应用自己的目录里，界面上说清去哪儿看

`core/logs.py::configure_logging()` 在 `<app data>/Gallery/logs/gallery.log` 上装一个轮转文件处理器
（1 MiB × 3），另加一个 stderr 处理器；`cli.main` 在导入 Qt **之前**调用它 —— 起不来那一段最需要日志。

- **不放在 `.gallery/` 下**：日志是应用自己的东西，一个进程也只开一个库，而「库还没打开就出事」
  那一段没有库根可依附。它与 `state.json`、缩略图缓存同在应用状态目录（ADR 0038 那条「东西只放
  两个位置」说的是**库**的数据）。
- 装不上（目录建不出来、没权限）**返回 `None`，不抛也不报**：记不成日志是小事，不让应用起来是大事。
- 界面上那两句「详情已写入日志」改成**带上路径**（`ui/tasks.py` 与 `library/watcher.py`）。

### 4. 删一个已经不在的事件：报中文错，不再抛裸 `KeyError`

`EventStore.delete()` 原先先问 `_kind_of()`（那条查不到就抛 `KeyError`）再看事件是否存在，顺序反了；
界面又只接 `GalleryError`，于是「点了删除什么也没发生」。改成：先看事件在不在，不在就抛一句
中文的 `GalleryError`（「这个事件已经不在了。」），界面据此挂横幅。**界面那一侧同时兜住
`Exception`**：写操作跑在主线程的槽里，逃出去没有第二个人接得住。

### 5. 索引连接只建一条

`Library.facet_index` 原先「没有就建」（没有锁）：几个线程同时第一次进来会各自建一条连接，
而同一个 `index.db` / `events.db` 上两条连接会让其中一条写失败（`database is locked`，同样不是
`GalleryError`）。加一把锁，建与重开都在锁里。

## 后果

- 云盘换掉库文件之后，**下一次读写自己就接上了**，不需要用户重启；`database is locked` 那一次也有
  一次重试的机会。
- **已知限度（不在本轮）**：库文件被**整份删掉**时不做特殊处置 —— 打开库时 SQLite 会建一份空的
  （现状，另开一条再说）；`.gallery/` 跟着云盘同步这件事本身没有变（ADR 0038 已定），本轮只是让
  它在被换掉之后能自己恢复。
- 抓取层的错误面（`RemoteDisconnected` / `IncompleteRead` / 超时）与「在源站查不到番号」那句误导
  文案同属这一族，另行修。
