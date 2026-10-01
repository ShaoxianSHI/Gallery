"""由库根目录推导配置目录下的各个路径，以及应用级状态目录。

这里的函数都是**纯函数**：不创建目录、不读取文件、不检查存在性
（唯一的例外是 ``has_config_dir``，它只回答一个是非问题）。
落盘由调用方显式触发，这样「什么时候会写用户的目录」在代码里始终是可见的。
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from gallery.core.constants import (
    APP_NAME,
    CACHE_DIR_NAME,
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    INDEX_FILE_NAME,
    JOURNAL_DIR_NAME,
    THUMBNAIL_DIR_NAME,
)


def config_dir(root: Path) -> Path:
    """库的配置目录：库根下的隐藏目录（名字见 ``CONFIG_DIR_NAME``）。"""
    return Path(root) / CONFIG_DIR_NAME


def config_file(root: Path) -> Path:
    """库级配置文件：配置目录下的 ``config.toml``。"""
    return config_dir(root) / CONFIG_FILE_NAME


def cache_dir(root: Path) -> Path:
    """库内缓存目录（全部内容都可随时删除）。"""
    return config_dir(root) / CACHE_DIR_NAME


def thumbnail_cache_dir(root: Path) -> Path:
    """库内缩略图缓存目录。"""
    return cache_dir(root) / THUMBNAIL_DIR_NAME


def journal_dir(root: Path) -> Path:
    """元数据写回日志目录（M5 才会真正写入）。"""
    return config_dir(root) / JOURNAL_DIR_NAME


def index_file(root: Path) -> Path:
    """SQLite 索引文件（M2 才会真正写入）。"""
    return config_dir(root) / INDEX_FILE_NAME


def has_config_dir(root: Path) -> bool:
    """库内是否已经存在配置目录。

    界面用它决定要不要先征求用户同意再创建缓存目录。
    """
    return config_dir(root).is_dir()


def library_digest(root: Path) -> str:
    """库路径的稳定短摘要，用于在系统目录下为每个库单独建缓存目录。"""
    resolved = str(Path(root).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def app_data_base() -> Path:
    """当前平台的「应用数据」根目录。"""
    # 先落到局部变量，避免 mypy 按当前平台把 sys.platform 收窄成字面量、
    # 进而把其他平台的分支判成不可达。
    platform: str = sys.platform
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def app_state_dir(*, base: Path | None = None) -> Path:
    """应用自己的状态目录（会话记忆等，不含任何照片数据）。"""
    return (base if base is not None else app_data_base()) / APP_NAME


def app_state_file(*, base: Path | None = None) -> Path:
    """会话状态文件。"""
    return app_state_dir(base=base) / "state.json"


def app_cache_root(*, base: Path | None = None) -> Path:
    """应用级缓存根目录，用于用户拒绝在库内创建配置目录的场景。"""
    return app_state_dir(base=base) / CACHE_DIR_NAME


def external_thumbnail_cache_dir(root: Path, *, base: Path | None = None) -> Path:
    """库外缩略图缓存目录：``<app cache>/thumbs/<库路径摘要>``。"""
    return app_cache_root(base=base) / THUMBNAIL_DIR_NAME / library_digest(root)


__all__ = [
    "app_cache_root",
    "app_data_base",
    "app_state_dir",
    "app_state_file",
    "cache_dir",
    "config_dir",
    "config_file",
    "external_thumbnail_cache_dir",
    "has_config_dir",
    "index_file",
    "journal_dir",
    "library_digest",
    "thumbnail_cache_dir",
]
