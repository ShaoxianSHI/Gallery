"""应用级会话状态：最近打开的库、窗口几何。

它**不是数据**。会话文件损坏、字段缺失、路径已不存在，都静默回退到默认值 ——
绝不能让「记不住上次开了哪个文件夹」变成打不开软件。

真正的用户数据只有两样：照片本身，和库里的 ``config.toml``。
所以这里也不用 ``QSettings``（plist 不透明、没法在测试里构造），而是一个显式 JSON 文件，
路径可以注入，测试直接传 ``tmp_path``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_text_atomic

#: 最多记住几个最近打开的库。
MAX_RECENT_ROOTS: Final = 8

#: 会话文件格式版本。将来结构变化时用于迁移或丢弃。
SESSION_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    """窗口位置与大小。刻意用普通整数表达，避免 core 依赖 Qt。"""

    x: int
    y: int
    width: int
    height: int
    maximized: bool = False


@dataclass(frozen=True, slots=True)
class SessionState:
    """跨启动保留的界面状态。"""

    recent_roots: tuple[Path, ...] = ()
    window: WindowGeometry | None = None
    #: 缩略图长边（像素）。界面把它归到最近的档位，非法值一律回退到默认。
    thumbnail_edge: int | None = None

    def with_recent_root(self, root: Path) -> SessionState:
        """把 ``root`` 提到最前面，去重并截断到上限。"""
        root = Path(root)
        others = tuple(item for item in self.recent_roots if item != root)
        return replace(self, recent_roots=(root, *others)[:MAX_RECENT_ROOTS])

    def with_window(self, window: WindowGeometry) -> SessionState:
        return replace(self, window=window)

    def with_thumbnail_edge(self, edge: int | None) -> SessionState:
        return replace(self, thumbnail_edge=edge)


def load_session(*, base: Path | None = None) -> SessionState:
    """读取会话状态；任何异常都返回默认值。"""
    raw = read_text_or_none(paths.app_state_file(base=base))
    if raw is None:
        return SessionState()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return SessionState()
    if not isinstance(data, dict):
        return SessionState()
    return SessionState(
        recent_roots=_parse_roots(data.get("recent_roots")),
        window=_parse_window(data.get("window")),
        thumbnail_edge=_parse_positive_int(data.get("thumbnail_edge")),
    )


def save_session(state: SessionState, *, base: Path | None = None) -> None:
    """原子地写出会话状态。

    :raises OSError: 写不进去时抛出，由调用方决定是提示还是忽略
        （窗口关闭阶段通常选择忽略：记不住窗口位置不值得打断用户）。
    """
    payload = {
        "version": SESSION_VERSION,
        "recent_roots": [str(path) for path in state.recent_roots],
        "window": _window_to_json(state.window),
        "thumbnail_edge": state.thumbnail_edge,
    }
    write_text_atomic(
        paths.app_state_file(base=base),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _window_to_json(window: WindowGeometry | None) -> dict[str, object] | None:
    if window is None:
        return None
    return {
        "x": window.x,
        "y": window.y,
        "width": window.width,
        "height": window.height,
        "maximized": window.maximized,
    }


def _parse_roots(value: object) -> tuple[Path, ...]:
    if not isinstance(value, list):
        return ()
    roots: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item:
            continue
        path = Path(item)
        if path in roots:
            continue
        roots.append(path)
        if len(roots) >= MAX_RECENT_ROOTS:
            break
    return tuple(roots)


def _parse_positive_int(value: object) -> int | None:
    # bool 是 int 的子类，必须显式排除。
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_window(value: object) -> WindowGeometry | None:
    if not isinstance(value, dict):
        return None
    numbers: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        item = value.get(key)
        # bool 是 int 的子类，显式排除，避免 true 被当成 1。
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        numbers[key] = item
    if numbers["width"] <= 0 or numbers["height"] <= 0:
        return None
    maximized = value.get("maximized", False)
    return WindowGeometry(
        x=numbers["x"],
        y=numbers["y"],
        width=numbers["width"],
        height=numbers["height"],
        maximized=maximized if isinstance(maximized, bool) else False,
    )


__all__ = [
    "MAX_RECENT_ROOTS",
    "SESSION_VERSION",
    "SessionState",
    "WindowGeometry",
    "load_session",
    "save_session",
]
