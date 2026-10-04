"""``gallery.core.library.config`` 的测试。

配置文件的字段是**面向用户的公开接口**（用户会手改它），所以这里重点验证两件事：
非法输入给出可行动的中文提示，以及坏配置永远不会被静默接受。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.constants import DEFAULT_MAX_CACHE_BYTES
from gallery.core.errors import ConfigError
from gallery.core.library.config import (
    ALBUM_COVERS_KEY,
    LibraryConfig,
    load_config,
    prepare_library,
    save_config,
)


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


# ---- 相册封面（ADR 0028）---------------------------------------------------


def test_album_covers_round_trip_through_the_file(tmp_path: Path) -> None:
    """封面那张表写得进去、读得回来，读回来的还是同一份。"""
    original = LibraryConfig(album_covers={"2025-苏州": "2025-苏州/一张.jpg"})

    save_config(tmp_path, original)

    assert load_config(tmp_path) == original
    # 键里有中文、还带一层子目录：都得原样留在文件里（用户是手改这个文件的）。
    assert tomllib.loads(paths.config_file(tmp_path).read_text(encoding="utf-8")) == {
        "recursive": True,
        "thumbnail_max_edge": 512,
        "max_cache_bytes": DEFAULT_MAX_CACHE_BYTES,
        "album_covers": {"2025-苏州": "2025-苏州/一张.jpg"},
    }


def test_a_cover_containing_a_quote_survives_the_round_trip(tmp_path: Path) -> None:
    """文件名里带引号或反斜杠（用户在访达上就能造出来）不能把文件写坏。

    界面上从来不这么写，但这是**用户手改的文件**：与其指望没人这么干，不如写出去的
    那份经得起读回来。
    """
    original = LibraryConfig(album_covers={'空 "引号"': '空 "引号"/a\\b.jpg'})

    save_config(tmp_path, original)

    assert load_config(tmp_path) == original


def test_no_cover_means_no_table_in_the_file(tmp_path: Path) -> None:
    """一张封面都没有时整段不写：刚建出来的库不该因为一节空注释像是有过设置。"""
    save_config(tmp_path, LibraryConfig())

    body = paths.config_file(tmp_path).read_text(encoding="utf-8")

    assert ALBUM_COVERS_KEY not in body
    assert load_config(tmp_path).album_covers == {}


def test_album_covers_are_written_in_a_stable_order(tmp_path: Path) -> None:
    """按键排序写出去：这个文件是要跟着库一起提交进版本控制的，顺序抖动全是假 diff。"""
    save_config(tmp_path, LibraryConfig(album_covers={"b": "b/2.jpg", "a": "a/1.jpg"}))

    body = paths.config_file(tmp_path).read_text(encoding="utf-8")

    assert body.index('"a"') < body.index('"b"')


def test_album_covers_table_is_not_editable_in_place(tmp_path: Path) -> None:
    """拿到的那张表是只读的：配置不可变，别处悄悄改掉它，内存与文件就对不上了。"""
    config = LibraryConfig(album_covers={"a": "a/1.jpg"})

    with pytest.raises(TypeError):
        config.album_covers["b"] = "b/2.jpg"  # type: ignore[index]


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("album_covers = 3\n", ALBUM_COVERS_KEY),
        ('[album_covers]\n"京都" = 3\n', "京都"),
        ('[album_covers]\n"京都" = "/照片.jpg"\n', "相对"),
        ('[album_covers]\n"京都" = "../照片.jpg"\n', "相对"),
        ('[album_covers]\n"/京都" = "京都/照片.jpg"\n', "相对"),
        ('[album_covers]\n"京都" = "别处/照片.jpg"\n', "不在这个相册里"),
        ('[album_covers]\n"" = "京都/照片.jpg"\n', "空路径"),
    ],
)
def test_invalid_album_covers_are_rejected(tmp_path: Path, body: str, needle: str) -> None:
    """封面那两列会被拼成路径去读用户的照片，因此校验比别的字段严。

    一条坏行不该拖垮整个库：那正是每句提示里都带着文件路径的原因。
    """
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert needle in excinfo.value.message
    assert excinfo.value.path == file


def test_a_cover_from_a_sub_album_counts(tmp_path: Path) -> None:
    """子相册里的照片可以当父相册的封面 —— 键只是一段相对路径，深一点没关系。"""
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text('[album_covers]\n"2025" = "2025/苏州/一张.jpg"\n', encoding="utf-8")

    assert load_config(tmp_path).album_covers == {"2025": "2025/苏州/一张.jpg"}


def test_a_cover_equal_to_the_album_itself_is_rejected(tmp_path: Path) -> None:
    """封面不能是相册自己：那是文件夹，不是照片。"""
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text('[album_covers]\n"京都" = "京都"\n', encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert "不在这个相册里" in excinfo.value.message
