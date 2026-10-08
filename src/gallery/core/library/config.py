"""库级配置（``config.toml``）的读写：配置目录里唯一不是缓存的文件，用户手改是预期行为。
缺字段用默认值、非法取值给可行动的中文提示（带上路径与合法取值）、写入走原子替换。
相册封面与库级界面偏好 ``[ui]``（ADR 0038 决策 4）也在这里，存的路径都相对库根。
"""

from __future__ import annotations

import contextlib
import json
import logging
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, cast

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_text_atomic
from gallery.core.constants import DEFAULT_MAX_CACHE_BYTES, DEFAULT_THUMBNAIL_MAX_EDGE
from gallery.core.errors import ConfigError
from gallery.core.library.calendar import LEVELS as CALENDAR_LEVELS
from gallery.core.metadata.facets import FACET_MODIFIED, group_facet, sort_facet

logger = logging.getLogger(__name__)

#: 缩略图长边允许的范围。太小看不清，太大就失去缩略图的意义。
MIN_THUMBNAIL_MAX_EDGE: Final = 64
MAX_THUMBNAIL_MAX_EDGE: Final = 2048

#: ``config.toml`` 里那张「相册 → 封面照片」表的键名（ADR 0028）。
ALBUM_COVERS_KEY: Final = "album_covers"

#: 库级界面偏好那张表的键名（ADR 0038 决策 4）。
UI_KEY: Final = "ui"

#: 浏览方式之一：按文件夹逐层进入，子文件夹先以相册形式呈现。
BROWSE_MODE_ALBUMS: Final = "albums"

#: 浏览方式之一：把整个库（含所有子文件夹）的照片平铺在一起。
BROWSE_MODE_FLAT: Final = "flat"

#: 浏览方式之一：按时间浏览（年 / 月 / 周三层，ADR 0033）。
BROWSE_MODE_CALENDAR: Final = "calendar"

#: 允许的浏览方式。界面上只有这三个，不给「不选」。
BROWSE_MODES: Final = (BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT, BROWSE_MODE_CALENDAR)

#: 排序字段的**界面默认值**：用户从没选过排序字段时，按修改时间排 —— 时间是照片之间最实在的
#: 线索，而且文件自己就带着它，不必等 exiftool（按拍摄时间排也有那一项，只是要读一次元数据）。
#: **方向是升序**（旧 → 新），与菜单里其余每一档一致（ADR 0049）。
DEFAULT_SORT_FACET: Final = FACET_MODIFIED

#: 日历视图停在哪一层的默认值。
DEFAULT_CALENDAR_LEVEL: Final = "month"

#: 检索条件的作用域之一：横跨整个库，命中结果**接管**当前那一层（ADR 0026 决策 1）。
SEARCH_SCOPE_LIBRARY: Final = "library"

#: 检索条件的作用域之二：只把**当前这一层**的照片收窄，视图本身不动 —— 月视图仍然是月视图
#: （ADR 0040 决策 1）。
SEARCH_SCOPE_LEVEL: Final = "level"

#: 允许的作用域。界面上只有这两种，不给「不选」—— 没有条件时两者是一回事。
SEARCH_SCOPES: Final = (SEARCH_SCOPE_LIBRARY, SEARCH_SCOPE_LEVEL)

_CONFIG_HEADER: Final = (
    "# Gallery 库级配置。\n"
    "# 这是配置目录中唯一建议纳入版本控制的文件，其余内容都是可重建的缓存。\n"
    "# 字段含义见 README.md；改坏不会损坏照片，删掉本文件会恢复默认值。\n"
)


@dataclass(frozen=True, slots=True)
class LibraryUIState:
    """**库级**界面偏好：上次在这个库里看到哪儿了（软件自己记的，用户要调的是顶层那几个键）。

    字段为 ``None`` 即「从没选过」，由界面默认值接手。与应用级 ``state.json`` 分开，是因为
    换库时这几个该重来，而最近打开列表与窗口几何不该。``thumbnail_edge`` 是网格每格的显示
    档位，与顶层 ``thumbnail_max_edge``（缓存规格）不是一回事，归一是界面的事。
    """

    browse_mode: str | None = None
    metadata_visible: bool | None = None
    group_facet: str | None = None
    sort_facet: str | None = None
    sort_descending: bool | None = None
    calendar_level: str | None = None
    thumbnail_edge: int | None = None
    #: 检索条件的作用域（ADR 0040 决策 1）。**只记作用域，不记条件本身** —— 条件是临时的，
    #: 换库就清空（ADR 0026 决策 10），而「我习惯用筛选而不是搜索」是个关于这个库的偏好。
    search_scope: str | None = None

    @property
    def is_empty(self) -> bool:
        """一个都没记过。也用来判断旧 ``state.json`` 里的值要不要迁过来。"""
        return all(getattr(self, attribute) is None for _, attribute, _ in _UI_FIELDS)

    def with_thumbnail_edge(self, edge: int | None) -> LibraryUIState:
        """换个缩略图档位返回新的一份，原样不动（它是不可变的）。"""
        return replace(self, thumbnail_edge=edge)

    def with_browse_mode(self, mode: str | None) -> LibraryUIState:
        """换浏览方式（平铺 / 相册 / 日历）返回新的一份。"""
        return replace(self, browse_mode=mode)

    def with_metadata_visible(self, visible: bool | None) -> LibraryUIState:
        """换「网格里显不显示这一行元数据」返回新的一份。"""
        return replace(self, metadata_visible=visible)

    def with_calendar_level(self, level: str) -> LibraryUIState:
        """记下日历停在哪一层。**只记合法的层**，来路不明的值不写进去。"""
        return replace(self, calendar_level=level if level in CALENDAR_LEVELS else None)

    def with_search_scope(self, scope: str) -> LibraryUIState:
        """记下检索条件的作用域（ADR 0040）。同样只记合法值。"""
        return replace(self, search_scope=scope if scope in SEARCH_SCOPES else None)

    def resolved_search_scope(self) -> str:
        """这一档该用的检索作用域。没选过、或值来路不明（老版本、手改过）就交回
        :data:`SEARCH_SCOPE_LIBRARY` —— 界面上没有「不选」可退回。"""
        scope = self.search_scope
        if scope in SEARCH_SCOPES:
            return scope
        return SEARCH_SCOPE_LIBRARY

    def with_ordering(
        self, group_facet: str | None, sort_facet: str, descending: bool
    ) -> LibraryUIState:
        """记下分组 / 排序选择（ADR 0009）。``sort_facet`` 要真字段 id：菜单里没有「不排序」。"""
        return replace(
            self,
            group_facet=group_facet,
            sort_facet=sort_facet,
            sort_descending=descending,
        )

    def resolved_calendar_level(self) -> str:
        """日历这一档该用的层。存着的值来路不明（老版本、手改过、程序里直接塞的）就回默认。"""
        level = self.calendar_level
        return level if level in CALENDAR_LEVELS else DEFAULT_CALENDAR_LEVEL

    def resolved_sort_facet(self) -> str:
        """这一档该用的排序字段 id。没选过、或存着的值来路不明（老版本、手改过）就交回
        :data:`DEFAULT_SORT_FACET`，界面上没有「不排序」可退回。

        这里自己再判一次而不靠 :meth:`from_mapping` 的校验：``LibraryUIState`` 也能被直接
        ``LibraryUIState(sort_facet=…)`` 造出来，而「resolve」承诺的是交回一个能用的值。
        """
        if self.sort_facet is not None and sort_facet(self.sort_facet) is not None:
            return self.sort_facet
        return DEFAULT_SORT_FACET

    def to_toml(self) -> str:
        """``[ui]`` 那一张表。**一个都没记过时整段不写**（与相册封面同一原则：没人设过的东西不该留下
        一行等着被误读成「曾经有过设置」）。按固定顺序写，于是每次保存 diff 里没有噪音。
        """
        if self.is_empty:
            return ""
        lines = [
            "\n# 界面偏好：上次在这个库里看到哪儿了（软件自己记的，不必手改）。\n",
            "# 换库时这几个各算各的，所以删掉整节只会回到界面默认值。\n",
            f"[{UI_KEY}]\n",
        ]
        lines.extend(
            f"{key} = {_toml_value(value)}\n"
            for key, attribute, _ in _UI_FIELDS
            if (value := getattr(self, attribute)) is not None
        )
        return "".join(lines)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> LibraryUIState:
        """读 ``[ui]`` 那张表。**值来路不明一律当「没记过」而不是报错**：这张表是软件自己写的，
        手改坏一个分组字段 id 不该让整个库打不开（顶层那几个键是公开接口，非法值必须报错）。
        """
        return cls(
            **cast(
                "dict[str, Any]",
                {attribute: validate(data.get(key)) for key, attribute, validate in _UI_FIELDS},
            )
        )


def _ui_browse_mode(value: object) -> str | None:
    return value if value in BROWSE_MODES else None


def _ui_calendar_level(value: object) -> str | None:
    return value if value in CALENDAR_LEVELS else None


def _ui_search_scope(value: object) -> str | None:
    return value if value in SEARCH_SCOPES else None


def _ui_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _ui_group_facet(value: object) -> str | None:
    """合法的分组字段 id，否则 ``None``（不分组）。"""
    return value if isinstance(value, str) and group_facet(value) is not None else None


def _ui_sort_facet(value: object) -> str | None:
    """合法的排序字段 id，否则 ``None``（没选过，由界面默认值接手）。"""
    return value if isinstance(value, str) and sort_facet(value) is not None else None


def _ui_edge(value: object) -> int | None:
    """正整数才是合法的缩略图长边。bool 是 int 的子类，必须显式排除。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


#: ``[ui]`` 那 8 个字段：``(toml 键名, dataclass 属性名, 读回来时的校验)``。
#:
#: **一份就是全部**：:attr:`LibraryUIState.is_empty`、:meth:`LibraryUIState.to_toml`
#: 与 :meth:`LibraryUIState.from_mapping` 都由它推出来，写出去的顺序也是这个顺序
#: （固定顺序让每次保存的 diff 没有噪音）。三处各写一遍时，加一个偏好要改四处，
#: 而漏掉哪一处都不报错 —— 那一项要么存不下去、要么读不回来，界面上只是
#: 「这个偏好没记住」。字段与 :class:`LibraryUIState` 对不对得上由一条测试盯着。
_UI_FIELDS: Final = (
    ("browse_mode", "browse_mode", _ui_browse_mode),
    ("metadata_visible", "metadata_visible", _ui_bool),
    ("group_facet", "group_facet", _ui_group_facet),
    ("sort_facet", "sort_facet", _ui_sort_facet),
    ("sort_descending", "sort_descending", _ui_bool),
    ("calendar_level", "calendar_level", _ui_calendar_level),
    ("thumbnail_edge", "thumbnail_edge", _ui_edge),
    ("search_scope", "search_scope", _ui_search_scope),
)


@dataclass(frozen=True, slots=True)
class LibraryConfig:
    """一个库的配置。字段都有安全默认值，缺省即默认。"""

    #: 是否递归扫描子文件夹。
    recursive: bool = True
    #: 缩略图长边像素数。
    thumbnail_max_edge: int = DEFAULT_THUMBNAIL_MAX_EDGE
    #: 缩略图缓存上限（字节）。0 表示不限制。
    max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES
    #: **相册封面**（ADR 0028）：相册相对库根的路径 → 封面照片相对库根的路径。空 = 一个都没指定，
    #: 于是每个相册都用「里面第一张照片」。存相对路径是必须的：绝对路径在库里挪个位置、换台
    #: 机器就全废了，而这份文件用户会手改、也会跟着库提交。
    album_covers: Mapping[str, str] = field(default_factory=dict)
    #: **库级界面偏好**（ADR 0038 决策 4）：同样是「关于这个库」的，所以与相册封面同住一份文件。
    ui: LibraryUIState = field(default_factory=LibraryUIState)

    def __post_init__(self) -> None:
        # ``frozen=True`` 只挡得住属性重新赋值，映射本身照样可改：抄一份再包成只读视图，否则封面会
        # 「记住」一个已经改掉的路径，而文件与内存里的内容从此对不上。
        object.__setattr__(self, "album_covers", MappingProxyType(dict(self.album_covers)))

    def to_toml(self) -> str:
        return (
            _CONFIG_HEADER
            + "\n"
            + f"recursive = {_toml_bool(self.recursive)}\n"
            + f"thumbnail_max_edge = {self.thumbnail_max_edge}\n"
            + f"max_cache_bytes = {self.max_cache_bytes}  # 0 表示不限制\n"
            + self._album_covers_toml()
            + self.ui.to_toml()
        )

    def _album_covers_toml(self) -> str:
        """封面那张表（ADR 0028）。**一张封面都没有时整段不写** —— 一个刚建出来的库不该因为多了一节
        空注释就让人以为这里曾经有过设置。按键排序写出去，diff 里才不会有噪音。
        """
        if not self.album_covers:
            return ""
        lines = [
            "\n# 相册封面：键是相册相对库根的路径，值是封面照片相对库根的路径。\n",
            "# 没写在这里的相册用它里面的第一张照片当封面；\n",
            "# 封面照片被移走或改名之后也一样（那一行留着，照片挪回来时又算数）。\n",
            f"[{ALBUM_COVERS_KEY}]\n",
        ]
        lines.extend(
            f"{_toml_string(album)} = {_toml_string(photo)}\n"
            for album, photo in sorted(self.album_covers.items())
        )
        return "".join(lines)

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, object], *, source: Path | None = None
    ) -> LibraryConfig:
        """从 TOML 映射构造配置，逐字段校验类型与范围。"""
        defaults = cls()
        recursive = _read_bool(data, "recursive", defaults.recursive, source=source)
        edge = _read_int(data, "thumbnail_max_edge", defaults.thumbnail_max_edge, source=source)
        if not MIN_THUMBNAIL_MAX_EDGE <= edge <= MAX_THUMBNAIL_MAX_EDGE:
            raise ConfigError(
                f"配置项 thumbnail_max_edge 必须在 {MIN_THUMBNAIL_MAX_EDGE} 到 "
                f"{MAX_THUMBNAIL_MAX_EDGE} 之间，当前是 {edge}。",
                path=source,
            )
        max_bytes = _read_int(data, "max_cache_bytes", defaults.max_cache_bytes, source=source)
        if max_bytes < 0:
            raise ConfigError(
                f"配置项 max_cache_bytes 不能是负数，当前是 {max_bytes}（0 表示不限制）。",
                path=source,
            )
        return cls(
            recursive=recursive,
            thumbnail_max_edge=edge,
            max_cache_bytes=max_bytes,
            album_covers=_read_album_covers(data, source=source),
            ui=LibraryUIState.from_mapping(_read_ui(data, source=source)),
        )


def _read_ui(data: Mapping[str, object], *, source: Path | None) -> Mapping[str, object]:
    """``[ui]`` 那张表。**没有这张表就是没有偏好**，不是错误（老库与刚建出来的库都没有它）；
    类型不对也不报错（那是软件自己写的表），只当空表。
    """
    raw = data.get(UI_KEY, {})
    if not isinstance(raw, Mapping):
        return {}
    return raw


def load_config(root: Path) -> LibraryConfig:
    """读取库配置。文件不存在时返回默认配置。"""
    file = paths.config_file(root)
    raw = read_text_or_none(file)
    if raw is None:
        return LibraryConfig()
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"配置文件语法有误，无法解析：{file}。请修正后重试（或直接删除该文件以恢复默认值）。",
            path=file,
            detail=str(exc),
        ) from exc
    return LibraryConfig.from_mapping(data, source=file)


def save_config(root: Path, config: LibraryConfig) -> None:
    """原子地写出库配置。"""
    file = paths.config_file(root)
    try:
        write_text_atomic(file, config.to_toml())
    except OSError as exc:
        raise ConfigError(
            f"无法写入配置文件：{file}（{exc.strerror}）。请检查该文件夹的写入权限。",
            path=file,
            detail=str(exc),
        ) from exc


def prepare_library(root: Path, *, config: LibraryConfig | None = None) -> Path | None:
    """确保配置目录与 ``config.toml`` 存在，返回配置文件的路径；已存在的不覆盖。

    **建不出来时返回 ``None`` 而不是抛异常**（ADR 0038 决策 3）：只读卷、SMB 共享、网络盘断开
    都会让它建不出来，那时界面要继续开这个库（索引退成内存缓存、缩略图照常去系统目录），只在
    横幅上说一声 —— 用户其实没有第三个有意义的选择。
    """
    directory = paths.config_dir(root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.info("建不了配置目录，索引将只在内存里用：%s", directory, exc_info=True)
        return None
    file = paths.config_file(root)
    if not file.exists():
        save_config(root, config if config is not None else LibraryConfig())
    return file


def discard_legacy_thumb_cache(root: Path) -> int:
    """删掉**旧版**的库内缩略图缓存目录，返回删掉的文件数（ADR 0038 决策 6）。

    里面全是可再生的缩略图，删掉最坏结果是重新解码一遍（红线 5）。**只删 Gallery 自己写的那个
    子目录**：配置目录里的 ``config.toml``、``index.db`` 与 ``journal/`` 一个都不碰。

    收完 ``thumbs`` 还要再收只装过它的父目录 ``cache/``（判据是它空不空）。这一步**不能挂在
    「thumbs 还在不在」这个早退后面** —— 一次开库之后 thumbs 就没了，第二次打开时那个空壳还在。

    :returns: 删掉的**文件**数；没有文件可删时是 0（空壳目录照收不误）。
    """
    directory = paths.legacy_thumbnail_cache_dir(root)
    removed = 0
    if directory.is_dir():
        try:
            for entry in directory.iterdir():
                if not entry.is_file():
                    continue
                try:
                    entry.unlink()
                    removed += 1
                except OSError:
                    logger.info("删不掉旧缩略图缓存里的文件：%s", entry, exc_info=True)
        except OSError:
            logger.info("读不了旧缩略图缓存目录：%s", directory, exc_info=True)
    for path in (directory, paths.legacy_cache_dir(root)):
        with contextlib.suppress(OSError):
            path.rmdir()
    return removed


def discard_legacy_external_index() -> int:
    """删掉**旧版**的库外索引目录，返回删掉的文件数（ADR 0038 决策 6）。

    只删目录名确实是旧索引那一个、且只删 ``.db``，免得将来 ``<app cache>/`` 底下多出别的东西
    时被误删。它是**整台机器一份**的，与当前打开的是哪个库无关。

    :returns: 删掉的文件数；目录本来就不存在时是 0。
    """
    directory = paths.app_index_cache_dir()
    if not directory.is_dir():
        return 0
    removed = 0
    try:
        for entry in directory.iterdir():
            if not entry.is_file() or entry.suffix != ".db":
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                logger.info("删不掉旧的库外索引：%s", entry, exc_info=True)
    except OSError:
        logger.info("读不了旧的库外索引目录：%s", directory, exc_info=True)
    with contextlib.suppress(OSError):
        directory.rmdir()
    return removed


def _read_album_covers(data: Mapping[str, object], *, source: Path | None) -> dict[str, str]:
    """读那张「相册 → 封面照片」表，逐条校验（ADR 0028）。**没有这张表就是没有封面**，不是错误。

    校验比别的字段严，是因为这两列**会被拼成路径去读用户的照片**：一个 ``../..`` 或一个绝对
    路径会让「封面」指向库外的东西。宁可打开库时就给一句能照着改的中文提示。
    """
    raw = data.get(ALBUM_COVERS_KEY, {})
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"配置项 {ALBUM_COVERS_KEY} 必须是一张表（形如 [{ALBUM_COVERS_KEY}]），"
            f"当前是 {raw!r}。",
            path=source,
        )
    covers: dict[str, str] = {}
    for album, photo in raw.items():
        label = f"{ALBUM_COVERS_KEY} 里的相册「{album}」"
        key = _read_relative(label, str(album), source=source)
        if not isinstance(photo, str):
            raise ConfigError(
                f"{label} 的封面必须写成照片的相对路径（字符串），当前是 {photo!r}。",
                path=source,
            )
        cover = _read_relative(f"{label} 的封面", photo, source=source)
        if not cover.startswith(f"{key}/"):
            # 子相册里的照片算数，兄弟相册里的不算 —— 那不是这个相册的封面。
            raise ConfigError(
                f"{label} 的封面「{photo}」不在这个相册里；"
                f"封面要写成这个相册里的照片，例如「{key}/照片.jpg」。",
                path=source,
            )
        covers[key] = cover
    return covers


def _read_relative(label: str, value: str, *, source: Path | None) -> str:
    """一个相对库根的路径：不能空、不能是绝对路径、不能带 ``..``。

    顺手归一化成 posix 写法（``./2025/苏州/`` → ``2025/苏州``），否则一个手写成 ``./京都`` 的
    相册名就能让封面「设置成功却读不到」。
    """
    path = PurePosixPath(value)
    if not value or not path.parts:
        raise ConfigError(f"{label} 不能是空路径。", path=source)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(
            f"{label}「{value}」必须是相对库根目录的路径：不能以 / 开头，也不能带 ..。",
            path=source,
        )
    return path.as_posix()


def _read_bool(data: Mapping[str, object], key: str, default: bool, *, source: Path | None) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(
            f"配置项 {key} 必须是 true 或 false，当前是 {value!r}。",
            path=source,
        )
    return value


def _read_int(data: Mapping[str, object], key: str, default: int, *, source: Path | None) -> int:
    value = data.get(key, default)
    # bool 是 int 的子类，必须显式排除，否则 `recursive = true` 会被当成整数 1。
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"配置项 {key} 必须是整数，当前是 {value!r}。",
            path=source,
        )
    return value


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_value(value: str | bool | int) -> str:
    """``[ui]`` 表里的值：字符串走 TOML 基本字符串，布尔与整数直写（那一节只有这三种类型，
    真多出一种时再改这里）。

    **``bool`` 必须先判**：它是 ``int`` 的子类，反过来写会把 ``sort_descending = True``
    落进文件里 —— 那不是 TOML，下次读这个库直接报语法错。
    """
    if isinstance(value, bool):
        return _toml_bool(value)
    return str(value) if isinstance(value, int) else _toml_string(value)


def _toml_string(value: str) -> str:
    """一个 TOML 基本字符串——借 JSON 来转义。两者在基本字符串上的转义规则是同一套（``\\"`` ``\\\\``
    与控制字符），而 ``json`` 是标准库里谁都认识的那一份。
    """
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "ALBUM_COVERS_KEY",
    "BROWSE_MODES",
    "BROWSE_MODE_ALBUMS",
    "BROWSE_MODE_CALENDAR",
    "BROWSE_MODE_FLAT",
    "DEFAULT_CALENDAR_LEVEL",
    "DEFAULT_SORT_FACET",
    "MAX_THUMBNAIL_MAX_EDGE",
    "MIN_THUMBNAIL_MAX_EDGE",
    "SEARCH_SCOPES",
    "SEARCH_SCOPE_LEVEL",
    "SEARCH_SCOPE_LIBRARY",
    "UI_KEY",
    "LibraryConfig",
    "LibraryUIState",
    "discard_legacy_thumb_cache",
    "load_config",
    "prepare_library",
    "save_config",
]
