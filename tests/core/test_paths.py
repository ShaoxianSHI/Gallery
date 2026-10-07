"""``gallery.core.paths`` 的测试：路径推导必须是纯函数且完全由库根决定。"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.constants import (
    APP_ICON_BASENAME,
    APP_ICON_SIZES,
    CONFIG_DIR_NAME,
    RESOURCES_DIR_NAME,
)


def test_paths_live_under_config_dir(tmp_path: Path) -> None:
    root = tmp_path / "库"
    config = root / CONFIG_DIR_NAME

    assert paths.config_dir(root) == config
    assert paths.config_file(root) == config / "config.toml"
    assert paths.journal_dir(root) == config / "journal"
    assert paths.index_file(root) == config / "index.db"
    # 事件库也在配置目录里，但它**不是缓存**（ADR 0043）—— 与索引只差一个文件名，
    # 而「删掉重扫就能恢复」那句话对这两个文件一个字都不成立。
    assert paths.events_file(root) == config / "events.db"
    # 缩略图**不在**这个目录树里（ADR 0038 决策 1），但搬走之前留在库里的那块
    # 仍要认得出来，迁移时靠它定位。
    assert paths.legacy_cache_dir(root) == config / "cache"
    assert paths.legacy_thumbnail_cache_dir(root) == config / "cache" / "thumbs"
    assert tmp_path not in paths.thumbnail_cache_dir(root).parents


def test_paths_do_not_touch_disk(tmp_path: Path) -> None:
    """推导路径不得创建任何东西——写盘只能由显式调用触发。"""
    root = tmp_path / "库"

    paths.thumbnail_cache_dir(root)
    paths.config_file(root)
    paths.legacy_thumbnail_cache_dir(root)

    assert not root.exists()


def test_has_config_dir(tmp_path: Path) -> None:
    root = tmp_path / "库"
    root.mkdir()

    assert paths.has_config_dir(root) is False
    paths.config_dir(root).mkdir()
    assert paths.has_config_dir(root) is True


def test_library_digest_is_stable_and_distinct(tmp_path: Path) -> None:
    first = tmp_path / "一"
    second = tmp_path / "二"

    assert paths.library_digest(first) != paths.library_digest(second)
    assert len(paths.library_digest(first)) == 16


def test_app_paths_follow_injected_base(tmp_path: Path) -> None:
    base = tmp_path / "Application Support"

    assert paths.app_state_dir(base=base) == base / "Gallery"
    assert paths.app_state_file(base=base) == base / "Gallery" / "state.json"
    assert paths.app_cache_root(base=base) == base / "Gallery" / "cache"

    cache = paths.thumbnail_cache_dir(tmp_path / "库", base=base)
    assert cache.parent == base / "Gallery" / "cache" / "thumbs"
    assert cache.name == paths.library_digest(tmp_path / "库")


def test_app_data_base_is_platform_specific(
    monkeypatch: pytest.MonkeyPatch, real_app_data_base: Callable[[], Path]
) -> None:
    """各平台落在各平台该在的地方（配置目录名的来处）。

    **用没被隔离的那一份**：其余用例都跑在 ``isolated_app_data`` 之下（那份 fixture
    把默认值钉到临时目录上，好让测试永远不碰用户真实的缓存），而这一条测的正是
    **真实的默认值**，隔离了就没得测。
    """
    if sys.platform == "darwin":
        assert real_app_data_base() == Path.home() / "Library" / "Application Support"
    else:
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
        assert real_app_data_base() == Path("/tmp/xdg")


def test_is_within(tmp_path: Path) -> None:
    root = tmp_path / "库"
    inside = root / "相册" / "更深"

    assert paths.is_within(root, root) is True
    assert paths.is_within(root, inside) is True
    assert paths.is_within(root, tmp_path / "别处") is False
    # 前缀相同的兄弟目录不算「在其下」
    assert paths.is_within(root, tmp_path / "库2") is False


def test_the_guard_really_refuses_the_library_root(tmp_path: Path) -> None:
    """``allow_root=False`` 是**禁止**，不是一个什么都不检查的静默返回。

    这一支写成 ``return`` 就是文档与代码打架：这是一个「确认」用的闸门，别的调用点
    未必都在前面自己查过一遍库根，读代码的人只能靠猜那道闸到底拦不拦。
    """
    root = tmp_path / "库"
    root.mkdir()

    paths.guard_within_root(root, root, allow_root=True)  # 粘贴的目标可以是库根

    with pytest.raises(paths.GuardError) as caught:
        paths.guard_within_root(root, root, allow_root=False)

    assert caught.value.reason is paths.GuardReason.IS_ROOT


def test_the_guard_refuses_the_config_dir(tmp_path: Path) -> None:
    """配置目录永远不在允许范围内 —— 里面放的每一样都是「只有 Gallery 自己看得见」的缓存。"""
    root = tmp_path / "库"
    root.mkdir()
    config = paths.config_dir(root)
    config.mkdir()

    with pytest.raises(paths.GuardError) as caught:
        paths.guard_within_root(root, config)

    assert caught.value.reason is paths.GuardReason.IS_CONFIG_DIR


def test_the_guard_rejects_a_path_outside_the_library(tmp_path: Path) -> None:
    root = tmp_path / "库"
    root.mkdir()
    outside = tmp_path / "别处"
    outside.mkdir()

    with pytest.raises(paths.GuardError) as caught:
        paths.guard_within_root(root, outside)

    assert caught.value.reason is paths.GuardReason.OUTSIDE_ROOT


def test_the_reason_is_the_interface_not_the_message(tmp_path: Path) -> None:
    """**改消息文本不该改变原因**（也不要动 ``ValueError`` 这条兼容）。

    五个写入口按 :attr:`GuardError.reason` 分支，而不是拿消息文本去认。原先靠
    ``"配置目录" in str(exc)`` 那套，改一个字的文案就会让某一支静默失效、报错说的话
    与真正发生的事对不上 —— 而这是红线 1 的入口。

    判据里连 ``str`` 一起量：消息是为了让人在日志里看懂，它**不是**接口。
    """
    root = tmp_path / "库"
    root.mkdir()
    outside = tmp_path / "别处"
    outside.mkdir()

    with pytest.raises(ValueError) as caught:  # 仍然兼容「拒绝一个路径」= 值校验失败
        paths.guard_within_root(root, outside)

    error = caught.value
    assert isinstance(error, paths.GuardError)
    assert error.reason is paths.GuardReason.OUTSIDE_ROOT
    assert str(outside) in str(error), "消息里该带上那个路径，便于从日志里看出来"


def test_the_hot_paths_do_not_resolve_the_same_string_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这道闸走带记忆的 ``resolved_str``** —— 它在粘贴 / 移动 / 改名的逐项循环里被问，
    而 ``root`` 在整场会话里不变。裸 ``resolve()`` 每次都要 ``lstat`` 路径上的每一段
    （实测十二段的库一次约 19 µs，带记忆那份约 0.08 µs）。

    钉的是「问第二次不再解析」：第一次必然要真算一次（缓存未命中），而那一笔之后
    就该全是查表。测试里量时间本来就不稳，钉这个更实。
    """
    root = tmp_path / "库"
    (root / "相册").mkdir(parents=True)
    inside = root / "相册" / "一张.jpg"
    # 先把这一批填进缓存（``config_dir`` 也算一个：闸门每次都要拿它比一比）。
    paths.is_within(root, inside)
    paths.is_within(root, paths.config_dir(root))
    paths.is_within(root, root / "相册")

    calls: list[str] = []
    original = Path.resolve

    def counting(self: Path, *args: object, **kwargs: object) -> Path:
        calls.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting)
    paths.is_within(root, inside)
    paths.guard_within_root(root, inside)
    paths.is_within(root, root / "相册")

    assert calls == [], f"同一批路径被重复解析了 {len(calls)} 次"


def test_resources_dir_follows_the_package() -> None:
    """资源跟着包走，而不是当前工作目录：从别处启动也找得到。"""
    resources = paths.resources_dir()

    assert resources.is_absolute()
    assert resources.name == RESOURCES_DIR_NAME
    assert resources.parent.name == "gallery"


def test_app_icon_files_are_committed_and_ordered() -> None:
    """图标各档必须真的在仓库里，否则窗口图标会静默变成空白。"""
    files = paths.app_icon_files()

    assert [path.name for path in files] == [
        f"{APP_ICON_BASENAME}-{edge}.png" for edge in APP_ICON_SIZES
    ]
    assert all(path.is_file() for path in files)


def test_app_icon_master_is_the_largest() -> None:
    master = paths.app_icon_master()

    assert master in paths.app_icon_files()
    assert master.name.endswith(f"-{max(APP_ICON_SIZES)}.png")


def test_resources_dir_prefers_sys_prefix_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打包之后资源在 ``sys.prefix`` 下，必须优先于 ``__file__`` 推导。

    Nuitka 把 ``gallery`` 编译成 dist 目录里的单个 ``gallery.so``，``__file__``
    指向一条并不存在的虚拟路径；照它推导会得到一个查不到图标的目录，窗口图标
    于是静默变空白。
    """
    packaged = tmp_path / "prefix" / RESOURCES_DIR_NAME
    packaged.mkdir(parents=True)
    monkeypatch.setattr(paths.sys, "prefix", str(tmp_path / "prefix"))

    assert paths.resources_dir() == packaged


def test_resources_dir_falls_back_to_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """``sys.prefix`` 下没有这份资源时（源码树 / 虚拟环境），回到包自身的位置。"""
    monkeypatch.setattr(paths.sys, "prefix", str(Path("/nonexistent-prefix")))

    assert paths.resources_dir().parent.name == "gallery"
    assert paths.resources_dir().name == RESOURCES_DIR_NAME


def test_augment_external_tool_path_adds_missing_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """从访达 / Dock 启动时 PATH 里没有 Homebrew，exiftool 因此找不到。

    这是打包之后必须成立的前提：``.app`` 没有 shell 启动器可以补 PATH。
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("HOME", raising=False)

    added = paths.augment_external_tool_path([str(first), str(second)])

    assert added == [str(first), str(second)]
    assert os.environ["PATH"] == os.pathsep.join(["/usr/bin:/bin", str(first), str(second)])


def test_augment_external_tool_path_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已经能找到 exiftool 的场合（终端里）不该改动 PATH。"""
    existing = tmp_path / "bin"
    existing.mkdir()
    original = os.pathsep.join(["/usr/bin:/bin", str(existing)])
    monkeypatch.setenv("PATH", original)

    added = paths.augment_external_tool_path([str(existing), str(tmp_path / "missing")])

    assert added == []
    assert os.environ["PATH"] == original


def test_augment_external_tool_path_expands_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~`` 要展开：``EXTRA_TOOL_DIRS`` 里就带着 ``~/.local/bin``。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    (tmp_path / ".local" / "bin").mkdir(parents=True)

    added = paths.augment_external_tool_path([str(Path("~/.local/bin"))])

    assert added == [str(tmp_path / ".local" / "bin")]
