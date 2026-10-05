"""库级配置（``config.toml``）的读写。

它是配置目录里唯一不是缓存的文件，所以这里的读写必须防御：
文件缺失用默认值，语法或取值非法给出可行动的中文提示，写入走原子替换。

配置文件的字段对用户是**公开接口**——用户手改 `config.toml` 是预期行为，
因此每个错误提示里都要带上文件路径和合法取值范围。

相册封面（ADR 0028）也在这份文件里，它的两列都是**相对库根的路径**：库挪个位置、
换台机器 clone 一份，那几行照样指着同一张照片。

**库级界面偏好（``[ui]`` 表）也在这里**（ADR 0038 决策 4）。它原先住在应用级的
``state.json`` 里，与「最近打开过哪些库」「窗口多大」混在一起 —— 但换库就该重来，
而 ``state.json`` 那两样是应用级的（打开软件时还不知道要开哪个库）。拆开之后
「用 A 库的地点分组习惯切到 B 库」会回到 B 库自己的设置，这正是要的。
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
from typing import Final

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

#: 排序字段的**界面默认值**：用户从没选过排序字段时，按修改时间排。
#:
#: 时间才是照片之间最实在的那条线索，而**文件自己就带着它** —— 不必等 exiftool，
#: 打开一个库就能立刻排好顺序（``FILE_FACET_IDS`` 里的字段才做得到这一点）。
#: 想按拍摄时间排的，「排序」菜单里有那一项，读一次元数据即可切过去。
DEFAULT_SORT_FACET: Final = FACET_MODIFIED

#: 日历视图停在哪一层的默认值。
DEFAULT_CALENDAR_LEVEL: Final = "month"

_CONFIG_HEADER: Final = (
    "# Gallery 库级配置。\n"
    "# 这是配置目录中唯一建议纳入版本控制的文件，其余内容都是可重建的缓存。\n"
    "# 字段含义见 README.md；改坏不会损坏照片，删掉本文件会恢复默认值。\n"
)


@dataclass(frozen=True, slots=True)
class LibraryUIState:
    """**库级**界面偏好：上次在这个库里看到哪儿了。

    每个字段的 ``None`` 都有确切含义 ——「从没选过」，由界面默认值接手。它与
    ``state.json`` 里的应用级状态分开，是因为判据不同：换库时这几个该重来，
    最近打开列表与窗口几何不该。

    **这些字段是软件自己记的，不必手改。** 用户要调的是同一文件里那几个顶层键
    （``recursive`` / ``thumbnail_max_edge`` / ``max_cache_bytes``）与
    ``[album_covers]``。

    ``thumbnail_edge`` 是网格里每格显示多大，与顶层的 ``thumbnail_max_edge``
    （缓存里存多大）**不是一回事**：后者是缓存规格，前者是显示档位。档位表在
    ``ui/browser/thumb_sizes.py``，这里只保证它是个正整数 —— 归一（就近取档）是
    界面的事，``core`` 不该再定义一份档位表出来。
    """

    browse_mode: str | None = None
    metadata_visible: bool | None = None
    group_facet: str | None = None
    sort_facet: str | None = None
    sort_descending: bool | None = None
    calendar_level: str | None = None
    thumbnail_edge: int | None = None

    @property
    def is_empty(self) -> bool:
        """一个都没记过。也用来判断旧 ``state.json`` 里的值要不要迁过来。"""
        return all(
            value is None
            for value in (
                self.browse_mode,
                self.metadata_visible,
                self.group_facet,
                self.sort_facet,
                self.sort_descending,
                self.calendar_level,
                self.thumbnail_edge,
            )
        )

    def with_thumbnail_edge(self, edge: int | None) -> LibraryUIState:
        return replace(self, thumbnail_edge=edge)

    def with_browse_mode(self, mode: str | None) -> LibraryUIState:
        return replace(self, browse_mode=mode)

    def with_metadata_visible(self, visible: bool | None) -> LibraryUIState:
        return replace(self, metadata_visible=visible)

    def with_calendar_level(self, level: str) -> LibraryUIState:
        """记下日历停在哪一层。**只记合法的层**，来路不明的值不写进去。"""
        return replace(self, calendar_level=level if level in CALENDAR_LEVELS else None)

    def with_ordering(
        self, group_facet: str | None, sort_facet: str, descending: bool
    ) -> LibraryUIState:
        """记下分组 / 排序选择（见 ADR 0009）。

        ``sort_facet`` 必须是真字段 id：菜单里没有「不排序」，界面上永远有一个排序字段。
        """
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
        """这一档该用的排序字段 id。

        没选过、或存着的值来路不明（老版本、手改过、程序里直接塞的）就交回
        :data:`DEFAULT_SORT_FACET` —— 界面上没有「不排序」可退回，默认值就是唯一的退路。

        **这里自己再判一次，不靠 :meth:`from_mapping` 的校验**：``LibraryUIState``
        也能被直接 ``LibraryUIState(sort_facet=…)`` 造出来，而「resolve」这个词
        承诺的是「交回一个能用的值」—— 那就不能只对读文件那条路成立。
        """
        if self.sort_facet is not None and sort_facet(self.sort_facet) is not None:
            return self.sort_facet
        return DEFAULT_SORT_FACET

    def to_toml(self) -> str:
        """``[ui]`` 那一张表。**一个都没记过时整段不写** —— 跟相册封面同一原则：
        没人设过的东西不该在文件里留下一行等着被误读成「曾经有过设置」。

        按固定顺序写，于是同一个库的 ``config.toml`` 每次保存都长得一样，
        跟着库提交进版本控制时 diff 里全是噪音。
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
            for key, value in (
                ("browse_mode", self.browse_mode),
                ("metadata_visible", self.metadata_visible),
                ("group_facet", self.group_facet),
                ("sort_facet", self.sort_facet),
                ("sort_descending", self.sort_descending),
                ("calendar_level", self.calendar_level),
                ("thumbnail_edge", self.thumbnail_edge),
            )
            if value is not None
        )
        return "".join(lines)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> LibraryUIState:
        """读 ``[ui]`` 那张表。

        **值来路不明一律当「没记过」而不是报错** —— 与会话文件同一套理由：
        这张表是软件自己写的，手改坏一个分组字段 id 不该让整个库打不开。
        （对照：顶层那几个键是公开接口，非法值必须报错并说清合法取值。）
        """
        browse = data.get("browse_mode")
        level = data.get("calendar_level")
        return cls(
            browse_mode=browse if browse in BROWSE_MODES else None,
            metadata_visible=_ui_bool(data.get("metadata_visible")),
            group_facet=_ui_group_facet(data.get("group_facet")),
            sort_facet=_ui_sort_facet(data.get("sort_facet")),
            sort_descending=_ui_bool(data.get("sort_descending")),
            calendar_level=level if level in CALENDAR_LEVELS else None,
            thumbnail_edge=_ui_edge(data.get("thumbnail_edge")),
        )


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


@dataclass(frozen=True, slots=True)
class LibraryConfig:
    """一个库的配置。字段都有安全默认值，缺省即默认。"""

    #: 是否递归扫描子文件夹。
    recursive: bool = True
    #: 缩略图长边像素数。
    thumbnail_max_edge: int = DEFAULT_THUMBNAIL_MAX_EDGE
    #: 缩略图缓存上限（字节）。0 表示不限制。
    max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES
    #: **相册封面**（ADR 0028）：相册相对库根的路径 → 封面照片相对库根的路径。
    #:
    #: 空 = 一个都没指定，于是每个相册都用「里面第一张照片」（从前的行为，一个字节都不变）。
    #: 存**相对路径**是必须的：绝对路径在库里挪个位置、换台机器就全废了，而这是用户会
    #: 手改、也会跟着库一起提交进版本控制的文件（见模块文档）。
    album_covers: Mapping[str, str] = field(default_factory=dict)
    #: **库级界面偏好**（ADR 0038 决策 4）：上次在这个库里看到哪儿了。
    #:
    #: 与相册封面一样住在 ``config.toml`` 里，理由是同一句：它是「关于这个库」的。
    #: 它原先在应用级的 ``state.json`` 里，于是「在 A 库选了地点分组」会跟着切到 B 库。
    ui: LibraryUIState = field(default_factory=LibraryUIState)

    def __post_init__(self) -> None:
        # ``frozen=True`` 只挡得住属性**重新赋值**，映射本身照样可改。这份映射是从用户
        # 手改的 TOML 里读出来的：抄一份再包成只读视图，它才不会在别处被悄悄改掉，
        # 而 ``config.toml`` 与内存里的内容从此对不上（封面会「记住」一个已经改掉的路径）。
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
        """封面那张表（ADR 0028）。**一张封面都没有时整段不写** ——
        一个刚建出来的库不该因为多了一节空注释就让人以为这里曾经有过设置。

        按键排序写出去：否则同一份配置每次保存的顺序都可能不同，diff 里全是噪音，
        而这是用户会跟着库一起提交进版本控制的文件。
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
    """``[ui]`` 那张表。**没有这张表就是没有偏好**，不是错误 ——
    从前的库、刚建出来的库都没有它。

    类型不对也不报错（那是软件自己写的表），只是当成空表。
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
    """确保配置目录与 ``config.toml`` 存在，返回配置文件的路径。

    **建不出来时返回 ``None`` 而不是抛异常**（ADR 0038 决策 3）：这个目录是打开库时
    自动建的，而只读卷、SMB 共享、网络盘断开都会让它建不出来 —— 那时界面要继续开
    这个库（索引退成内存缓存、缩略图照常去系统目录），只在横幅上说一声。
    弹窗那条路已经删掉了：用户其实没有第三个有意义的选择。

    已存在的配置文件不会被覆盖。
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

    缩略图搬去系统目录之后 配置目录下的 ``cache/`` 整块作废 —— 它正是「同一个库两份
    缓存」那份重复。里面全是可再生的缩略图，删掉最坏结果是下次浏览重新解码一遍
    （红线 5）。

    **只删 Gallery 自己写的那个子目录**，配置目录 里的 ``config.toml``、
    ``index.db`` 与 ``journal/`` 一个都不碰 —— 前者不是缓存，后两者删掉有代价
    （见 README 关于 ``journal`` 那段例外）。

    **收完 ``thumbs`` 还要再收那个只装过它的父目录 ``cache/``**，判据是它空不空
    （``rmdir`` 对非空目录会失败）：里面还有别人的东西时它自然留在原地。
    这一步**不能挂在「thumbs 还在不在」这个早退后面** —— 一次开库之后 thumbs 就
    没了，第二次打开时那个空壳还在，早退就永远走不到收它那一句（实测过一次：
    库内只剩下一个空的 ``cache/``，而这条清理报「0 个」）。

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

    「把索引放到系统目录」那条路整个删掉了，于是 ``<app cache>/index/`` 里那几份
    ``<摘要>.db`` 再也不会有人读、也不会有人写。删之前**先确认目录名是旧的索引那一个**
    （见 :data:`~gallery.core.constants.INDEX_CACHE_DIR_NAME` 的注释），
    免得将来 ``<app cache>/`` 底下多出别的东西时把不该删的删掉。

    与 :func:`discard_legacy_thumb_cache` 不同，这个是**整台机器一份**的：库外索引
    本来就按库摘要分文件躺在同一个目录里，与当前打开的是哪个库无关。

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
    """读那张「相册 → 封面照片」表，逐条校验（ADR 0028）。

    **没有这张表就是没有封面**，不是一个错误 —— 从前的库、刚建出来的库都没有它。

    校验比别的字段严，是因为这两列**会被拼成路径去读用户的照片**：一个 ``../..`` 或者
    一个绝对路径会让「封面」指向库外的东西。宁可在打开库时就给一句能照着改的中文提示，
    也不要等到界面上画出一张莫名其妙的图。
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
            # 子相册里的照片算数（键本来就只是一段相对路径），兄弟相册里的不算 ——
            # 那不是这个相册的封面。
            raise ConfigError(
                f"{label} 的封面「{photo}」不在这个相册里；"
                f"封面要写成这个相册里的照片，例如「{key}/照片.jpg」。",
                path=source,
            )
        covers[key] = cover
    return covers


def _read_relative(label: str, value: str, *, source: Path | None) -> str:
    """一个相对库根的路径：不能空、不能是绝对路径、不能带 ``..``。

    顺手归一化成 posix 写法（``./2025/苏州/`` → ``2025/苏州``），于是
    「相册」与「封面」两列、以及界面上拼出来的那把键，比较的都是同一种写法 ——
    否则一个手写成 ``./京都`` 的相册名就能让封面「设置成功却读不到」。
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
    """``[ui]`` 表里的值：字符串走 TOML 基本字符串，布尔与整数直写。

    那一节只有字符串、布尔与正整数三种类型，所以不必做成一张类型分发表的通用实现 ——
    真多出一种类型时再改这里，别先把架子搭起来。

    **``bool`` 必须先判**：它是 ``int`` 的子类，反过来写会把 ``sort_descending = True``
    落进文件里 —— 那不是 TOML，下次读这个库直接报语法错。
    """
    if isinstance(value, bool):
        return _toml_bool(value)
    return str(value) if isinstance(value, int) else _toml_string(value)


def _toml_string(value: str) -> str:
    """一个 TOML 基本字符串。

    **借 JSON 来转义**：两者的转义规则在基本字符串上是同一套（``\\"`` ``\\\\``
    与控制字符），而 ``json`` 是标准库里的、谁都认识的那一份。手写一套转义只为省下
    一个 import，代价是将来少一个能一眼看懂的实现。
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
    "UI_KEY",
    "LibraryConfig",
    "LibraryUIState",
    "discard_legacy_thumb_cache",
    "load_config",
    "prepare_library",
    "save_config",
]
