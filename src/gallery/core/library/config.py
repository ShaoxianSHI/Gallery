"""库级配置（``config.toml``）的读写。

它是配置目录里唯一不是缓存的文件，所以这里的读写必须防御：
文件缺失用默认值，语法或取值非法给出可行动的中文提示，写入走原子替换。

配置文件的字段对用户是**公开接口**——用户手改 `config.toml` 是预期行为，
因此每个错误提示里都要带上文件路径和合法取值范围。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_text_atomic
from gallery.core.constants import DEFAULT_MAX_CACHE_BYTES, DEFAULT_THUMBNAIL_MAX_EDGE
from gallery.core.errors import ConfigError

#: 缩略图长边允许的范围。太小看不清，太大就失去缩略图的意义。
MIN_THUMBNAIL_MAX_EDGE: Final = 64
MAX_THUMBNAIL_MAX_EDGE: Final = 2048

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

    def to_toml(self) -> str:
        return (
            _CONFIG_HEADER
            + "\n"
            + f"recursive = {_toml_bool(self.recursive)}\n"
            + f"thumbnail_max_edge = {self.thumbnail_max_edge}\n"
            + f"max_cache_bytes = {self.max_cache_bytes}  # 0 表示不限制\n"
        )

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
        return cls(recursive=recursive, thumbnail_max_edge=edge, max_cache_bytes=max_bytes)


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


__all__ = [
    "MAX_THUMBNAIL_MAX_EDGE",
    "MIN_THUMBNAIL_MAX_EDGE",
    "LibraryConfig",
    "load_config",
    "prepare_library",
    "save_config",
]
