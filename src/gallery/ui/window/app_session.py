"""这一次运行的状态（ADR 0058 阶段 3 下半）。

会话（窗口几何与最近打开过哪些库）、会话文件落在哪、缩略图缓存根在哪。
三样都是**开窗时定一次、之后只读**的，所以它们的主人是这个对象而不是那三个 mixin。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core.library.session import SessionState, load_session

__all__ = ["AppSession"]


class AppSession:
    """**这一次运行**的状态：会话、两个路径（ADR 0058 阶段 3）。

    放在一个对象里是因为它们是一起交进来的 —— ``MainWindow.__init__`` 的那两个
    形参（会话目录、缩略图缓存根）与从会话目录读出来的那份是同一件事。
    """

    #: 会话文件落在哪（``None`` = 用平台默认位置）。**只读**。
    _session_base: Path | None
    #: 系统级缩略图缓存根目录（``None`` = 平台默认位置）。**测试必须注入它**：
    #: 否则会在真实缓存目录里留下几百个没有对应库的摘要目录。只读。
    _cache_base: Path | None
    #: 读出来的那一份会话：窗口几何、最近打开过哪些库。
    _session: SessionState

    def __init__(self, session_base: Path | None = None, cache_base: Path | None = None) -> None:
        self._session_base = session_base
        self._cache_base = cache_base
        self._session = load_session(base=session_base)
