"""应用级会话状态：最近打开的库、窗口几何、与库无关的偏好。

它**不是数据**：会话文件损坏、字段缺失、路径已不存在，都静默回退到默认值 —— 绝不能让
「记不住上次开了哪个文件夹」变成打不开软件。这里只剩应用级的那几样（ADR 0038 决策 4），
判据是「打开软件时还不知道要开哪个库」；库级偏好在库的 ``config.toml`` 的 ``[ui]`` 表里。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
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


#: 动画速度的档位：慢、中、快、关（ADR 0073）。**应用级**，换库不该把它重置。
ANIMATION_SPEEDS: Final = ("slow", "normal", "fast", "off")
DEFAULT_ANIMATION_SPEED: Final = "normal"

#: 外观档位：跟随系统、浅色、深色。
APPEARANCES: Final = ("system", "light", "dark")
DEFAULT_APPEARANCE: Final = "system"

#: 语言偏好：跟随系统 / 简体中文 / English（ADR 0106）。**应用级**：换库不该把它重置。
LANGUAGE_PREFERENCES: Final = ("system", "zh_CN", "en")
DEFAULT_LANGUAGE_PREFERENCE: Final = "system"

#: 背景色的分界（HSL 的 ``L``）：浅色那一档要亮于它、深色那一档要暗于它。
#: 只换底不换字，所以两档各自被限制在自己那一侧 —— 否则正文压在背景上看不清（ADR 0097）。
BACKGROUND_LIGHTNESS_SPLIT: Final = 0.5

#: 放映自动前进的默认间隔（秒）。界面上的档位是 3 / 5 / 8 / 15。
DEFAULT_SLIDESHOW_SECONDS: Final = 3
MIN_SLIDESHOW_SECONDS: Final = 1
MAX_SLIDESHOW_SECONDS: Final = 60

#: 放映背景音乐的音量档位（百分比）。0 是静音。**应用级** —— 与放映间隔同类，
#: 换库不该把音量重置（ADR 0109）。
MUSIC_VOLUMES: Final = (0, 30, 60, 100)
DEFAULT_MUSIC_VOLUME: Final = 60

#: 「用外部应用打开」的四个位置（ADR 0080 / 0094）。与 ``PageRole`` 同一套名字：
#: 大图页的照片 / 大图页的视频 / 网格页 / 日历页。次序**只对这个应用有意义**，所以进会话偏好
#: 而不进库配置。
EXTERNAL_PAGE_DETAIL: Final = "detail"
EXTERNAL_PAGE_VIDEO: Final = "video"
EXTERNAL_PAGE_GRID: Final = "grid"
EXTERNAL_PAGE_CALENDAR: Final = "calendar"
EXTERNAL_PAGES: Final = (
    EXTERNAL_PAGE_DETAIL,
    EXTERNAL_PAGE_VIDEO,
    EXTERNAL_PAGE_GRID,
    EXTERNAL_PAGE_CALENDAR,
)


@dataclass(frozen=True, slots=True)
class Preferences:
    """与打开哪个库无关的偏好（ADR 0073），因此进不了任何一份 ``config.toml``。

    每一项都自带「值来路不明就回默认」的解析：这份文件会被手改，也会被老版本写坏。
    """

    animation_speed: str = DEFAULT_ANIMATION_SPEED
    appearance: str = DEFAULT_APPEARANCE
    language: str = DEFAULT_LANGUAGE_PREFERENCE
    #: 两档自定义背景色（``#rrggbb``），空串 = 用系统那一档（ADR 0097）。
    light_background: str = ""
    dark_background: str = ""
    #: 两档各自的背景图（背景库里的 id），空串 = 用背景色（ADR 0098）。
    #: 认不出来的 id 只让那一档退回颜色，不当成错误 —— 背景库可能被整个清掉。
    light_backdrop: str = ""
    dark_backdrop: str = ""
    slideshow_seconds: int = DEFAULT_SLIDESHOW_SECONDS
    #: 放映背景音乐的音量（0–100）。手改越界时收到边界，不跟着静音。
    music_volume: int = DEFAULT_MUSIC_VOLUME
    open_last_library: bool = True
    #: 网格与大图页标题上写不写文件扩展名（视图菜单里那一项）。
    show_file_extensions: bool = True
    #: 大图页底部摆不摆那一条缩略图导航（视图菜单里那一项，ADR 0107）。
    show_filmstrip: bool = True
    #: 四个位置各自的「用外部应用打开」次序（页面 → 应用 id）。空就是内置次序（ADR 0080）。
    external_orders: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def resolved_animation_speed(self) -> str:
        """这一档该用的动画速度；不认识的值（老版本、手改坏）回默认。"""
        if self.animation_speed in ANIMATION_SPEEDS:
            return self.animation_speed
        return DEFAULT_ANIMATION_SPEED

    def resolved_appearance(self) -> str:
        """这一档该用的外观；不认识的值回「跟随系统」。"""
        if self.appearance in APPEARANCES:
            return self.appearance
        return DEFAULT_APPEARANCE

    def resolved_language(self) -> str:
        """这一档该用的语言偏好；不认识的值（老版本、手改坏）回「跟随系统」。"""
        if self.language in LANGUAGE_PREFERENCES:
            return self.language
        return DEFAULT_LANGUAGE_PREFERENCE

    def resolved_slideshow_seconds(self) -> int:
        """放映间隔（秒）；越界或类型不对回默认。"""
        seconds = self.slideshow_seconds
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return DEFAULT_SLIDESHOW_SECONDS
        if MIN_SLIDESHOW_SECONDS <= seconds <= MAX_SLIDESHOW_SECONDS:
            return seconds
        return DEFAULT_SLIDESHOW_SECONDS

    def resolved_music_volume(self) -> int:
        """背景音乐音量（0–100）；类型不对回默认，越界收到边界。"""
        value = self.music_volume
        if isinstance(value, bool) or not isinstance(value, int):
            return DEFAULT_MUSIC_VOLUME
        return min(max(value, 0), 100)

    def background_for(self, *, dark: bool) -> str | None:
        """这一档该用的背景色；没设过（或设得不合法）返回 ``None`` —— 用系统那一档。"""
        value = self.dark_background if dark else self.light_background
        return normalize_background(value, dark=dark)

    def with_background(self, value: str | None, *, dark: bool) -> Preferences:
        """设 / 清某一档；``None`` 或空串表示恢复系统那一档。"""
        clean = "" if value is None else (normalize_background(value, dark=dark) or "")
        if dark:
            return replace(self, dark_background=clean)
        return replace(self, light_background=clean)

    def backdrop_for(self, *, dark: bool) -> str:
        """这一档该用哪张背景图；空串表示用背景色。"""
        value = self.dark_backdrop if dark else self.light_backdrop
        return value if isinstance(value, str) else ""

    def with_backdrop(self, backdrop_id: str | None, *, dark: bool) -> Preferences:
        """设 / 清某一档的背景图；``None`` 或空串表示回到背景色。"""
        clean = "" if backdrop_id is None else backdrop_id.strip()
        if dark:
            return replace(self, dark_backdrop=clean)
        return replace(self, light_backdrop=clean)

    def order_for(self, page: str) -> tuple[str, ...]:
        """这一页的次序；没设过就是空元组 —— 空元组的意思是「照内置次序」。"""
        for name, ids in self.external_orders:
            if name == page:
                return ids
        return ()

    def with_external_order(self, page: str, ids: Sequence[str]) -> Preferences:
        """换掉某一页的次序；``ids`` 为空表示恢复内置次序。

        **只按 :data:`EXTERNAL_PAGES` 那四个位置存**，于是写出去的 JSON 每次都是同一个形状。
        """
        if page not in EXTERNAL_PAGES:
            raise ValueError(f"page 只能是 {EXTERNAL_PAGES} 之一，收到 {page!r}")
        orders = dict(self.external_orders)
        orders[page] = tuple(ids)
        kept = tuple((name, orders[name]) for name in EXTERNAL_PAGES if orders.get(name))
        return replace(self, external_orders=kept)

    def to_json(self) -> dict[str, object]:
        return {
            "animation_speed": self.resolved_animation_speed(),
            "appearance": self.resolved_appearance(),
            "language": self.resolved_language(),
            "light_background": self.background_for(dark=False) or "",
            "dark_background": self.background_for(dark=True) or "",
            "light_backdrop": self.backdrop_for(dark=False),
            "dark_backdrop": self.backdrop_for(dark=True),
            "slideshow_seconds": self.resolved_slideshow_seconds(),
            "music_volume": self.resolved_music_volume(),
            "open_last_library": self.open_last_library,
            "show_file_extensions": self.show_file_extensions,
            "show_filmstrip": self.show_filmstrip,
            "external_orders": {name: list(ids) for name, ids in self.external_orders},
        }

    @classmethod
    def from_mapping(cls, value: object) -> Preferences:
        """读那张表。**任何一项来路不明都只回它自己的默认值**，不牵连其余几项。"""
        if not isinstance(value, dict):
            return cls()
        speed = value.get("animation_speed")
        appearance = value.get("appearance")
        language = value.get("language")
        seconds = value.get("slideshow_seconds")
        volume = value.get("music_volume")
        open_last = value.get("open_last_library")
        show_extensions = value.get("show_file_extensions")
        show_filmstrip = value.get("show_filmstrip")
        return cls(
            animation_speed=speed if isinstance(speed, str) else DEFAULT_ANIMATION_SPEED,
            appearance=appearance if isinstance(appearance, str) else DEFAULT_APPEARANCE,
            language=language if isinstance(language, str) else DEFAULT_LANGUAGE_PREFERENCE,
            light_background=normalize_background(value.get("light_background"), dark=False) or "",
            dark_background=normalize_background(value.get("dark_background"), dark=True) or "",
            light_backdrop=_text(value.get("light_backdrop")),
            dark_backdrop=_text(value.get("dark_backdrop")),
            slideshow_seconds=seconds
            if isinstance(seconds, int) and not isinstance(seconds, bool)
            else DEFAULT_SLIDESHOW_SECONDS,
            music_volume=volume
            if isinstance(volume, int) and not isinstance(volume, bool)
            else DEFAULT_MUSIC_VOLUME,
            open_last_library=open_last if isinstance(open_last, bool) else True,
            show_file_extensions=show_extensions if isinstance(show_extensions, bool) else True,
            show_filmstrip=show_filmstrip if isinstance(show_filmstrip, bool) else True,
            external_orders=_parse_external_orders(value.get("external_orders")),
        )


def normalize_background(value: object, *, dark: bool) -> str | None:
    """把一个颜色收成 ``#rrggbb``；不认识、或者落在另一侧的一律当没设过。

    **为什么要卡那一侧**：正文色取的是系统那一套（深色下浅字、浅色下深字），而这一档的颜色
    只换底不换字 —— 挑反了就是浅字压浅底，而那种界面没有任何地方会报错。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        return None
    try:
        red, green, blue = (int(text[index : index + 2], 16) for index in (1, 3, 5))
    except ValueError:
        return None
    lightness = (max(red, green, blue) + min(red, green, blue)) / 2 / 255
    if (lightness < BACKGROUND_LIGHTNESS_SPLIT) != dark:
        return None
    return f"#{red:02x}{green:02x}{blue:02x}"


def _text(value: object) -> str:
    """用户会手改这份文件：不是字符串就当没设过。"""
    return value.strip() if isinstance(value, str) else ""


def _parse_external_orders(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """只认那四个位置与字符串 id —— 手改坏的、老版本的、多出来的键一律静默丢掉。"""
    if not isinstance(value, dict):
        return ()
    kept: list[tuple[str, tuple[str, ...]]] = []
    for page in EXTERNAL_PAGES:
        ids = value.get(page)
        if not isinstance(ids, list):
            continue
        names = tuple(item for item in ids if isinstance(item, str))
        if names:
            kept.append((page, names))
    return tuple(kept)


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
    #: 与库无关的那几项（ADR 0073）。默认值就是「什么都没改过」。
    preferences: Preferences = field(default_factory=Preferences)

    def with_preferences(self, preferences: Preferences) -> SessionState:
        """换一份偏好返回新的一份，其余字段原样带过去。"""
        return replace(self, preferences=preferences)

    def with_recent_root(self, root: Path) -> SessionState:
        """把 ``root`` 提到最前面，去重并截断到上限。"""
        root = Path(root)
        others = tuple(item for item in self.recent_roots if item != root)
        return replace(self, recent_roots=(root, *others)[:MAX_RECENT_ROOTS])

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
        preferences=Preferences.from_mapping(data.get("preferences")),
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
        "preferences": state.preferences.to_json(),
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
    "ANIMATION_SPEEDS",
    "APPEARANCES",
    "BACKGROUND_LIGHTNESS_SPLIT",
    "DEFAULT_ANIMATION_SPEED",
    "DEFAULT_APPEARANCE",
    "DEFAULT_LANGUAGE_PREFERENCE",
    "DEFAULT_MUSIC_VOLUME",
    "DEFAULT_SLIDESHOW_SECONDS",
    "LANGUAGE_PREFERENCES",
    "LEGACY_UI_KEYS",
    "MAX_RECENT_ROOTS",
    "MAX_SLIDESHOW_SECONDS",
    "MIN_SLIDESHOW_SECONDS",
    "MUSIC_VOLUMES",
    "SESSION_VERSION",
    "Preferences",
    "SessionState",
    "WindowGeometry",
    "legacy_ui_state",
    "load_session",
    "normalize_background",
    "save_session",
]
