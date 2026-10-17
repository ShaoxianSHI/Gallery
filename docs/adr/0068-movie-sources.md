# ADR 0068：影片导入支持 YouTube 与 Bilibili：统一的来源与编号

- 状态：已接受
- 日期：2026-10-17
- 修改：[0063](0063-movie-events-and-import.md) 决策 1 的字段表（`片商` 改名 `作者`、新增 `来源`、`番号` 改称
  `编号`）与决策 2 的「抓取源固定在 JavDB」
- 相关 ADR：0043（事件体系）、0048（事件封面）、0038（东西只放两个位置）、0065（按事件字段分组）
- 相关代码：`src/gallery/core/events/sources.py`、`src/gallery/core/events/transport.py`、
  `src/gallery/core/events/youtube.py`、`src/gallery/core/events/bilibili.py`、
  `src/gallery/core/events/jav.py`、`src/gallery/core/events/kinds.py`、
  `src/gallery/core/events/schema.py`、`src/gallery/core/library/facade.py`、
  `src/gallery/ui/dialogs/movie_import.py`、`src/gallery/ui/write_ops.py`

## 背景

ADR 0063 把「影片」做成了「**按番号**抓封面与元数据」。用户手上还有另一大批同性质的东西：
YouTube 与 B 站的视频 —— 同样是「用户手里只有一张封面或几张截图，详情在网站上」，
同样有一个唯一编号，用户自然也希望「粘贴一批链接就变成一格格能点开的照片」。

调研过三件事，结论都记在下面：

**一、两边都有唯一编号，但它们不是番号那种形状。**

| | 形状 | 归一规则 |
|---|---|---|
| JavDB 番号 | `IPX-660` | 全角转半角、大写、`IPX660` → `IPX-660` |
| YouTube | `videoId`，11 位 `[A-Za-z0-9_-]` | **大小写敏感**，不做任何改写 |
| Bilibili | `BV` + 9 位（共 12 位，base58，不含 `0 I O l`） | **大小写敏感**；旧的 `av`（纯数字 aid）可纯算术互转 |

实测：`BV1GJ411x7h7` 与 `BV1gj411X7h7` 是**两部不同的片子**（aid `80433022` 与 `444729062`）。
所以 0063 那条「大写、去连字符」的归一**一个字都不能复用**到这两边。

**二、两边都能只靠标准库拿到封面与元信息，而且返回的是 JSON 而不是 HTML。**

- YouTube：官方 oEmbed（`/oembed?url=...&format=json`，**免 key、免配额**）给出标题、频道、封面。
  时长 / 发布日期 / 标签**不在 oEmbed 里**，要另外读 watch 页里那份 `ytInitialPlayerResponse`。
- Bilibili：`x/web-interface/view?bvid=...` 一个请求给出标题、封面原图、发布时间、时长、UP 主、
  分区、合集、简介；标签在 `x/web-interface/view/detail/tag`。两个接口**游客可用**（实测免 cookie、
  免 WBI 签名）。

**三、第三方源不值得。** yt-dlp 与 `bilibili-api-python` 都能做，但都要引一个很重的依赖
（yt-dlp 解 YouTube 的 JS 挑战还要一个 Deno 运行时）；Invidious / Piped 的公共实例这几年大面积不稳；
`noembed.com` 根本不认 B 站。约束就是「能用官方接口就用官方接口，能用标准库就用标准库」。

## 决策

### 1. 三者共用「影片」这一类事件，新增 `来源` 字段

不新开一类事件。用户手上的东西是同一类（一部作品的封面 + 详情在网站上），而菜单、分组、封面、
⌥ 点开网站那一整套在 0063 里已经建好了；另开一类只会让「按事件分组」多一层没有区别的选项。

`来源` 的值域是 `JavDB` / `YouTube` / `Bilibili` 三选一，**面板上是下拉**（`EventField.choices`），
也是「⌥ 点开哪一页」与「判重」的键。取值用网站自己的名字：这三个都是专有名词，译成中文反而认不出。

### 2. 只抓能抓到的公共字段，抓不到的就留空

三类来源共用一个字段表，**取并集而不是交集**：AV 独有的那几栏（发行商 / 导演 / 演员 / 评分）
留着不动，于是 0063 已有的能力一栏没少；只是 YouTube 与 B 站的卡片上那几栏是空的。

| 字段 | JavDB | YouTube | Bilibili |
|---|---|---|---|
| 编号 `code` | 番号 | videoId | BV 号 |
| 名称（公共列） | 标题 | 视频标题 | 稿件标题 |
| 开始日期（公共列） | 发行日期 | 发布日期 | 发布时间 |
| 时长 `duration` | 分钟 | 秒 ÷ 60 | 秒 ÷ 60 |
| 作者 `creator` | 片商 | 频道 | UP 主 |
| 系列 `series` | 系列 | — | 合集 |
| 标签 `tags` | 类别 | 关键词 | 标签 |
| 发行商 `label` / 导演 `director` / 演员 `actors` / 评分 `rating` | 有 | — | — |

**`片商` 改称 `作者`、`番号` 改称 `编号`、`类别` 改称 `标签`**：这三栏在三个来源里都有对应物，
用 AV 的词去装频道的名字会让用户看不懂（「片商：Rick Astley」）。改名只动 id / 列名 / 文案，
不动用户已经填了的数据 —— 见决策 7 的迁移。

**时长统一用分钟**（既有那一栏就是分钟）：YouTube 与 B 站给的是秒，除 60 四舍五入；
不足半分钟的就是空。为此把这一列的单位改成秒（或给 AV 的那 170 分钟补一次乘法）都不值：
那一栏是给人看的，而「约几分钟」正是人看的粒度。

### 3. 编号怎么认：先认网址，再认形状，剩下的当番号

用户粘进来的是一坨自由文本，每一行可能是番号、是链接、也可能是一句抱怨。识别规则按可靠性排序：

1. **网址**：域名是 `youtube.com` / `youtu.be` → YouTube，`bilibili.com` → Bilibili，
   编号从路径或查询串里取。
2. **`BV` + 10 位合法字符**、**`av` + 数字** → Bilibili（这两个形状与番号不可能撞）。
3. **11 位的 `[A-Za-z0-9_-]`，且不是番号的形状**（`^[A-Z]+-\d+$`）→ YouTube。
   这一条是为了「用户手上只有一串 id」的情形；括号里那个排除条件挡住 `ABCDE-12345` 这类
   恰好 11 位又长成番号的编号。
4. **其余** → JavDB 番号。

**不做搜索**：番号要搜是因为它不是网址；YouTube 与 B 站的编号本身就是网址的一部分，
拿到编号就能直达。于是这两个来源少一次请求，也少一整类「搜到多条分不清」的失败。

**不支持 `b23.tv` 短链**：展开它要发一次网络请求，而识别这一步是纯函数（不联网）。用户从手机
分享出来的往往是短链，那时给的提示要说得明白（「短链请先在浏览器里打开，把完整地址粘进来」）。

### 4. 传输共用一份，解析每个来源一份

0063 里那套 HTTP（浏览器 UA、限速、退避重试、gzip / deflate）**升成三个来源共用**的
`core/events/transport.py`：一份 `HttpTransport`，每个来源只给自己的 `base` / `Referer` / cookie。
它已经是「与反爬相处」的全部策略，抄三份就是三处会各自漂。

解析仍然是纯函数（HTML 或 JSON → 一份元数据），每个来源一个模块，测试传假传输、**永不联网** ——
与 0063 决策 3 同一分工。

抓到的结果共用一份 `MovieInfo`（值对象，带 `to_draft()`）：**字段到事件的那份映射只有一处**，
三个来源只负责把网页读成它。抓取分两步走：**标题与封面走官方接口**（稳），**时长 / 日期 / 标签
再去补一刀，补不到就算了** —— 补的那一刀是抓 HTML，站方改版就会断，而它断了不该让整次导入失败。

### 5. 打开影片网站按来源拼

`config.toml` 的 `movie_site` 仍然只管 JavDB（那一栏可以换成 jable.tv 之类的镜像），
YouTube 与 B 站的地址形状是固定的，没有可配置的余地：

```
JavDB     https://jable.tv/videos/ + ipx-660 + /
YouTube   https://www.youtube.com/watch?v= + <videoId>
Bilibili  https://www.bilibili.com/video/ + <BV 号> + /
```

### 6. 封面写盘那一套原样复用

`save_downloaded_file`（挑空名字、原子写、记一条 `download`）、`journal`、绑定、设封面
**一行都不改**：从 `i.ytimg.com` 与 `hdslb.com` 下来的字节与从 JavDB 下来的没有区别。
「只增不改」的守护（`WRITE_METHODS_ON_LIBRARY` 里的 `import_movies`）仍然只有一处调用点。

封面文件名还是「编号 + 后缀」。YouTube 的 `maxresdefault.jpg` 不是每个视频都有，按
`maxresdefault → hq720 → sddefault → mqdefault` 逐档回退，最后垫上 oEmbed 给的那一个
（它是 `hqdefault`，4:3、上下带黑边，但一定有）；B 站的 `pic` 给的就是原图（把它从 `http` 升成 `https`）。

### 7. 字段改名要走迁移，等号右边不许静默丢掉

`event_movie` 那一张表要被改三处：加一列 `source`、把 `studio` 改名 `creator`、
把已经存在的行回填成 `来源 = JavDB`（0068 之前影片只有 JavDB 这一个来路）。

`schema._MIGRATIONS` 至此只有「加一列」一种步子，改名与回填都表达不了。所以把步子扩成
三种：**加列**、**改名**（旧列在新列不在时才改）、**一条显式语句**（回填）。三种都必须是幂等的 ——
新建的库里 `event_movie` 一开出来就是最新结构，那两步走过去必须什么都不做。

**回填不许省**：不填的话，「库里已有的影片」与「这次要导入的同一部」在判重时对不上，
用户会得到两份一样的影片。

## 后果

- **新模块**：`core/events/sources.py`（编号识别、打开网址、按来源分派）、
  `core/events/transport.py`（共用的 HTTP）、`core/events/youtube.py`、`core/events/bilibili.py`。
- **`jav.py` 收窄**：`JavHttp` 与解压挪进 `transport.py`；`JavMovie` 换成共用的 `MovieInfo`。
- **`event_movie` 多一列、改一列名**，`SCHEMA_VERSION` 2 → 3。迁移那三条都是幂等的。
- **`MovieImportPlan.movies` 里放的是 `MovieInfo`**，判重键从「番号」变成「（来源, 编号）」。
- **界面文案**：对话框、横幅、⌥ 点开失败那几句里的「番号」一律改说「编号 / 链接」。
- **抓取请求数变多**：一次 YouTube 导入最多三个请求（oEmbed + watch 页 + 封面），
  仍然受 1 秒限速约束。
- **`来源` 会出现在「按影片分组」的字段子菜单里**（ADR 0065 自动带上）—— 这是对的，
  「只看 YouTube 的那些」正是分组要答的问题。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 用 yt-dlp 抓（含 B 站） | 一个很重的第三方依赖；新版解 YouTube 的 JS 挑战还要一个 Deno 运行时。官方接口 + 标准库已经够 |
| 用 `bilibili-api-python` | 同上，而且它自带的风控处理（WBI 签名）只有 30 行 md5，用不着为此引一个包 |
| 用 Invidious / Piped / noembed 这类第三方源 | 公共实例长期不稳，noembed 不认 B 站；官方接口本身游客可用，绕开它没有收益 |
| 用 YouTube Data API v3 | 要用户自己去 Google Cloud 申请 key、每天 10000 配额。装上就能用是底线，最多将来做成可选项 |
| 抓 B 站视频页的 HTML | 现在是前端渲染，页面里没有 `__INITIAL_STATE__`（实测）；而 JSON 接口就在旁边 |
| 归一成大写（复用 `normalize_code`） | BV 号大小写敏感，实测会静默指向**另一部**片子 |
| 编号一律存成 av 号 | BV 号是 2020 年后的正式编号，用户粘进来的也都是 BV；av 只是兼容旧输入 |
| 支持 `b23.tv` 短链 | 展开它要联网，而识别是纯函数。给一句「先打开再粘完整地址」比把联网塞进纯函数干净 |
| 时长这一列改成秒 | 那一栏是给人看的，AV 的 170 分钟会变成 10200；而且要把已有数据乘一次 60 |
| 新开一类「视频」事件 | 同一类东西、同一套界面，只会让分组菜单多一层没有区别的选项 |
| 把 YouTube 的时长 / 日期抓取做成必须 | 那是抓 HTML 的一刀，站方改版就断；断了不该让整次导入失败 —— 所以它是「补一刀」 |
