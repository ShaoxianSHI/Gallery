"""影片抓取共用的 HTTP：浏览器 UA、限速、退避重试、gzip（ADR 0068）。

各来源与反爬相处的方式是同一套：**每个来源自己的 ``base`` / ``Referer`` / cookie / UA**，加上请求
之间留一秒、限速与 5xx 退避重试。各写一份就会各自漂，所以它单独成模块。
"""

from __future__ import annotations

import gzip
import http.client
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Final, Protocol

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.i18n import t

#: 浏览器 UA。源站都按它放行，空 UA 会被挡。
UA: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: 移动浏览器的 UA。爱奇艺的 ``m.`` 站点按它给服务端渲染的那一版，用桌面 UA 只会拿回一个空壳。
MOBILE_UA: Final = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

#: 安卓的 UA。**抖音的分享页只按它给服务端渲染的那一版**（iPhone 与桌面 UA 都只有空壳）。
ANDROID_UA: Final = (
    "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
)

#: 无论怎么重试都值得再试一次的 HTTP 状态码：限速、被挡、源站自己 5xx。
#: **其余的非 2xx 立刻如实报出去**：401 / 404 再试一百次也是同一个答案。
_RETRY_STATUS: Final = frozenset({403, 429, 500, 502, 503, 504})

_ACCEPT: Final = "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8"


class Transport(Protocol):
    """抓取用的传输。:class:`HttpTransport` 是真实实现，测试传假实现（**不必联网**）。"""

    def get_text(self, url: str, *, referer: str | None = None) -> str: ...

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes: ...

    def get_final_url(self, url: str, *, referer: str | None = None) -> str: ...


@dataclass
class HttpTransport:
    """一个只依赖标准库的 HTTP 客户端。**限速 + 退避重试**，与源站的反爬相处。"""

    #: 这个来源的老家。不发 ``Referer`` 时用它，也用来拼相对地址。
    base: str
    #: 有些源站要求一个 cookie 才放行（JavDB 的年龄确认）。
    cookie: str = ""
    #: 不给 ``Referer`` 时的那个。**B 站要 ``bilibili.com`` 而不是接口那个域名**。
    referer: str = ""
    #: 这一次请求用的 UA。绝大多数源站用桌面 UA 就行；爱奇艺要移动版（见 :data:`MOBILE_UA`）。
    ua: str = UA
    language: str = "zh-CN,zh;q=0.9,en;q=0.8"
    timeout: float = 30.0
    delay: float = 1.0
    retries: int = 3
    _last: float = field(default=0.0, repr=False)
    #: 源站自己种下的 cookie（抖音的 ``ttwid`` 就是它）。**按来源各存一份**，不跨来源。
    _cookies: dict[str, str] = field(default_factory=dict, repr=False)

    def _headers(self, referer: str | None) -> dict[str, str]:
        cookie = "; ".join(part for part in (self.cookie, self._cookie_header()) if part)
        return {
            "User-Agent": self.ua,
            "Accept": _ACCEPT,
            "Accept-Language": self.language,
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer or self.referer or self.base.rstrip("/") + "/",
            **({"Cookie": cookie} if cookie else {}),
        }

    def _cookie_header(self) -> str:
        """把源站种下的那几格拼回请求头。"""
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())

    def _remember_cookies(self, response: object) -> None:
        """记下 ``Set-Cookie``。**不带 cookie 时抖音的分享页只给一个空壳**。"""
        headers = getattr(response, "headers", None)
        get_all = getattr(headers, "get_all", None)
        if get_all is None:
            return
        for raw in get_all("Set-Cookie") or ():
            name, separator, rest = raw.partition("=")
            if separator and name.strip():
                self._cookies[name.strip()] = rest.split(";", 1)[0].strip()

    def _get(self, url: str, referer: str | None) -> tuple[bytes, str]:
        headers = self._headers(referer)
        last: str = ""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last = time.time()
                    self._remember_cookies(response)
                    raw = _decompress(response.read(), response.headers.get("Content-Encoding"))
                    return raw, response.geturl()
            except urllib.error.HTTPError as exc:
                self._last = time.time()
                last = f"HTTP {exc.code}"
                if exc.code not in _RETRY_STATUS:
                    # 401 / 404 再试一百次也是同一个答案。**带上状态码如实报出去** ——
                    # 404 在三个源站上不是一回事，翻译交给各自的来源模块（见 SourcePageError）。
                    raise SourcePageError(exc.code, url) from exc
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                # 重试用完的限速 / 被挡是「连不上」而不是「没有这一页」：用户该做的动作完全不同
                # （等一会儿再试 vs 换一个编号）。
                break
            except urllib.error.URLError as exc:
                self._last = time.time()
                last = f"{exc.reason}"
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 30))
                    continue
            except (OSError, http.client.HTTPException, zlib.error) as exc:
                # 请求发出**之后**断掉的那几种（掐连接、话说一半、正文被截断）都不是 ``URLError``
                # 的子类 —— 网络抖动原先也走界面那句「未预期的问题」，重试一次多半就好了。
                self._last = time.time()
                last = type(exc).__name__
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 30))
                    continue
        raise MovieImportError(
            t(
                "event.transport.unreachable",
                reason=last or t("event.transport.unknown_reason"),
            )
        )

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get_text(self, url: str, *, referer: str | None = None) -> str:
        raw, _ = self._get(url, referer)
        return raw.decode("utf-8", "replace")

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes:
        raw, _ = self._get(url, referer)
        if raw.lstrip()[:1] == b"<":
            raise MovieImportError(t("event.transport.webpage_not_image"))
        return raw

    def get_final_url(self, url: str, *, referer: str | None = None) -> str:
        """跟完跳转之后落在哪一页。**分享短链里的编号只在这里拿得到**（抖音与 TikTok）。"""
        _, final = self._get(url, referer)
        return final


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    kind = (encoding or "").lower()
    if "gzip" in kind:
        return gzip.decompress(raw)
    if "deflate" in kind:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw)
    return raw


__all__ = [
    "ANDROID_UA",
    "MOBILE_UA",
    "UA",
    "HttpTransport",
    "Transport",
]
