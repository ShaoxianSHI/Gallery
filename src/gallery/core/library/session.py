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
from gallery.core.metadata.facets import FACET_CAPTURED, group_facet, sort_facet

#: 最多记住几个最近打开的库。
MAX_RECENT_ROOTS: Final = 8

#: 排序字段的**界面默认值**：用户从没选过排序字段时，按拍摄时间排。
#:
#: 照片是按时间发生的事，不是按文件名发生的事 —— 打开一个库想看的就是「什么时候拍的」。
DEFAULT_SORT_FACET: Final = FACET_CAPTURED

#: 会话里表示「明确不按任何字段排」（相册在前、同层按名称）的哨兵。
#:
#: 之所以要一个哨兵：``sort_facet`` 为 ``None`` 已经占了「没选过，用界面默认值」这个
#: 意思，而界面默认值现在是拍摄时间。用户的显式选择必须能表达出来，否则每次重开软件
#: 都会被默认值拽回去 —— 他选的就白选了。
SORT_FACET_NONE: Final = "none"

#: 会话文件格式版本。将来结构变化时用于迁移或丢弃。
SESSION_VERSION: Final = 1

#: 浏览方式之一：按文件夹逐层进入，子文件夹先以相册形式呈现。
BROWSE_MODE_ALBUMS: Final = "albums"

#: 浏览方式之一：把整个库（含所有子文件夹）的照片平铺在一起。
BROWSE_MODE_FLAT: Final = "flat"

#: 允许的浏览方式。
BROWSE_MODES: Final = (BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT)


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
    #: 上次用的浏览方式；``None`` 表示用界面默认值。
    browse_mode: str | None = None
    #: 大图右侧的元数据面板是否展开；``None`` 表示用界面默认值（不展开）。
    metadata_visible: bool | None = None
    #: 上次用的分组字段（见 ``metadata.facets``）；``None`` 表示不分组。
    group_facet: str | None = None
    #: 上次用的排序字段；``None`` 表示**没选过**（由 :meth:`resolved_sort_facet` 交回
    #: :data:`DEFAULT_SORT_FACET`），:data:`SORT_FACET_NONE` 表示明确不按任何字段排。
    sort_facet: str | None = None
    #: 排序是否逆序；``None`` 表示用界面默认值（正序）。
    sort_descending: bool | None = None

    def with_recent_root(self, root: Path) -> SessionState:
        """把 ``root`` 提到最前面，去重并截断到上限。"""
        root = Path(root)
        others = tuple(item for item in self.recent_roots if item != root)
        return replace(self, recent_roots=(root, *others)[:MAX_RECENT_ROOTS])

    def with_window(self, window: WindowGeometry) -> SessionState:
        return replace(self, window=window)

    def with_thumbnail_edge(self, edge: int | None) -> SessionState:
        return replace(self, thumbnail_edge=edge)

    def with_browse_mode(self, mode: str | None) -> SessionState:
        return replace(self, browse_mode=mode)

    def with_metadata_visible(self, visible: bool) -> SessionState:
        return replace(self, metadata_visible=visible)

    def with_ordering(
        self, group_facet: str | None, sort_facet: str | None, descending: bool
    ) -> SessionState:
        """记下分组 / 排序选择（见 ADR 0009）。"""
        return replace(
            self,
            group_facet=group_facet,
            # 界面上「不排序」传的就是 ``None``，但会话里必须与「没选过」分开记，
            # 否则这次选择会被下一次启动的默认值覆盖掉。
            sort_facet=sort_facet if sort_facet is not None else SORT_FACET_NONE,
            sort_descending=descending,
        )

    def resolved_sort_facet(self) -> str | None:
        """这一档真正该用的排序字段 id；``None`` 表示不按任何字段排（相册在前、按名称）。

        没选过（:attr:`sort_facet` 为 ``None``）就交回 :data:`DEFAULT_SORT_FACET`。
        """
        if self.sort_facet == SORT_FACET_NONE:
            return None
        return self.sort_facet or DEFAULT_SORT_FACET


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
        browse_mode=_parse_browse_mode(data.get("browse_mode")),
        metadata_visible=_parse_bool(data.get("metadata_visible")),
        # 字段 id 要对着真实字段表校验：会话文件是旧版本留下的、或者被人手改过时，
        # 一个来路不明的 id 会让界面在别处炸出 KeyError。
        group_facet=_parse_group_facet(data.get("group_facet")),
        sort_facet=_parse_sort_facet(data.get("sort_facet")),
        sort_descending=_parse_bool(data.get("sort_descending")),
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
        "browse_mode": state.browse_mode,
        "metadata_visible": state.metadata_visible,
        "group_facet": state.group_facet,
        "sort_facet": state.sort_facet,
        "sort_descending": state.sort_descending,
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


def _parse_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_group_facet(value: object) -> str | None:
    """合法的分组字段 id，否则 ``None``（不分组）。"""
    if not isinstance(value, str):
        return None
    return value if group_facet(value) is not None else None


def _parse_sort_facet(value: object) -> str | None:
    """排序字段 id；没存过或来路不明都当「没选过」（交回界面默认值）。

    老的会话文件里这个键一律是 ``null`` —— 那时它表示「不排序」，如今它表示
    「没选过」。分不清这两者的场合一律按「没选过」处理：新默认值对所有人都生效，
    真想不排序的用户重新点一次即可（那次会记成 :data:`SORT_FACET_NONE`）。
    """
    if value == SORT_FACET_NONE:
        return SORT_FACET_NONE
    if isinstance(value, str) and sort_facet(value) is not None:
        return value
    return None


def _parse_browse_mode(value: object) -> str | None:
    if isinstance(value, str) and value in BROWSE_MODES:
        return value
    return None


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
    "BROWSE_MODES",
    "BROWSE_MODE_ALBUMS",
    "BROWSE_MODE_FLAT",
    "DEFAULT_SORT_FACET",
    "MAX_RECENT_ROOTS",
    "SESSION_VERSION",
    "SORT_FACET_NONE",
    "SessionState",
    "WindowGeometry",
    "load_session",
    "save_session",
]
