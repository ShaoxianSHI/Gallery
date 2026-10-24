"""``gallery.core.events.transport``：三个来源共用的那一份 HTTP（ADR 0068）。

这几条盯的是**与反爬相处**的那半边：断在半路的要重试、非 2xx 要如实带上状态码。

**不联网**：``urlopen`` 换成假实现。
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request

import pytest

from gallery.core.errors import MovieImportError, SourcePageError
from gallery.core.events.transport import HttpTransport

BASE = "https://javdb.com"


class _FakeResponse:
    """一个够用的响应对象：``urlopen`` 的上下文管理器协议 + ``read`` / ``geturl``。"""

    headers = {"Content-Encoding": None}

    def __init__(self, body: bytes = b"<html>ok</html>") -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return f"{BASE}/x"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """退避等待在测试里没有意义（真等就是几秒），而重试次数与判定才是要验的。"""
    monkeypatch.setattr("gallery.core.events.transport.time.sleep", lambda _seconds: None)


def test_a_connection_dropped_mid_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """限速与反爬常常直接掐连接（``RemoteDisconnected`` / ``IncompleteRead``）。

    它们**不是** ``URLError`` 的子类，原先会一路传递到界面那句「出现了一个未预期的问题」——
    而重试一次多半就好了。
    """
    _no_sleep(monkeypatch)
    calls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    raw, _ = HttpTransport(base=BASE, delay=0, retries=3)._get(f"{BASE}/x", None)

    assert raw == b"<html>ok</html>"
    assert len(calls) == 2


def test_a_half_read_body_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """正文说到一半就没了（``IncompleteRead``，连 ``OSError`` 都不是）也算网络抖动。"""
    _no_sleep(monkeypatch)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise http.client.IncompleteRead(b"half", 100)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(MovieImportError) as excinfo:
        HttpTransport(base=BASE, delay=0, retries=2)._get(f"{BASE}/x", None)

    assert "稍后再试" in excinfo.value.message
    assert "IncompleteRead" in excinfo.value.message


def test_a_truncated_body_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Content-Encoding: gzip`` 而正文被截断：解压炸掉，也是等一会儿再试的那一种。"""
    _no_sleep(monkeypatch)
    calls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        calls.append(request.full_url)
        if len(calls) == 1:
            response = _FakeResponse(b"not gzip at all")
            response.headers = {"Content-Encoding": "gzip"}
            return response
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    raw, _ = HttpTransport(base=BASE, delay=0, retries=3)._get(f"{BASE}/x", None)

    assert raw == b"<html>ok</html>"
    assert len(calls) == 2


def test_a_non_2xx_carries_its_status_to_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """**状态码要如实带上**：404 在三个源站上不是一回事，翻译交给各自的来源模块（ADR 0068）。"""
    _no_sleep(monkeypatch)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SourcePageError) as excinfo:
        HttpTransport(base=BASE, delay=0, retries=3)._get(f"{BASE}/gone", None)

    assert excinfo.value.status == 404
    assert excinfo.value.url == f"{BASE}/gone"


def test_a_retryable_status_gives_up_as_a_connection_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试用完的 5xx 是「连不上」而不是「没有这一页」—— 用户该做的动作完全不同。"""
    _no_sleep(monkeypatch)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 503, "Slow Down", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(MovieImportError) as excinfo:
        HttpTransport(base=BASE, delay=0, retries=2)._get(f"{BASE}/x", None)

    assert not isinstance(excinfo.value, SourcePageError)
    assert "稍后再试" in excinfo.value.message


class _CookieHeaders(dict[str, object]):
    """``http.client.HTTPMessage`` 那一半的接口：``get`` 取一个，``get_all`` 取一串。"""

    def get_all(self, name: str) -> list[str]:
        value = self.get(name)
        if value is None:
            return []
        return value if isinstance(value, list) else [str(value)]


def test_a_cookie_the_site_sets_comes_back_on_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抖音的分享页要先拿到 ``ttwid`` 才会给服务端渲染的那一版（ADR 0085）。"""
    seen: list[str | None] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen.append(request.get_header("Cookie"))
        response = _FakeResponse()
        response.headers = _CookieHeaders(
            {"Content-Encoding": None, "Set-Cookie": ["ttwid=abc123; Path=/; HttpOnly"]}
        )
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = HttpTransport(base=BASE, delay=0)

    transport.get_text(f"{BASE}/first")
    transport.get_text(f"{BASE}/second")

    assert seen[0] is None
    assert seen[1] == "ttwid=abc123"


def test_the_final_url_is_the_one_after_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """分享短链的编号只在跳转后的地址里（ADR 0085），所以这一条要单独给出来。"""
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: _FakeResponse())

    assert HttpTransport(base=BASE, delay=0).get_final_url(f"{BASE}/short") == f"{BASE}/x"


def test_a_page_instead_of_an_image_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """封面下回来一个网页（多半是被挡住了给的拦截页）：说清是图片这一步出的问题。"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: _FakeResponse(b"<html>nope</html>")
    )

    with pytest.raises(MovieImportError) as excinfo:
        HttpTransport(base=BASE, delay=0).get_bytes(f"{BASE}/cover.jpg")

    assert "不是图片" in excinfo.value.message
