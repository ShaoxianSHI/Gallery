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
from gallery.core.library.calendar import LEVELS as CALENDAR_LEVELS
from gallery.core.metadata.facets import FACET_MODIFIED, group_facet, sort_facet

#: 最多记住几个最近打开的库。
MAX_RECENT_ROOTS: Final = 8

#: 排序字段的**界面默认值**：用户从没选过排序字段时，按修改时间排。
#:
#: 时间才是照片之间最实在的那条线索，而**文件自己就带着它** —— 不必等 exiftool，
#: 打开一个库就能立刻排好顺序（``FILE_FACET_IDS`` 里的字段才做得到这一点）。
#: 想按拍摄时间排的，「排序」菜单里有那一项，读一次元数据即可切过去。
DEFAULT_SORT_FACET: Final = FACET_MODIFIED

#: 会话文件格式版本。**读的时候要认它**，不然将来结构一变，一份 v1 的老文件会被
#: 当成 v2 解析出一个半懂不懂的状态 —— 那正是这个字段当初写下来要防的事。
SESSION_VERSION: Final = 1

#: 浏览方式之一：按文件夹逐层进入，子文件夹先以相册形式呈现。
BROWSE_MODE_ALBUMS: Final = "albums"

#: 浏览方式之一：把整个库（含所有子文件夹）的照片平铺在一起。
BROWSE_MODE_FLAT: Final = "flat"

#: 浏览方式之一：按时间浏览（年 / 月 / 周 / 日四层，ADR 0033）。
#:
#: 它**横跨整个库**，与前两种的一个根本差别是：那两个的「一层」是一批照片，
#: 它的一层是**一段日子**，而那一段里的照片要等元数据读完才摆得出来。
BROWSE_MODE_CALENDAR: Final = "calendar"

#: 允许的浏览方式。
BROWSE_MODES: Final = (BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT, BROWSE_MODE_CALENDAR)

#: 日历视图的四层（见 ``core.library.calendar.LEVELS``）。会话里只记**当前是哪一层**，
#: 停在哪天不记 —— 每次启动都从今天开始，跟「回到上次打开的库」是同一个脾气。
DEFAULT_CALENDAR_LEVEL: Final = "month"


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
    #: 日历视图上次停在哪一层；``None`` 表示用 :data:`DEFAULT_CALENDAR_LEVEL`。
    calendar_level: str | None = None
    #: 上次用的排序字段；``None`` 表示**没选过**（由 :meth:`resolved_sort_facet` 交回
    #: :data:`DEFAULT_SORT_FACET`）。界面上没有「不排序」这个选择，排序字段总是有一个。
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

    def with_calendar_level(self, level: str) -> SessionState:
        """记下日历停在哪一层。**只记合法的层**，来路不明的值不写进去。"""
        return replace(self, calendar_level=level if level in CALENDAR_LEVELS else None)

    def with_ordering(
        self, group_facet: str | None, sort_facet: str, descending: bool
    ) -> SessionState:
        """记下分组 / 排序选择（见 ADR 0009）。

        ``sort_facet`` 必须是真字段 id：菜单里没有「不排序」，界面手上永远有一个排序字段。
        """
        return replace(
            self,
            group_facet=group_facet,
            sort_facet=sort_facet,
            sort_descending=descending,
        )

    def resolved_calendar_level(self) -> str:
        """日历这一档该用的层。存着的值来路不明（老版本、手改过）就回默认。"""
        level = self.calendar_level
        return level if level in CALENDAR_LEVELS else DEFAULT_CALENDAR_LEVEL

    def resolved_sort_facet(self) -> str:
        """这一档该用的排序字段 id。

        没选过（:attr:`sort_facet` 为 ``None``，或者会话文件里是个来路不明的值）就交回
        :data:`DEFAULT_SORT_FACET` —— 界面没有「不排序」可退回，默认值就是唯一的退路。
        """
        return self.sort_facet or DEFAULT_SORT_FACET


def load_session(*, base: Path | None = None) -> SessionState:
    """读取会话状态；读不出来、结构不对或是别的版本，都返回默认值。"""
    raw = read_text_or_none(paths.app_state_file(base=base))
    if raw is None:
        return SessionState()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return SessionState()
    if not isinstance(data, dict):
        return SessionState()
    # 认版本号：这个键过去只写不读，于是「用于迁移或丢弃」那句话是空的 ——
    # 一份结构不同的老文件会被照新结构解析出一个半懂不懂的状态。
    # 眼下只有 v1，所以不认识的一律当作「读不出来」，退回默认。
    if data.get("version", SESSION_VERSION) != SESSION_VERSION:
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
        calendar_level=_parse_calendar_level(data.get("calendar_level")),
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
        "calendar_level": state.calendar_level,
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
    """排序字段 id；没存过或来路不明都当「没选过」，由 :data:`DEFAULT_SORT_FACET` 接手。

    曾经有一版把「明确不排序」记成哨兵 ``"none"``，那一项后来从菜单里删掉了 ——
    读到它按「没选过」处理即可，用户下次打开就是默认排序字段。
    """
    if isinstance(value, str) and sort_facet(value) is not None:
        return value
    return None


def _parse_calendar_level(value: object) -> str | None:
    """合法的层名，否则 ``None``（由 :meth:`SessionState.resolved_calendar_level` 接手）。"""
    return value if isinstance(value, str) and value in CALENDAR_LEVELS else None


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
    "BROWSE_MODE_CALENDAR",
    "BROWSE_MODE_FLAT",
    "DEFAULT_CALENDAR_LEVEL",
    "DEFAULT_SORT_FACET",
    "MAX_RECENT_ROOTS",
    "SESSION_VERSION",
    "SessionState",
    "WindowGeometry",
    "load_session",
    "save_session",
]
