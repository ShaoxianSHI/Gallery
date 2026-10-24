# ADR 0085：影片导入接上抖音与 TikTok

- 状态：已接受
- 日期：2026-10-24
- 修改：[0082](0082-more-movie-sources.md) 决策 1 的来源值域（8 → 10）与决策 3 的识别顺序
  （多一档「短链先认 token，抓取时再展开」）
- 相关 ADR：0063（影片事件与导入）、0072（导入菜单按来源分）、0068（共用传输）、
  0082（其余来源与「歧义靠面板消」）、0083（Pornhub）
- 相关代码：`src/gallery/core/events/{douyin,tiktok}.py`、`src/gallery/core/events/sources.py`、
  `src/gallery/core/events/transport.py`、`src/gallery/core/events/kinds.py`、
  `src/gallery/ui/dialogs/{movie_import,event_edit}.py`、`src/gallery/ui/window/event_flow.py`

## 背景

「粘一个编号或链接就变成一格格能点开的照片」已经覆盖了八个来源。用户希望短视频也走同一套：
抖音与 TikTok 的分享链接粘进来，抓回标题、作者、封面与时长，之后 ⌥ 点开回到那一页。

约束与前几家一样：**只依赖标准库**、**游客态**（不登录、不带账号凭据）、**编号识别是纯函数**、
**只读**（抓元数据与写封面，不下载影片本身）。这一轮多一条实际限制：分享链接里往往没有编号。

## 决策

### 1. 来源值是 `抖音` 与 `TikTok`，不动库结构

`来源` 的值域从八个变成十个。抖音是中文站、TikTok 用站方自己的写法，与 0068 那条
「用网站自己的名字」一致。`source` 仍然只是一格文字，没有迁移。

菜单按 0072 的规则拼：中文名不加空格（「导入抖音…」），拉丁字母留一个（「导入 TikTok…」）。
**两条各占一行**，都排在 YouTube 后面；面板上那一排按钮的顺序是
爱奇艺 / 腾讯视频 / 优酷 / 芒果TV、Bilibili / YouTube / **抖音 / TikTok**、JavDB / Pornhub。

### 2. 短链在识别时只认 token，展开放在抓取那一步

分享链接有两种：

| 写法 | 有没有编号 |
|---|---|
| `www.douyin.com/video/<id>`、`…?modal_id=<id>`、`iesdouyin.com/share/video/<id>` | 有 |
| `v.douyin.com/<token>`、`vm.tiktok.com/<token>`、`vt.tiktok.com/<token>`、`tiktok.com/t/<token>` | 没有 |
| `www.tiktok.com/@user/video/<id>`、`/embed/v2/<id>`、`/player/v1/<id>` | 有 |

**识别仍然不联网**（0068 决策 3）：带编号的地址直接取出来，短链只把 token 当作编号交出去。
真正换编号发生在 `fetch` 里 —— 抖音跟一次跳转（`Transport.get_final_url`），TikTok 问一次
oEmbed。于是 `parse_ref` 的返回类型不变，而判重用的是展开之后的真实编号（`MovieInfo.code`）。

抖音的短链 token **不含连字符**：番号（`IPX-660`）因此不会被误当成短链去展开。
TikTok 的 token 允许 `_` / `-`，所以裸编号只走「面板选了来源」那一条，不参与形状推断。

### 3. 抖音：分享页要「先拿 cookie，再要一次」

`iteminfo` 接口（`iesdouyin.com/web/api/v2/aweme/iteminfo/`）一次给出标题、作者、封面、
发布时间与时长，字段都在 `item_list[0]`。它现在常常返回 200 + 空正文（站方收紧），所以
真正的路是分享页：`iesdouyin.com/share/video/<id>/` 里那段 `_ROUTER_DATA` 带着同一份
`item_list`。接口失败只记一条日志，两条都空才报错。

分享页有两个前提，缺一条都只拿回一个空壳：

1. **安卓 UA**（桌面与 iPhone UA 都只有空壳）；
2. **先拿到 `ttwid` cookie**。第一次请求的正文是空壳，但响应头会种下 `ttwid`；带上它再要
   一次，才是服务端渲染的那一版。

所以 `HttpTransport` 多了一个**按来源各存一份的 cookie 罐**（源站自己种的 cookie 会跟到下一次
请求），分享页那条路最多问三次 —— 拿到 cookie 之后偶尔仍会给一次空壳。

- `video.duration` 是**毫秒**，先换成秒再交给 `minutes_of_seconds`；
- `create_time` 是秒级时间戳，按北京时间取日期（抖音上显示的就是那一天）；
- 话题取 `text_extra[].hashtag_name`，去重保序；
- 封面按 `origin_cover` → `cover` → `dynamic_cover` 取第一个，并升成 `https`。

### 4. TikTok：oEmbed 一次给全，网页补时长与日期

`www.tiktok.com/oembed` 免 key、免配额，给出标题、作者、封面与 `embed_product_id` ——
**短链也靠它换编号**，离线没有任何别的办法。封面是带时效签名的 CDN 地址，所以导入时当场下载，
不留到以后（这也与「封面写成一个新文件」同一时点）。

时长、发布日期与话题去读网页里那段 `__UNIVERSAL_DATA_FOR_REHYDRATION__`
（`webapp.video-detail.itemInfo.itemStruct`）。这一刀失败只记日志，oEmbed 已经拿到的标题、
作者与封面照旧可用（与 YouTube 的第二步同一取向）。

**网页那一刀还要补标题与封面**：实测 oEmbed 有时只回一个 `embed_product_id`（裸编号与短链
都见过），标题、作者与封面只在 `itemStruct` 里。所以网页这一份是「缺什么补什么」，不是只补
时长与日期。

**打开影片时用 `https://www.tiktok.com/@i/video/<id>`**：`@i` 是 TikTok 的占位 handle，
会跳到作者真正的那一页。于是「编号」这一栏仍是一串纯数字，不必把作者名也存进去。

### 5. 面板按钮改成 4 / 4 / 2 三行

十个来源放不下一排（`MAX_CHOICES_PER_ROW` 是 5，五个评分正好一行）。原先的算法把十项平均成
5 / 5，与「来源每行四个」的视觉习惯不一致。所以换行之后按 `WRAPPED_CHOICES_PER_ROW = 4` 铺：
十个来源是 4 / 4 / 2，前八项仍与旧版一致。**评分仍是五个一行**（不超过 5 项时不换行）。

## 后果

- **新模块**两个：`core/events/douyin.py`、`core/events/tiktok.py`（识别、解析、抓取都在里面）。
- **`sources.py`**：`_URL_PARSERS` / `_TEXT_PARSERS` 各多两行，`movie_url` 与 `_FETCHERS` /
  `_default_transports` 各多两支。
- **`transport.py`**：协议与实现各多一个 `get_final_url`（跟完跳转落在哪一页）；传输多一个
  **按来源各存一份的 cookie 罐**（抖音的 `ttwid` 靠它），以及安卓 UA 常量 `ANDROID_UA`。
- **`kinds.py`**：两个来源进 `MOVIE_SOURCES`，来源的下拉变成十项；编号那一栏的提示也带上它们。
- **界面**：导入菜单多两项，对话框多两份文案；来源按钮改成 4 / 4 / 2。
- **抓取请求数**：抖音每部两到四次（接口 + 分享页那两三次），TikTok 每部两次（oEmbed + 网页），
  短链再多试一到两个短链地址；仍然受一秒限速约束。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 识别时联网展开短链 | 识别是纯函数（0068 决策 3）：`parse_ref` 在面板与判重两处都被调用，联网会把「认一行字」变成一次网络请求 |
| 把短链整个存进「编号」 | 之后 ⌥ 点开只能打开一个短链，判重也会把同一个视频的两种写法当成两部；展开之后的 aweme_id / 视频 id 才是稳定的键 |
| TikTok 的编号存成「作者/视频 id」 | 打开时能少一次跳转，但用户看到的「编号」变成一段网址碎片；`@i` 占位 handle 已经能跳过去 |
| 抖音只读分享页、不碰接口 | 分享页是服务端渲染的 HTML，改版时比接口更脆；两条路各留一份，任一条活着就能导入 |
| 用 yt-dlp 或别的第三方库 | 只依赖标准库是这个项目的前提；这两个来源用标准库就够 |
| 把十个来源平铺成 5 / 5 两行 | 第二行第三、四位就不是抖音 / TikTok 了，而用户是按「第二排三四位」找它们的 |
