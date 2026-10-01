"""``gallery.core.paths`` 的测试：路径推导必须是纯函数且完全由库根决定。"""

from __future__ import annotations

import os
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
    assert paths.cache_dir(root) == config / "cache"
    assert paths.thumbnail_cache_dir(root) == config / "cache" / "thumbs"
    assert paths.journal_dir(root) == config / "journal"
    assert paths.index_file(root) == config / "index.db"


def test_paths_do_not_touch_disk(tmp_path: Path) -> None:
    """推导路径不得创建任何东西——写盘只能由显式调用触发。"""
    root = tmp_path / "库"

    paths.thumbnail_cache_dir(root)
    paths.config_file(root)

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

    assert paths.library_digest(first) == paths.library_digest(first)
    assert paths.library_digest(first) != paths.library_digest(second)
    assert len(paths.library_digest(first)) == 16


def test_app_paths_follow_injected_base(tmp_path: Path) -> None:
    base = tmp_path / "Application Support"

    assert paths.app_state_dir(base=base) == base / "Gallery"
    assert paths.app_state_file(base=base) == base / "Gallery" / "state.json"
    assert paths.app_cache_root(base=base) == base / "Gallery" / "cache"

    cache = paths.external_thumbnail_cache_dir(tmp_path / "库", base=base)
    assert cache.parent == base / "Gallery" / "cache" / "thumbs"


def test_app_data_base_is_platform_specific() -> None:
    base = paths.app_data_base()
    assert base.is_absolute()


def test_is_within(tmp_path: Path) -> None:
    root = tmp_path / "库"
    inside = root / "相册" / "更深"

    assert paths.is_within(root, root) is True
    assert paths.is_within(root, inside) is True
    assert paths.is_within(root, tmp_path / "别处") is False
    # 前缀相同的兄弟目录不算「在其下」
    assert paths.is_within(root, tmp_path / "库2") is False


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
