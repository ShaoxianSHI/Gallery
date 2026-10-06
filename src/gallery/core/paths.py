"""由库根目录推导配置目录下的各个路径，以及应用级状态目录。

这里的函数都是**纯函数**：不创建目录、不读取文件、不检查存在性
（唯一的例外是 ``has_config_dir``，它只回答一个是非问题）。
落盘由调用方显式触发，这样「什么时候会写用户的目录」在代码里始终是可见的。

**东西放在哪里（ADR 0038）**：除缩略图之外，Gallery 关于一个库的一切都在
库根下的配置目录里 —— 配置、索引、事件、写操作日志、库级界面偏好。缩略图是唯一的例外，
它按库路径摘要落在 ``<app cache>/thumbs/<摘要>/``：那是一万个只增不减的小文件，
放在用户的照片文件夹里会被云同步来回传，而它本来就是随时能重新解码出来的东西。

配置目录里**只有事件库（``events.db``）不是缓存**（ADR 0043）：事件是用户自己填的，
重扫照片重建不出来，所以索引重建与清缓存都不许碰它。

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
from functools import lru_cache
from pathlib import Path
from typing import Final

from gallery.core.constants import (
    APP_ICON_BASENAME,
    APP_ICON_SIZES,
    APP_NAME,
    CACHE_DIR_NAME,
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    EVENTS_FILE_NAME,
    INDEX_FILE_NAME,
    JOURNAL_DIR_NAME,
    LEGACY_INDEX_CACHE_DIR_NAME,
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


def index_file(root: Path) -> Path:
    """SQLite 索引文件：分组 / 排序用的元数据快照（见 ADR 0009、ADR 0038）。"""
    return config_dir(root) / INDEX_FILE_NAME


def events_file(root: Path) -> Path:
    """事件库文件：事件定义与照片↔事件的绑定（ADR 0043）。

    **它不是缓存** —— 事件是用户自己填的，重扫重建不出来，所以索引重建与清缓存都不碰它。
    """
    return config_dir(root) / EVENTS_FILE_NAME


def journal_dir(root: Path) -> Path:
    """写操作日志目录：改名、移到废纸篓、粘贴、移动到相册、新建相册各留一条。"""
    return config_dir(root) / JOURNAL_DIR_NAME


def legacy_cache_dir(root: Path) -> Path:
    """**旧版**的库内缓存目录（配置目录下的 ``cache/``）—— 缩略图曾经住在这里面。

    新版一个字节都不往这里写（ADR 0038 决策 1），这个函数只用来一次性清掉它。
    """
    return config_dir(root) / CACHE_DIR_NAME


def legacy_thumbnail_cache_dir(root: Path) -> Path:
    """**旧版**的库内缩略图缓存目录 —— ADR 0038 之前缩略图住在这里。

    新版不再往这里写任何东西，这个函数只用来一次性清掉它（迁移，见
    :func:`gallery.core.library.config.discard_legacy_thumb_cache`）。
    """
    return legacy_cache_dir(root) / THUMBNAIL_DIR_NAME


def has_config_dir(root: Path) -> bool:
    """库内是否已经存在配置目录。"""
    return config_dir(root).is_dir()


def library_digest(root: Path) -> str:
    """库路径的稳定短摘要，用于在系统目录下为每个库单独建缩略图目录。"""
    resolved = str(Path(root).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def is_within(root: Path, candidate: Path) -> bool:
    """``candidate`` 是否就是 ``root`` 或位于其下（按解析后的绝对路径比较）。

    相册模式允许逐层进入子文件夹，界面因此需要一个「不许走出库」的闸门。

    **走 :func:`resolved_str` 而不是裸的 ``resolve()``**：这条闸门在粘贴 / 移动 / 改名的
    逐项循环里被问，而 ``root`` 在整场会话里不变；裸 ``resolve()`` 要 ``lstat`` 路径上的
    每一段（实测十二段的库一次约 19 µs，而带记忆那份约 0.08 µs）。
    """
    resolved_root = Path(resolved_str(root))
    resolved = Path(resolved_str(candidate))
    return resolved == resolved_root or resolved_root in resolved.parents


def guard_within_root(root: Path, path: Path, *, allow_root: bool = True) -> None:
    """确认 ``path`` 在库内、不是配置目录、也不是库根本身。

    :param allow_root: ``True`` 时允许 ``path == root``（粘贴的目标可以是库根）；
        ``False`` 时**禁止** —— 库根本身不能被改名或删除。
    :raises ValueError: 不满足上面任何一条。

    **别再把「库根」写成静默返回。** 这条闸门是「确认」用的，而 ``return`` 什么都不
    确认 —— 文档写着「禁止」、代码却放行，读的人只能靠猜。
    """
    root = Path(root)
    path = Path(path)
    if not is_within(root, path):
        raise ValueError(f"不在库内：{path}")
    if not allow_root and resolved_str(path) == resolved_str(root):
        raise ValueError(f"这是库本身：{root}")
    if resolved_str(path) == resolved_str(config_dir(root)):
        raise ValueError("这是 Gallery 自己的配置目录")


#: :func:`resolved` 记多少条。够一个中型库一整轮，且不因为翻的库多而一直涨。
RESOLVED_CACHE_SIZE = 8192


@lru_cache(maxsize=RESOLVED_CACHE_SIZE)
def _resolved_of(raw: str) -> str:
    return str(Path(raw).expanduser().resolve())


def resolved_str(path: Path | str) -> str:
    """:func:`resolved` 的字符串版，**省掉一次 ``Path`` 对象的往返**。

    要字符串的地方（拼缓存键、算相对路径）直接用它：``str(resolved(p))`` 会先把查表
    得到的那个字符串重新构造成一个 ``Path``，再转回字符串 —— 那次构造是这条路上一半
    的开销，而结果原封不动。
    """
    return _resolved_of(str(path))


def resolved(path: Path | str) -> Path:
    """:meth:`Path.resolve` 的**带记忆**版本，语义完全一样。

    为什么值得多这一层：``resolve()`` 要 ``lstat`` 路径上的每一段（一个五层的库就是
    五次系统调用，约 12 µs），而本项目里问的都是**同一个问题反复问**——

    - 缓存键每取一张缩略图就要算一次（屏幕上每一格、日历上每一格，见
      :mod:`gallery.core.media.thumbs`）；
    - 监听器每收到一个文件事件就判一次可见性，而库根在整场会话里不变
      （见 :func:`gallery.core.library.scanner.is_visible_path`）；
    - 每次弹出右键菜单都要给每个相册算一次相对路径（见
      :meth:`gallery.core.library.facade.Library.album_label`）。

    这些位置上「重新解析一遍」的答案与上一次逐字相同，于是付的是纯浪费。记一份之后
    每个不同的路径只算一次，之后都是查表。

    **上限是有的**（:data:`RESOLVED_CACHE_SIZE`），所以长期开着也不会一直涨。

    注意它缓存的是**答案**，不是「文件还在不在」——符号链接或路径本身变了要重新起进程，
    那本来也不是这个函数该管的事（真正要跟着文件变的判断都带着 ``mtime_ns`` /
    ``size`` 一起做）。
    """
    return Path(_resolved_of(str(path)))


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
    """会话状态文件。**只装应用级的东西**（最近打开过哪些库、窗口几何）——
    库级的界面偏好在 ``config.toml`` 里（ADR 0038）。"""
    return app_state_dir(base=base) / "state.json"


def app_cache_root(*, base: Path | None = None) -> Path:
    """应用级缓存根目录。**缩略图是这里唯一的住户**（ADR 0038）。"""
    return app_state_dir(base=base) / CACHE_DIR_NAME


def app_index_cache_dir(*, base: Path | None = None) -> Path:
    """**旧版**的库外索引目录：``<app cache>/index``。

    新版不再往这里写任何东西（索引固定在库里的配置目录下，ADR 0038 决策 1），
    这个函数只用来一次性清掉它。
    """
    return app_cache_root(base=base) / LEGACY_INDEX_CACHE_DIR_NAME


def thumbnail_cache_dir(root: Path, *, base: Path | None = None) -> Path:
    """本库的缩略图缓存目录：``<app cache>/thumbs/<库路径摘要>``。

    这是 Gallery **唯一**不进 配置目录 的东西：按实测，一万张照片的缩略图是
    九万多个 38 KiB 的小文件，而索引无论多大都只是一个文件（ADR 0038）。放在系统
    目录里，用户把库放进 Dropbox / OneDrive 时它们不会跟着来回传。

    :param base: 系统级缓存根目录，测试用；``None`` 表示走平台默认位置。
    """
    return app_cache_root(base=base) / THUMBNAIL_DIR_NAME / library_digest(root)


#: 外部工具（exiftool 等）常见的安装目录。终端里 ``PATH`` 里有它们是因为登录 shell 读了用户的
#: 配置文件，从访达 / Dock 启动的进程没有这个上下文。写死是刻意的：进程内没有别的方式知道
#: 第三方包管理器把东西装到了哪。
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
    "RESOLVED_CACHE_SIZE",
    "app_cache_root",
    "app_data_base",
    "app_icon_files",
    "app_icon_master",
    "app_index_cache_dir",
    "app_state_dir",
    "app_state_file",
    "augment_external_tool_path",
    "config_dir",
    "config_file",
    "guard_within_root",
    "has_config_dir",
    "icon_file",
    "index_file",
    "is_within",
    "journal_dir",
    "legacy_cache_dir",
    "legacy_thumbnail_cache_dir",
    "library_digest",
    "places_db_file",
    "resolved",
    "resolved_str",
    "resources_dir",
    "thumbnail_cache_dir",
]
