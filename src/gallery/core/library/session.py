"""应用级会话状态：最近打开的库、窗口几何。

它**不是数据**：会话文件损坏、字段缺失、路径已不存在，都静默回退到默认值 —— 绝不能让
「记不住上次开了哪个文件夹」变成打不开软件。这里只剩应用级的那两样（ADR 0038 决策 4），
判据是「打开软件时还不知道要开哪个库」；库级偏好在库的 ``config.toml`` 的 ``[ui]`` 表里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_text_atomic
from gallery.core.library.config import LibraryUIState

#: 最多记住几个最近打开的库。
MAX_RECENT_ROOTS: Final = 8

#: 会话文件格式版本。**读的时候要认它**，不然将来结构一变，一份 v1 的老文件会被
#: 当成 v2 解析出一个半懂不懂的状态。
SESSION_VERSION: Final = 1

#: 旧版会话文件里那几个应用级 UI 键，ADR 0038 把它们搬进了库的 ``config.toml``。
#: **只给一次性迁移读**（:func:`legacy_ui_state`），不再写出也不再读成状态。
LEGACY_UI_KEYS: Final = (
    "browse_mode",
    "metadata_visible",
    "group_facet",
    "calendar_level",
    "sort_facet",
    "sort_descending",
    "thumbnail_edge",
)


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
    """跨启动保留的**应用级**界面状态：最近打开过哪些库、窗口在哪儿。

    两者问的都是「这个应用」而不是「这个库」，所以进不了任何一份 ``config.toml``；
    库级偏好见 :class:`~gallery.core.library.config.LibraryUIState`。
    """

    recent_roots: tuple[Path, ...] = ()
    window: WindowGeometry | None = None

    def with_recent_root(self, root: Path) -> SessionState:
        """把 ``root`` 提到最前面，去重并截断到上限。"""
        root = Path(root)
        others = tuple(item for item in self.recent_roots if item != root)
        return replace(self, recent_roots=(root, *others)[:MAX_RECENT_ROOTS])

    def without_recent_root(self, root: Path) -> SessionState:
        """把 ``root`` 从名单里拿掉；它本来就不在里面时原样返回。"""
        return replace(self, recent_roots=tuple(item for item in self.recent_roots if item != root))

    def without_recent_roots(self) -> SessionState:
        """把整份名单清掉。"""
        return replace(self, recent_roots=())

    def with_window(self, window: WindowGeometry) -> SessionState:
        return replace(self, window=window)


def load_session(*, base: Path | None = None) -> SessionState:
    """读取会话状态；读不出来、结构不对或是别的版本，都返回默认值。"""
    data = _read_state(base=base)
    if data is None:
        return SessionState()
    return SessionState(
        recent_roots=_parse_roots(data.get("recent_roots")),
        window=_parse_window(data.get("window")),
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
    }
    write_text_atomic(
        paths.app_state_file(base=base),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def legacy_ui_state(*, base: Path | None = None) -> LibraryUIState | None:
    """旧版会话文件里的**库级**界面偏好，供一次性迁移用（ADR 0038 决策 4）。

    读它是为了让「从老版本升级上来的用户，排序偏好不会被静默重置」，代价只是多写一次
    ``config.toml``。读不出文件 / 结构不对 / 版本不认 / 没有这几个键 / 一个都认不得，
    一律返回 ``None``（与 :func:`load_session` 同一套判据）。
    """
    data = _read_state(base=base)
    if data is None:
        return None
    state = LibraryUIState.from_mapping({key: data[key] for key in LEGACY_UI_KEYS if key in data})
    return None if state.is_empty else state


def _read_state(*, base: Path | None) -> dict[str, object] | None:
    """把会话文件读成一张映射；读不出来或结构不对时是 ``None``。

    版本号**必须认**（:data:`SESSION_VERSION`）：那个键过去只写不读，于是一份结构不同的
    老文件会被照新结构解析出一个半懂不懂的状态。不认识的一律当作「读不出来」。
    """
    raw = read_text_or_none(paths.app_state_file(base=base))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version", SESSION_VERSION) != SESSION_VERSION:
        return None
    return data


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
    "LEGACY_UI_KEYS",
    "MAX_RECENT_ROOTS",
    "SESSION_VERSION",
    "SessionState",
    "WindowGeometry",
    "legacy_ui_state",
    "load_session",
    "save_session",
]
