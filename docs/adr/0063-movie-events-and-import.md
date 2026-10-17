# ADR 0063：影片事件、按番号导入、⌥ 点开影片网站

- 状态：已接受
- 日期：2026-10-17
- 修改：[0047](0047-import-shows-from-yanji.md) 里「导入演出放在「文件」菜单」那一条 —— 所有导入
  收进「视图」右边的顶层「导入」菜单
- 相关 ADR：0043（事件体系）、0048（事件封面）、0034（用外部应用打开）、0038（东西只放两个位置）、
  0044（复制不覆盖）、0018（粘贴只增不改）
- 相关代码：`src/gallery/core/events/jav.py`、`src/gallery/core/metadata/download.py`、
  `src/gallery/core/library/facade.py`、`src/gallery/core/library/config.py`、
  `src/gallery/ui/dialogs/movie_import.py`、`src/gallery/ui/write_ops.py`、
  `src/gallery/ui/window/event_flow.py`、`src/gallery/ui/browser/grid.py`、`src/gallery/ui/viewer/detail.py`

## 背景

Gallery 的事件体系（ADR 0043）为「旅行」「演出」这类**拍出来的**照片服务。还有一类东西不是拍的：
影片本身 —— 用户手里的是一张封面或几张截图，而它们属于某一部具体作品。这类内容有两个特点：
**识别靠番号**（`IPX-660` 这样的编号，而不是日期与地点），**详情在网站上**（封面、发行日期、演员、
类别都得去抓）。既然番号能拼出一个网址，用户自然希望「按住一个键点一下封面就打开那一部」。

约束 6「不往用户的照片目录写非 `.gallery/` 的文件」在这里要正面回答一次：抓下来的封面**要不要**
落进用户的文件夹？这一条与「粘贴」是同一族 —— 用户亲手触发、写出的是**新文件**、绝不覆盖既有
（约束 1「只增不改」）。不落进库的话，封面就进不了网格，也就没有一个「可以点的照片」，而 ADR 0048
的封面渲染本来就要求封面是库里的一张 `PhotoRef`。

## 决策

### 1. 第三类事件「影片」，发行日期复用公共列 `started_on`

`core/events/kinds.py` 加 `MOVIE`（`id="movie"`，`table="event_movie"`），字段：番号 `code`、时长
`duration`、片商 `studio`、发行商 `label`、系列 `series`、导演 `director`、演员 `actors`（多值）、
类别 `tags`（多值）、评分 `rating`。**新表由开库时的 `CREATE TABLE IF NOT EXISTS` 自动建，不需要
`SCHEMA_VERSION` 迁移** —— 那个版本号管的是 `events` 公共表的列，加一类事件只是多一张扩展表。

**发行日期复用公共列 `started_on`**：菜单按它排序、按事件分组的组键是 `起始日期#id`，单开一列会让
影片全落到「无日期」那一组里。代价是面板上这一栏写「开始日期」；要改成「发行日期」得给公共字段加
一层按类型的标签覆盖，本轮不做。

### 2. 抓取从 JavDB，打开用可配置的影片网站 —— 这是两件事

抓取源固定在 `jav.py` 的 `DEFAULT_SOURCE_BASE`（`https://javdb.com`）：它可用、且与同级 AV 项目的
`javmeta.py` 是同一套解析。**打开用的站点**是另一件事，前缀写进 `config.toml` 的 `movie_site`
（默认 `https://jable.tv/videos/`），番号接在后面（小写、带连字符）：
`https://jable.tv/videos/ipx-660/`。

之所以分开：**jable.tv 有 Cloudflare 拦截**（标准库拿不到页面），而打开一个网址只是交给系统浏览器，
不涉及抓取。`movie_site` 是用户会手改的公开配置，非法值给可行动的中文提示（必须以 http(s) 开头）。

### 3. 抓取是纯解析 + 可注入的传输，测试不联网

`core/events/jav.py` 里：`normalize_code`（全角转半角、`IPX660` → `IPX-660`）、`parse_search_items`、
`locate`、`parse_detail`、`JavMovie.to_draft()` 都是纯函数；`JavHttp` 是只依赖标准库的客户端，
`fetch_movie(code, *, client)` 收一个 `JavSource`（`get_text` / `get_bytes`）。测试传假实现、用页面
片段，**永不联网**。站点结构变了改的是这里的正则。

### 4. 封面是写进用户文件夹的**新文件**，走 write_ops + journal

`core/metadata/download.py::save_downloaded_file`：挑一个空着的名字（撞名按访达规矩加编号，绝不
覆盖）、原子写盘（不留半截文件）、在写操作日志里记一条 `download`。**它不进撤销栈**（撤销就是删文件，
而删除只允许用户亲手触发，与粘贴同一取舍）。

界面这一侧只有 `ui/write_ops.py` 调 `Library.import_movies`，且该方法被加进 `WRITE_METHODS_ON_LIBRARY`
—— 与粘贴 / 复制 / 移动到相册同一条「只增不改」的守护。**`import_movies` 不在换层那组取消**：它在动
用户文件，换层时掐掉就是文件写到一半（与 `_write_job` 同一条理由），取消只发生在切库 / 关窗。

### 5. 导入分两步：先抓（只读），再写

`Library.plan_movie_import` 逐条抓元数据、算出与库里已有番号重复的有几部（**只读**）；
`Library.import_movies` 按预演下封面、写文件、`create_event`、绑定、`set_event_cover`。两步都跑在
后台线程里（`_movie_import_job`），有重复先问一句（跳过 / 全部导入 / 取消），与「演迹」导入同一套。
**一次导入不进撤销栈**：它是一串新建事件加几个新文件，承诺 ⌘Z 是空话。

### 6. 按住 ⌥ 点一张照片打开它的影片网站

**不用 ⌘**：网格里 ⌘ 点是「加选 / 减选」（`_EXTEND_MODIFIERS`），是批量操作的基础。⌥ 不在扩选那组
修饰键里、也不被任何手势占用，于是「⌥ 点一格」是一次干净的接管：网格发一个 `movie_open_requested`
信号，窗口解析这一格属于哪个影片事件、拼出网址、交给 `core.external.launch.open_in_browser`。
**不属于任何影片的普通照片静默什么都不做**（轻量手势，不该弹一句「不属于影片」）；真属于影片却缺
番号时才说一句。大图页同样支持 ⌥ 点。**按事件分组时影片卡片本身也支持**：那一格背后就是那个影片
事件，不必再从照片反查 —— 而卡片不是 ``PhotoRef``，只认照片的那条路会把它整个跳过。不是影片的
分组卡片（旅行、演出）与普通照片同一取向，⌥ 点静默。

### 7. 导入进顶层「导入」菜单，摆在「视图」右边

菜单栏在「视图」之后新增顶层「导入」，把原在「文件」里的「从「演迹」导入」也挪进来，与「导入影片…」
并列（两件都是「把外面的东西拿进来」）。菜单栏这几条都是常驻的，事件库用不上时整层置灰（判据只有
`_events_usable` 一处）。

## 后果

- **约束 6 的边界再确认一次**：用户亲手触发的导入会往照片文件夹里新增一个封面文件（与粘贴 / 复制
  同一族），**不是**扫描 / 索引 / 预览那种「顺手写」；写盘走 core、只增不改、必记 journal，且界面
  调用点只在 `ui/write_ops.py`。
- **新的公开配置** `movie_site`（`config.toml`），以及 `Library.movie_url(event)`。
- **新的后台任务槽** `_movie_import_job`（切库 / 关窗取消，不在换层那组）；宿主耦合面新增
  `_current_folder` / `_movie_import_job`（write_ops）与 `_focus_row` / `_import_movies_action` /
  `import_movies_from_dialog`（event_flow），回归测试表随之更新。
- **`WRITE_METHODS_ON_LIBRARY` 多一条 `import_movies`** —— 漏掉它，任何界面模块都能往用户的照片
  目录里落文件。
- 抓下来的封面会和用户的私人照片一起出现在网格里，这是「存到当前目录」的必然结果，也是 ⌥ 点开
  能生效的前提。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 抓 jable.tv | 它有 Cloudflare 挑战，标准库拿不到页面；要抓得引无头浏览器或强制代理，代价远大于收益 |
| 封面存进 `.gallery/covers/` | 不写用户目录，但封面就进不了网格、没有「可点的照片」，ADR 0048 的封面渲染也要求它是库里的 `PhotoRef` |
| 把影片做成 `PhotoFacets` 的一个标签 | 那是「从照片读出来的元数据」，会被索引重建抹掉（ADR 0043 决策 1）；影片信息是用户数据 |
| 单独开一列 `release_date` 而不复用 `started_on` | 菜单排序与按事件分组都读 `started_on`，影片会全落到「无日期」一组 |
| ⌘ 点打开网站 | 网格里 ⌘ 点是加选 / 减选，抢过来会让影片照片无法参与批量操作 |
| 打开网址时把完整 URL 存进事件 | 站点前缀可配置，存了完整 URL 就改不动前缀了；只存番号、打开时现拼 |
| 导入进撤销栈 | 撤销一次导入要删掉刚写的封面文件，违反「删除只允许用户亲手触发」 |
