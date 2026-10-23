# ADR 0076：Google 地图那一项也走 Chrome PWA——装在通用域名上，认窗口要连路径一起认

- 状态：已接受
- 日期：2026-10-24
- 相关 ADR：0075（大图页把拍摄位置开到地图里）、0034（用外部应用打开：PWA 那条路）、
  0067（大图页那一列图片应用）
- 相关代码：`src/gallery/core/external/launch.py`、`src/gallery/core/external/pwa.py`、
  `src/gallery/core/external/applescript.py`、`src/gallery/core/external/maps.py`、
  `src/gallery/ui/window/widgets/external_button.py`

## 背景

0075 把 Google 地图那一项定成「永远开在浏览器」，当时的理由是 Mac 上没有它的原生应用。
后来本机装了 Chrome 的 Google 地图 PWA，实测这条路走得通：

- PWA 的 `CrAppModeShortcutURL` 是 `https://www.google.com/maps?force=tt&source=ttpwa`；
  它的应用窗口**名称为空、只有一个标签页**，地址在 `www.google.com/maps/@…`。
- `open -a "<那个应用包>"` 之后把那个窗口的地址改成
  `https://www.google.com/maps/search/?api=1&query=48.8584,2.2945`，页面落到了
  `…/maps/place/48°51'30.2"N 2°17'40.2"E/…`，标题就是这组坐标。

于是可以复用日历那条「启动 → 等浏览器 → 认出窗口 → 改写地址」的路，不需要第二套机制。

## 决策

1. **复用 `open_in_app`，失败一律退回浏览器并说明原因**（与 0034 / 0075 同一条降级规则）。
2. **认窗口要连路径一起认。** Google 地图装在 `www.google.com` 上，而那是共用域名：
   「窗口名为空 + 只有一页」不足以把它与同一域名下别的应用窗口分开。因此
   `find_chrome_pwa` 与 `is_pwa_window` 都加一个可选的 `path_prefix`，地图传 `/maps`；
   日历不传，行为不变。
3. **`open_in_app` 认窗口的域名从目标 URL 自己取**，不再硬编码日历那个 `HOST` ——
   它本来就是「把哪个地址交给哪个窗口」，域名属于参数，不属于常量。
4. **图标：装了 PWA 就用它包里的 `app.icns`**，没装才画中性折页（ADR 0034 决策 6：
   别人的图标不随仓库分发，但系统里已有的那一份可以用）。
5. **横幅按实际开在哪儿说**：`where == app` 时说「已在「Google 地图」中打开…」，
   只有真的降级到浏览器才说「已在浏览器中打开」。名字两边加「」，中英文都不挤。
6. **整条链路进后台线程**（与日历同一条）：等浏览器起来 + 认窗口最坏二十秒，
   不许占住主线程。系统「地图」那一项仍同步 —— 它只起一个 `open` 进程。

## 后果

- 装了 PWA：点「Google 地图」开在独立窗口里，并把地址改到这张照片的坐标。
- 没装 PWA、非 macOS、或者认不出窗口：退回默认浏览器，横幅照实说降级原因。
- 首次点击会弹「Gallery 想控制 Google Chrome」—— macOS 的自动化授权，属于用户可修复。
- 这条依赖 Chrome 未对外承诺的内部行为，与日历那条是同一个风险；改动 Chrome 之后
  两条会一起坏，而两条的失败路径都只是「改用浏览器」。

## 被否决的方案

- **只按 `www.google.com` 认窗口**：同一域名下还开着别的东西，而「名字为空 + 单页」这两个
  条件分不开它们 —— 认错就会改掉用户正在看的那一页。
- **每次点击都新开一个窗口**：PWA 窗口本来就在 Chrome 里，改写地址当场生效，再开一个只会
  越积越多。
- **用 `maps.google.com` 的地址**：PWA 的窗口留在 `www.google.com`，多一跳重定向，认窗口
  还得再放宽一次。
