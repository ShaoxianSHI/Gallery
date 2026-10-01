"""``gallery.core.paths`` 的测试：路径推导必须是纯函数且完全由库根决定。"""

from __future__ import annotations

from pathlib import Path

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
