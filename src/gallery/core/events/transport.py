"""影片抓取共用的 HTTP：浏览器 UA、限速、退避重试、gzip（ADR 0068）。

八个来源与反爬相处的方式是同一套：**每个来源自己的 ``base`` / ``Referer`` / cookie / UA**，加上请求
之间留一秒、限速与 5xx 退避重试。各写一份就是七处会各自漂，所以它单独成模块。
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

#: 无论怎么重试都值得再试一次的 HTTP 状态码：限速、被挡、源站自己 5xx。
#: **其余的非 2xx 立刻如实报出去**：401 / 404 再试一百次也是同一个答案。
_RETRY_STATUS: Final = frozenset({403, 429, 500, 502, 503, 504})

_ACCEPT: Final = "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8"


class Transport(Protocol):
    """抓取用的传输。:class:`HttpTransport` 是真实实现，测试传假实现（**不必联网**）。"""

    def get_text(self, url: str, *, referer: str | None = None) -> str: ...

    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes: ...


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

    def _headers(self, referer: str | None) -> dict[str, str]:
        return {
            "User-Agent": self.ua,
            "Accept": _ACCEPT,
            "Accept-Language": self.language,
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer or self.referer or self.base.rstrip("/") + "/",
            **({"Cookie": self.cookie} if self.cookie else {}),
        }

    def _get(self, url: str, referer: str | None) -> tuple[bytes, str]:
        headers = self._headers(referer)
        last: str = ""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last = time.time()
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
        raise MovieImportError(f"连不上源站，稍后再试：{last or '未知原因'}。")

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
            raise MovieImportError("封面下载回来的是一个网页，不是图片。")
        return raw


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
    "MOBILE_UA",
    "UA",
    "HttpTransport",
    "Transport",
]
