"""由库根目录推导配置目录下的各个路径，以及应用级状态目录。

这里的函数都是**纯函数**：不创建目录、不读取文件、不检查存在性
（唯一的例外是 ``has_config_dir``，它只回答一个是非问题）。
落盘由调用方显式触发，这样「什么时候会写用户的目录」在代码里始终是可见的。

随包分发的只读资源（目前只有应用图标）由 ``resources_dir`` 定位：它跟着包的
安装位置走，与用户的库无关。

``augment_external_tool_path`` 解决的是另一件事：**从访达 / Dock 启动的进程只拿到
系统那几个目录**，Homebrew 装的 exiftool 因此不在 ``PATH`` 里，元数据读不全，还会
一路降级成只读模式，看起来就像「明明装了 exiftool，Gallery 却说没有」。这里在进程内
把常见的安装位置补回去 —— 打包成 ``.app``（没有 shell 启动器可以补 ``PATH``）之后
同样有效。
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from gallery.core.constants import (
    APP_ICON_BASENAME,
    APP_ICON_SIZES,
    APP_NAME,
    CACHE_DIR_NAME,
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    INDEX_CACHE_DIR_NAME,
    INDEX_FILE_NAME,
    JOURNAL_DIR_NAME,
    PLACES_DB_FILENAME,
    RESOURCES_DIR_NAME,
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
    """写操作日志目录：改名、移到废纸篓、粘贴、移动到相册、新建相册各留一条。"""
    return config_dir(root) / JOURNAL_DIR_NAME


def index_file(root: Path) -> Path:
    """SQLite 索引文件：分组 / 排序用的元数据快照（见 ADR 0009）。"""
    return config_dir(root) / INDEX_FILE_NAME


def external_index_file(root: Path, *, base: Path | None = None) -> Path:
    """库外索引文件：``<app cache>/index/<库路径摘要>.db``。

    用户在开场对话框里选「缓存到系统目录」时用它 —— 那个承诺是**照片文件夹一个字节都
    不写入**，索引当然也得跟着走。
    """
    return app_cache_root(base=base) / INDEX_CACHE_DIR_NAME / f"{library_digest(root)}.db"


def has_config_dir(root: Path) -> bool:
    """库内是否已经存在配置目录。

    界面用它决定要不要先征求用户同意再创建缓存目录。
    """
    return config_dir(root).is_dir()


def library_digest(root: Path) -> str:
    """库路径的稳定短摘要，用于在系统目录下为每个库单独建缓存目录。"""
    resolved = str(Path(root).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def is_within(root: Path, candidate: Path) -> bool:
    """``candidate`` 是否就是 ``root`` 或位于其下（按解析后的绝对路径比较）。

    相册模式允许逐层进入子文件夹，界面因此需要一个「不许走出库」的闸门。
    """
    resolved_root = Path(root).resolve()
    resolved = Path(candidate).resolve()
    return resolved == resolved_root or resolved_root in resolved.parents


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


#: 外部工具（exiftool 等）常见的安装目录。
#:
#: 终端里 ``PATH`` 里有它们，是因为登录 shell 读了用户的配置文件；从访达 / Dock
#: 启动的进程没有这个上下文，只拿到 ``/usr/bin:/bin:/usr/sbin:/sbin``。写死这些
#: 目录是刻意的：进程内没有别的方式知道第三方包管理器把东西装到了哪。
EXTRA_TOOL_DIRS: Final = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    str(Path.home() / ".local" / "bin"),
)


def augment_external_tool_path(dirs: Sequence[str] | None = None) -> list[str]:
    """把常见的外部工具目录补进本进程的 ``PATH``，返回真正补进去的目录。

    只在「原来没有、现在存在」时才补，因此不会重复，也不会把 ``PATH`` 弄乱。
    已经能从 ``PATH`` 找到 exiftool 时（本机终端里的情形）什么也不会做。

    :param dirs: 待补的目录；省略则用 :data:`EXTRA_TOOL_DIRS`。测试用它来指定临时目录。
    """
    candidates = EXTRA_TOOL_DIRS if dirs is None else tuple(dirs)
    current = os.environ.get("PATH", "").split(os.pathsep)
    known = set(current)
    added: list[str] = []
    for directory in candidates:
        expanded = os.path.expanduser(directory)
        if not expanded or expanded in known:
            continue
        if not Path(expanded).is_dir():
            continue
        current.append(expanded)
        known.add(expanded)
        added.append(expanded)
    if added:
        os.environ["PATH"] = os.pathsep.join(current)
    return added


def resources_dir() -> Path:
    """随包分发的只读资源目录（图标等）。

    定位方式是包自身的安装位置，而不是当前工作目录：源码树里、``uv sync`` 装出来的
    虚拟环境里、以及打包出来的 ``Gallery.app`` 里都指向同一份文件。

    **两条路径，缺一不可：** Nuitka 把 ``gallery`` 整个编译成 dist 目录下的一个
    ``gallery.so``，模块的 ``__file__`` 于是指向一个**并不存在**的虚拟路径
    （``<dist>/gallery/core/paths.py``）—— 按它推导会得到一个不存在的目录。因此先看
    ``sys.prefix``：standalone 运行时它就是 dist 目录，数据文件正落在那下面。
    源码树与虚拟环境里 ``sys.prefix`` 下没有这份资源，于是自然落到 ``__file__`` 分支。
    """
    packaged = Path(sys.prefix) / RESOURCES_DIR_NAME
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[1] / RESOURCES_DIR_NAME


def icon_file(edge: int) -> Path:
    """应用图标某一档边长的 PNG。"""
    return resources_dir() / f"{APP_ICON_BASENAME}-{edge}.png"


def places_db_file() -> Path:
    """随包分发的城市库（``places.tsv.gz``），按坐标反查地点用的全部数据。

    与图标走同一个定位方式，因此源码树、虚拟环境、打包出来的 ``Gallery.app``
    读到的都是同一份。详见 ``resources/README.md``。
    """
    return resources_dir() / PLACES_DB_FILENAME


def app_icon_files() -> list[Path]:
    """应用图标的全部档位（从小到大）。

    交给 ``QIcon`` 时它会按需要的边长挑最接近的一档，小尺寸才不会糊。
    """
    return [icon_file(edge) for edge in APP_ICON_SIZES]


def app_icon_master() -> Path:
    """最大边长的那档图标，``.icns`` 由它缩放生成。"""
    return icon_file(max(APP_ICON_SIZES))


__all__ = [
    "EXTRA_TOOL_DIRS",
    "app_cache_root",
    "app_data_base",
    "app_icon_files",
    "app_icon_master",
    "app_state_dir",
    "app_state_file",
    "augment_external_tool_path",
    "cache_dir",
    "config_dir",
    "config_file",
    "external_index_file",
    "external_thumbnail_cache_dir",
    "has_config_dir",
    "icon_file",
    "index_file",
    "is_within",
    "journal_dir",
    "places_db_file",
    "library_digest",
    "resources_dir",
    "thumbnail_cache_dir",
]
