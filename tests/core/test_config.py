"""``gallery.core.config`` 的测试。

配置文件的字段是**面向用户的公开接口**（用户会手改它），所以这里重点验证两件事：
非法输入给出可行动的中文提示，以及坏配置永远不会被静默接受。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.config import LibraryConfig, load_config, prepare_library, save_config
from gallery.core.errors import ConfigError


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config == LibraryConfig()
    assert config.recursive is True
    assert config.thumbnail_max_edge == 512


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    original = LibraryConfig(recursive=False, thumbnail_max_edge=256, max_cache_bytes=1024)

    save_config(tmp_path, original)

    assert load_config(tmp_path) == original
    # 写出来的必须是合法 TOML，用户手改后还能读回来。
    tomllib.loads(paths.config_file(tmp_path).read_text(encoding="utf-8"))


def test_partial_file_falls_back_per_field(tmp_path: Path) -> None:
    paths.config_file(tmp_path).parent.mkdir(parents=True)
    paths.config_file(tmp_path).write_text("thumbnail_max_edge = 128\n", encoding="utf-8")

    config = load_config(tmp_path)

    assert config.thumbnail_max_edge == 128
    assert config.recursive is True


def test_broken_toml_reports_path(tmp_path: Path) -> None:
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text("recursive = = true\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert str(file) in excinfo.value.message
    assert "语法" in excinfo.value.message
    assert excinfo.value.path == file


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ('recursive = "yes"\n', "recursive"),
        ("thumbnail_max_edge = 10\n", "thumbnail_max_edge"),
        ("thumbnail_max_edge = 99999\n", "thumbnail_max_edge"),
        ("max_cache_bytes = -1\n", "max_cache_bytes"),
        ("max_cache_bytes = true\n", "max_cache_bytes"),
        ("thumbnail_max_edge = 512.5\n", "thumbnail_max_edge"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, body: str, needle: str) -> None:
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert needle in excinfo.value.message


def test_zero_cache_limit_means_unlimited(tmp_path: Path) -> None:
    paths.config_file(tmp_path).parent.mkdir(parents=True)
    paths.config_file(tmp_path).write_text("max_cache_bytes = 0\n", encoding="utf-8")

    assert load_config(tmp_path).max_cache_bytes == 0


def test_prepare_library_creates_config(tmp_path: Path) -> None:
    root = tmp_path / "库"

    file = prepare_library(root)

    assert file == paths.config_file(root)
    assert file.is_file()
    assert load_config(root) == LibraryConfig()


def test_prepare_library_keeps_existing_config(tmp_path: Path) -> None:
    root = tmp_path / "库"
    prepare_library(root, config=LibraryConfig(thumbnail_max_edge=128))

    prepare_library(root)

    assert load_config(root).thumbnail_max_edge == 128
