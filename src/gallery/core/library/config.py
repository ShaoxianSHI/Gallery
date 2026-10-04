"""库级配置（``config.toml``）的读写。

它是配置目录里唯一不是缓存的文件，所以这里的读写必须防御：
文件缺失用默认值，语法或取值非法给出可行动的中文提示，写入走原子替换。

配置文件的字段对用户是**公开接口**——用户手改 `config.toml` 是预期行为，
因此每个错误提示里都要带上文件路径和合法取值范围。

相册封面（ADR 0028）也在这份文件里，它的两列都是**相对库根的路径**：库挪个位置、
换台机器 clone 一份，那几行照样指着同一张照片。
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_text_atomic
from gallery.core.constants import DEFAULT_MAX_CACHE_BYTES, DEFAULT_THUMBNAIL_MAX_EDGE
from gallery.core.errors import ConfigError

#: 缩略图长边允许的范围。太小看不清，太大就失去缩略图的意义。
MIN_THUMBNAIL_MAX_EDGE: Final = 64
MAX_THUMBNAIL_MAX_EDGE: Final = 2048

#: ``config.toml`` 里那张「相册 → 封面照片」表的键名（ADR 0028）。
ALBUM_COVERS_KEY: Final = "album_covers"

_CONFIG_HEADER: Final = (
    "# Gallery 库级配置。\n"
    "# 这是配置目录中唯一建议纳入版本控制的文件，其余内容都是可重建的缓存。\n"
    "# 字段含义见 README.md；改坏不会损坏照片，删掉本文件会恢复默认值。\n"
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
    #: **相册封面**（ADR 0028）：相册相对库根的路径 → 封面照片相对库根的路径。
    #:
    #: 空 = 一个都没指定，于是每个相册都用「里面第一张照片」（从前的行为，一个字节都不变）。
    #: 存**相对路径**是必须的：绝对路径在库里挪个位置、换台机器就全废了，而这是用户会
    #: 手改、也会跟着库一起提交进版本控制的文件（见模块文档）。
    album_covers: Mapping[str, str] = field(default_factory=dict)

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
        )


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


def prepare_library(root: Path, *, config: LibraryConfig | None = None) -> Path:
    """创建配置目录与默认 ``config.toml``，返回配置文件的路径。

    **这是唯一会写入用户照片目录的函数**，必须由界面在用户明确同意之后调用。
    已存在的配置文件不会被覆盖。
    """
    directory = paths.config_dir(root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"无法创建配置目录：{directory}（{exc.strerror}）。请检查该文件夹的写入权限。",
            path=directory,
            detail=str(exc),
        ) from exc
    file = paths.config_file(root)
    if not file.exists():
        save_config(root, config if config is not None else LibraryConfig())
    return file


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


def _toml_string(value: str) -> str:
    """一个 TOML 基本字符串。

    **借 JSON 来转义**：两者的转义规则在基本字符串上是同一套（``\\"`` ``\\\\``
    与控制字符），而 ``json`` 是标准库里的、谁都认识的那一份。手写一套转义只为省下
    一个 import，代价是将来少一个能一眼看懂的实现。
    """
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "ALBUM_COVERS_KEY",
    "MAX_THUMBNAIL_MAX_EDGE",
    "MIN_THUMBNAIL_MAX_EDGE",
    "LibraryConfig",
    "load_config",
    "prepare_library",
    "save_config",
]
