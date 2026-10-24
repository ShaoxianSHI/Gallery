# ADR 0083：影片导入接上 Pornhub

- 状态：已接受
- 日期：2026-10-24
- 修改：[0082](0082-more-movie-sources.md) 决策 1 的来源值域（7 → 8）
- 相关 ADR：0063（影片事件与导入）、0072（导入菜单按来源分）、0082（其余四个来源与共用传输）
- 相关代码：`src/gallery/core/events/pornhub.py`、`src/gallery/core/events/sources.py`、
  `src/gallery/core/events/kinds.py`、`src/gallery/ui/dialogs/movie_import.py`、
  `src/gallery/ui/window/event_flow.py`

## 背景

「粘一个编号或链接就变成一格格能点开的照片」这件事，前面几个来源已经跑通（ADR 0063 / 0068 /
0082）。用户希望同一套东西也适用于 Pornhub：粘一条视频页地址，抓回标题、封面、时长与上传者，
之后 ⌥ 点开就能回到那一页。

约束与前几家一样：**只依赖标准库**、**游客态**（不登录、不带账号凭据）、**编号识别是纯函数**、
**只读**（抓元数据与写封面，不下载影片本身）。

## 决策

### 1. 来源值是 `Pornhub`，不动库结构

`来源` 的值域从七个变成八个。站点自己的写法就是 `Pornhub`，与 0068 那条「用网站自己的名字」
一致。`source` 仍然只是一格文字，`MOVIE_SOURCES` 是下拉的值域而不是 CHECK 约束 —— 没有迁移。

菜单那一行按 0072 的规则拼：拉丁字母的名字留一个空格，所以是「导入 Pornhub…」。

### 2. 网址优先，裸编号靠面板；viewkey 归一成小写

认得出这几种地址：

| 写法 | 例子 |
|---|---|
| 视频页 | `view_video.php?viewkey=<id>`（`/gay/` 也认） |
| 嵌入页 | `/embed/<id>`、`/gay/embed/<id>` |
| 作者页里的某一部 | `/pornstar/<名字>/video/<id>` |

域名认 `pornhub.com` / `.net` / `.org`、`premium` 那一类，以及各国子域（`cn.` / `de.` …）。
作者页、合集页、搜索页都不是「某一部」，认不出来。

**裸编号不参与形状推断**：viewkey 是一串小写字母与数字，与别家的编号混在一起分不出来，
所以只有「网址」或「面板选的来源」这两条路（与 0082 的腾讯 / 芒果同一取向）。
用户敲进来的编号先转小写 —— viewkey 本身只有小写，转一次不会指向另一部。

### 3. 播放页的地址是固定的

`https://www.pornhub.com/view_video.php?viewkey=<code>`。与其余来源一样，这里没有可配置的余地
（`config.toml` 的 `movie_site` 只管 JavDB 那一栏）。

### 4. 元数据在一页里，但散在三处

一页就够，字段却分散：`og:` 标签给标题与封面，`flashvars_N` 那段 JSON 另给时长与封面，
标签 / 分类 / 出演者挂在 `data-label` 上，上传者只在那句 `From:` 后面，JSON-LD 的
`VideoObject` 里又有一份时长、上传日期与作者。所以按可靠性逐项取：

| 栏 | 取值顺序 |
|---|---|
| 标题 | `og:title` → `twitter:title` → `<h1 class="title">` |
| 封面 | `flashvars.image_url` → `og:image` → JSON-LD `thumbnailUrl` |
| 时长 | `flashvars.video_duration`（秒）→ `og:duration`（秒）→ JSON-LD `duration`（ISO 8601） |
| 发行日期 | `og:video:release_date` → JSON-LD `uploadDate` |
| 上传者 | `From:` 后面那个链接 → `MODEL_PROFILE.username` → JSON-LD `author.name` |
| 标签 | `data-label="tag"` + `data-label="category"` |
| 演员 | `data-label="pornstar"` |

**取不到的留空**（0082 决策 4）：从别处猜一个日期或一个作者，列表会按它排序与分组，错得看不出来。
这几处字段名与取值方式取自 yt-dlp 的公开解析实现 —— 不是自己猜的，但站方改版时仍会断，
所以解析是纯函数，改起来只动一个文件。

### 5. 年龄确认的 cookie 必须带上

源站的游客页要先过年龄确认，不带那几条 cookie 拿回的是确认页（正文里一个影片字段都没有，
症状会被误报成「站方改了页面结构」）。传输里硬编码
`age_verified=1; accessAgeDisclaimerPH=1; accessAgeDisclaimerUK=1; accessPH=1; platform=pc`。
**它不绕过任何账号或付费**：这几条只是游客点过一次「已满 18 岁」之后网站自己会种的那几个值。

### 6. 「视频不在」与「这个地区看不到」各说一句话

页面自己会写出 `removed` / `noVideo` / `userMessageSection` 那几种标记，也有地域限制页。
两者都**不是** HTTP 错误（传输层不会重试），认不出来就会被说成「页面结构变了」。所以单独判：
- 删除 / 私密 / 下架 → 「Pornhub 上没有这个视频，或者它已被删除 / 设为私密」；
- 地域限制 → 「这个视频在当前的网络所在地看不到」；
- 404 → 「Pornhub 上没有这个视频」。

## 后果

- **新模块** `core/events/pornhub.py`（识别、解析、抓取都在里面）。
- **`sources.py`**：`_URL_PARSERS` / `_TEXT_PARSERS` 各多一行，`movie_url` 与 `_FETCHERS` /
  `_default_transports` 各多一支。
- **`kinds.py`**：`SOURCE_PORNHUB` 进 `MOVIE_SOURCES`，`来源` 的下拉变成八项（事件编辑器与
  「按影片分组」的字段子菜单自动带上，ADR 0065）。
- **界面**：导入菜单多一项，对话框多一份文案。
- **抓取**：仍是「每部一个请求」，与其他来源共用一秒限速。封面的后缀仍按字节认（0082 决策 6）。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 顺带下载影片文件 | 「只增不改」管的是用户照片；抓影片是另一件事，存储、格式与授权都不在这一次的范围里。这一家只做元数据与打开网页 |
| 用 `ph` 前缀自动认出裸编号 | 面板本来就按来源分开（0072），而形状规则每多一条就多一次「拿着编号去错的站」。0082 已经把这个取向定成「歧义靠面板消」 |
| 用 yt-dlp 或别的第三方库 | 只依赖标准库是这个项目的前提；元数据这一页用标准库就够 |
| 把分类与标签分成两栏 | 影片事件只有一个「标签」栏；分类与标签对用户是同一件事，拼在一起按页面顺序去重 |
| 认作者页 / 合集页为「某一部」 | 那些页面上没有唯一的 viewkey，打开哪一部全凭猜 |
