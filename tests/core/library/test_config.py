"""``gallery.core.library.config`` 的测试。

配置文件的字段是**面向用户的公开接口**（用户会手改它），所以这里重点验证两件事：
非法输入给出可行动的中文提示，以及坏配置永远不会被静默接受。
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.constants import DEFAULT_MAX_CACHE_BYTES, DEFAULT_MOVIE_SITE
from gallery.core.errors import ConfigError
from gallery.core.library.config import (
    ALBUM_COVERS_KEY,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    DEFAULT_CALENDAR_LEVEL,
    DEFAULT_SORT_FACET,
    GROUP_LAYOUT_CARDS,
    GROUP_LAYOUT_SECTIONS,
    SEARCH_SCOPE_LEVEL,
    SEARCH_SCOPE_LIBRARY,
    UI_KEY,
    LibraryConfig,
    LibraryUIState,
    discard_legacy_external_index,
    discard_legacy_thumb_cache,
    load_config,
    normalize_movie_site,
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


# ---- 影片网站前缀（ADR 0063）------------------------------------------------


def test_movie_site_defaults_to_jable(tmp_path: Path) -> None:
    from gallery.core.constants import DEFAULT_MOVIE_SITE

    assert load_config(tmp_path).movie_site == DEFAULT_MOVIE_SITE
    assert DEFAULT_MOVIE_SITE == "https://jable.tv/videos/"


def test_movie_site_round_trips_through_the_file(tmp_path: Path) -> None:
    save_config(tmp_path, LibraryConfig(movie_site="https://example.com/movies/"))

    assert load_config(tmp_path).movie_site == "https://example.com/movies/"


@pytest.mark.parametrize(
    "body",
    [
        'movie_site = ""\n',
        "movie_site = 3\n",
        'movie_site = "jable.tv"\n',
        'movie_site = "https://"\n',
    ],
)
def test_a_bad_movie_site_is_rejected(tmp_path: Path, body: str) -> None:
    file = paths.config_file(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert "movie_site" in excinfo.value.message


def test_normalizing_a_movie_site_only_trims_the_whitespace() -> None:
    """尾斜杠留给拼地址那一步去掉：配置文件要原样读回用户写的（ADR 0063）。"""
    assert normalize_movie_site("  https://jable.tv/videos/  ") == "https://jable.tv/videos/"
    assert normalize_movie_site("https://missav.com/en") == "https://missav.com/en"


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
        "movie_site": DEFAULT_MOVIE_SITE,
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


# ---- 库级界面偏好（``[ui]`` 表，ADR 0038 决策 4）-------------------------------------


def test_the_ui_table_survives_a_round_trip(tmp_path: Path) -> None:
    """那七项**原样回到界面上** —— 排序层级与日历停在哪一层都不能在往返里丢掉。"""
    config = LibraryConfig(
        ui=LibraryUIState(
            browse_mode=BROWSE_MODE_CALENDAR,
            metadata_visible=True,
            group_facet="city",
            sort_facet="captured",
            sort_descending=True,
            calendar_level="week",
            thumbnail_edge=288,
        )
    )

    save_config(tmp_path, config)

    assert load_config(tmp_path).ui == config.ui


def test_the_search_scope_survives_a_round_trip(tmp_path: Path) -> None:
    """检索作用域是**库级偏好**（ADR 0040 决策 6）：重开这个库还该是筛选，而不是变回搜索。

    与条件本身相反 —— 条件不进会话（ADR 0026 决策 10），因为条件是一次性的选择，而「我习惯
    用筛选」是关于这个库的偏好。
    """
    config = LibraryConfig(ui=LibraryUIState(search_scope=SEARCH_SCOPE_LEVEL))

    save_config(tmp_path, config)

    assert load_config(tmp_path).ui.search_scope == SEARCH_SCOPE_LEVEL


def test_an_unknown_search_scope_is_not_recorded() -> None:
    """来路不明的值不写进去 —— ``resolved_search_scope`` 拿不到就回默认那一种。"""
    assert LibraryUIState().with_search_scope("编出来的").search_scope is None
    assert LibraryUIState().with_search_scope(SEARCH_SCOPE_LIBRARY).search_scope == (
        SEARCH_SCOPE_LIBRARY
    )


def test_resolving_the_search_scope_falls_back_to_the_whole_library() -> None:
    """没选过就是整库检索（ADR 0040 决策 1）。

    **别把默认定成筛选**：那是主动去开的那一个。
    """
    assert LibraryUIState().resolved_search_scope() == SEARCH_SCOPE_LIBRARY
    assert LibraryUIState(search_scope="编出来的").resolved_search_scope() == SEARCH_SCOPE_LIBRARY
    assert (
        LibraryUIState(search_scope=SEARCH_SCOPE_LEVEL).resolved_search_scope()
        == SEARCH_SCOPE_LEVEL
    )


def test_the_search_scope_alone_means_the_ui_table_is_not_empty() -> None:
    """只设了作用域也算「设过」—— 少了它，重开这个库会静默变回整库检索。"""
    ui = LibraryUIState(search_scope=SEARCH_SCOPE_LEVEL)

    assert ui.is_empty is False
    assert "search_scope" in ui.to_toml()


def test_the_group_layout_survives_a_round_trip(tmp_path: Path) -> None:
    """分组形态是**库级偏好**（ADR 0089）：重开这个库还该是分区式。"""
    config = LibraryConfig(ui=LibraryUIState(group_layout=GROUP_LAYOUT_SECTIONS))

    save_config(tmp_path, config)

    assert load_config(tmp_path).ui.group_layout == GROUP_LAYOUT_SECTIONS
    assert load_config(tmp_path).ui.resolved_group_layout() == GROUP_LAYOUT_SECTIONS


def test_an_unknown_group_layout_falls_back_to_cards() -> None:
    """来路不明的值不写进去，读回来也回默认的卡片式（老库升级上来看到的就是它）。"""
    assert LibraryUIState().with_group_layout("编出来的").group_layout is None
    assert LibraryUIState().resolved_group_layout() == GROUP_LAYOUT_CARDS
    assert LibraryUIState(group_layout="编出来的").resolved_group_layout() == GROUP_LAYOUT_CARDS
    assert (
        LibraryUIState().with_group_layout(GROUP_LAYOUT_SECTIONS).resolved_group_layout()
        == GROUP_LAYOUT_SECTIONS
    )


def test_a_fresh_library_writes_no_ui_table(tmp_path: Path) -> None:
    """没人设过的东西不该在文件里留下一行 —— 那一行会被读成「设过，只是设成这个」。"""
    save_config(tmp_path, LibraryConfig())

    assert UI_KEY not in paths.config_file(tmp_path).read_text(encoding="utf-8")
    assert load_config(tmp_path).ui.is_empty is True


def test_a_bool_in_the_ui_table_is_written_as_toml_not_as_python(tmp_path: Path) -> None:
    """``bool`` 是 ``int`` 的子类 —— 反过来判就会写出 ``sort_descending = True``。

    那不是 TOML：下一次读这个库会直接报语法错，而用户什么都没做。
    """
    config = LibraryConfig(ui=LibraryUIState(sort_descending=True, metadata_visible=False))
    save_config(tmp_path, config)

    assert "sort_descending = true" in paths.config_file(tmp_path).read_text(encoding="utf-8")
    assert load_config(tmp_path).ui.sort_descending is True


def test_an_unknown_value_in_the_ui_table_means_never_chosen(tmp_path: Path) -> None:
    """``[ui]`` 是软件自己写的表：一个来路不明的字段 id 不该让整个库打不开。"""
    paths.config_file(tmp_path).parent.mkdir(parents=True)
    paths.config_file(tmp_path).write_text(
        '[ui]\nsort_facet = "编出来的字段"\nbrowse_mode = "根本没有这个"\n'
        'calendar_level = "decade"\nthumbnail_edge = 0\nmetadata_visible = "yes"\n'
        'search_scope = "编出来的"\n',
        encoding="utf-8",
    )

    ui = load_config(tmp_path).ui

    assert ui.is_empty is True


def test_a_ui_table_of_the_wrong_type_is_an_empty_table(tmp_path: Path) -> None:
    paths.config_file(tmp_path).parent.mkdir(parents=True)
    paths.config_file(tmp_path).write_text('ui = "不是一张表"\n', encoding="utf-8")

    assert load_config(tmp_path).ui.is_empty is True


def test_the_defaults_are_resolved_not_guessed(tmp_path: Path) -> None:
    """从没选过排序字段：按修改时间排；日历停在「月」。

    默认排序字段是「只看文件本身就能拿到」的那一档：打开库不必等 exiftool。
    """
    ui = LibraryUIState()

    assert ui.resolved_sort_facet() == DEFAULT_SORT_FACET == "modified"
    assert ui.resolved_calendar_level() == DEFAULT_CALENDAR_LEVEL == "month"


def test_resolving_rejects_a_bogus_saved_value() -> None:
    ui = LibraryUIState(sort_facet="编出来的", calendar_level="decade")

    assert ui.resolved_sort_facet() == DEFAULT_SORT_FACET
    assert ui.resolved_calendar_level() == DEFAULT_CALENDAR_LEVEL


def test_the_builders_leave_the_original_alone() -> None:
    """``LibraryUIState`` 是 frozen 的：链式改法要换新对象，
    否则「存回去的必须是改过的那一个」就不成立。"""
    first = LibraryUIState(thumbnail_edge=112, browse_mode=BROWSE_MODE_FLAT)
    second = first.with_thumbnail_edge(288).with_ordering("city", "captured", True)

    assert first.thumbnail_edge == 112
    assert first.sort_facet is None
    assert second.thumbnail_edge == 288
    assert second.sort_facet == "captured"
    assert second.sort_descending is True


def test_an_unknown_calendar_level_is_not_recorded() -> None:
    assert LibraryUIState().with_calendar_level("decade").calendar_level is None
    assert LibraryUIState().with_calendar_level("year").calendar_level == "year"


# ---- 只读卷上的降级与存量清理 -------------------------------------------------------


def test_prepare_library_gives_up_instead_of_raising(tmp_path: Path) -> None:
    """建不出来时返回 ``None``，界面据此退成内存索引并在横幅上说一声（ADR 0038 决策 3）。

    这条路以前是抛 ``ConfigError`` 的 —— 只读卷上打开库直接失败，而用户在那个对话框
    前其实没有第三个有意义的选择。
    """
    blocker = tmp_path / "拦住"
    blocker.write_bytes(b"x")
    root = tmp_path / "库"
    root.mkdir()
    # 在根目录下塞一个同名文件，``mkdir`` 必然失败（不是一个已存在的目录）。
    (root / paths.CONFIG_DIR_NAME).write_bytes(b"x")

    assert prepare_library(root) is None


def test_prepare_library_creates_the_config_dir(tmp_path: Path) -> None:
    file = prepare_library(tmp_path)

    assert file == paths.config_file(tmp_path)
    assert file.is_file()


def test_the_legacy_thumb_cache_is_discarded(tmp_path: Path) -> None:
    """缩略图搬去系统目录之后，``.gallery/cache/`` 整块作废（ADR 0038 决策 6）。

    那正是「同一个库两份缓存」里多出来的那一份。
    """
    legacy = paths.legacy_thumbnail_cache_dir(tmp_path)
    legacy.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg"):
        (legacy / name).write_bytes(b"x")
    prepare_library(tmp_path)
    (paths.config_dir(tmp_path) / "index.db").write_bytes(b"x")

    removed = discard_legacy_thumb_cache(tmp_path)

    assert removed == 2
    assert not legacy.exists()
    # **别的文件一个都不动**：config.toml 与 index.db 不是缓存（journal 更不是，
    # 删掉就丢了 ⌘Z 的撤销能力）。
    assert (paths.config_dir(tmp_path) / "index.db").exists()
    assert paths.config_file(tmp_path).is_file()


def test_discarding_a_legacy_cache_that_is_not_there(tmp_path: Path) -> None:
    assert discard_legacy_thumb_cache(tmp_path) == 0


def test_discarding_leaves_what_it_must_not_delete(tmp_path: Path) -> None:
    """只删**自己写的文件**；目录里还有别的东西就把目录留下，**不许报错**。

    现实里那一类东西是有的：OneDrive 同步过程中会在旁边造出冲突副本与临时目录。
    """
    legacy = paths.legacy_thumbnail_cache_dir(tmp_path)
    legacy.mkdir(parents=True)
    (legacy / "一张缩略图.jpg").write_bytes(b"x")
    (legacy / "一个冲突目录").mkdir()
    (legacy / "一个冲突目录" / "半张").write_bytes(b"x")

    removed = discard_legacy_thumb_cache(tmp_path)

    assert removed == 1
    assert not (legacy / "一张缩略图.jpg").exists()
    assert (legacy / "一个冲突目录" / "半张").exists()


def test_the_legacy_external_index_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库外那份索引整个作废（ADR 0038 决策 6）：「把索引放到系统目录」那条路没了。

    它是**整台机器一份**的，与当前打开的是哪个库无关 —— 所以清理函数不接库根。
    """
    cache = tmp_path / "系统缓存"
    monkeypatch.setattr(paths, "app_cache_root", lambda *, base=None: cache)
    legacy = paths.app_index_cache_dir()
    legacy.mkdir(parents=True)
    (legacy / "某个库的摘要.db").write_bytes(b"x")
    # 目录里别的东西**不许动**：将来 app cache 底下多出什么，这条清理不能顺手删掉。
    (legacy / "不相干的东西.txt").write_bytes(b"x")

    removed = discard_legacy_external_index()

    assert removed == 1
    assert not (legacy / "某个库的摘要.db").exists()
    assert (legacy / "不相干的东西.txt").exists()


def test_discarding_a_legacy_external_index_that_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "app_cache_root", lambda *, base=None: tmp_path / "空的")

    assert discard_legacy_external_index() == 0


def test_the_empty_legacy_cache_parent_is_removed_too(tmp_path: Path) -> None:
    """``thumbs`` 收掉之后，只装过它的那个 ``cache/`` 空壳也要收掉。

    「整块作废」才叫整块：留一个空目录在用户照片文件夹里，下一次看到它的人只会
    以为还有东西没清干净。**判据是它空不空**：``rmdir`` 对非空目录会失败，
    所以里面还有别人的东西时它自然留在原地。
    """
    prepare_library(tmp_path)
    legacy = paths.legacy_thumbnail_cache_dir(tmp_path)
    legacy.mkdir(parents=True)
    (legacy / "一张.jpg").write_bytes(b"x")

    assert discard_legacy_thumb_cache(tmp_path) == 1
    assert not paths.legacy_cache_dir(tmp_path).exists()


def test_a_legacy_cache_holding_something_else_is_left_alone(tmp_path: Path) -> None:
    """父目录里还有别的东西时**不许**动它 —— 那是别人的东西，不是 Gallery 写的。"""
    legacy = paths.legacy_thumbnail_cache_dir(tmp_path)
    legacy.mkdir(parents=True)
    (legacy / "一张.jpg").write_bytes(b"x")
    (paths.legacy_cache_dir(tmp_path) / "不相干的.txt").write_bytes(b"x")

    discard_legacy_thumb_cache(tmp_path)

    assert not legacy.exists()
    assert (paths.legacy_cache_dir(tmp_path) / "不相干的.txt").exists()


def test_the_empty_shell_is_removed_even_after_the_thumbs_are_gone(tmp_path: Path) -> None:
    """``thumbs`` 早就没了、只剩一个空壳 ``cache/`` 时，**也要把它收掉**。

    清理函数不能写成「``thumbs`` 不存在就 return 0」：**第一次**打开库把 thumbs 删掉、
    第二次打开时那个空壳就再也没人管，于是报出来的是「清理了 0 个文件」而目录还在。
    """
    prepare_library(tmp_path)
    paths.legacy_cache_dir(tmp_path).mkdir(parents=True)  # 只有空壳，没有 thumbs

    assert discard_legacy_thumb_cache(tmp_path) == 0
    assert not paths.legacy_cache_dir(tmp_path).exists()


def test_the_ui_field_table_matches_the_dataclass() -> None:
    """**字段表与 :class:`LibraryUIState` 一一对应** —— 少一个、多一个、拼错一个都不行。

    这张表是新加偏好的唯一落点：属性名拼错时 ``getattr`` / ``cls(**…)`` 会在**运行时**
    抛，但那时库已经打开了；而漏掉一项则更安静 —— 那个偏好存不下去也读不回来，
    界面上只表现为「换了库这个设置就丢了」。
    """
    from gallery.core.library import config as module

    listed = [attribute for _, attribute, _ in module._UI_FIELDS]
    declared = [field.name for field in dataclasses.fields(module.LibraryUIState)]

    assert sorted(listed) == sorted(declared), (
        f"字段表与 dataclass 对不上，多/少的是 {sorted(set(listed) ^ set(declared))}"
    )
    assert len(listed) == len(set(listed)), f"字段表里有重复的属性名：{listed}"


def test_the_ui_field_table_preserves_the_written_order() -> None:
    """``[ui]`` 写出去的顺序就是字段表的顺序 —— 每次保存的 diff 才没有噪音。

    锁定顺序是有意的：把字段表排序一下会让所有老库的 ``config.toml`` 在下次保存时
    整段重排，diff 里全是噪音。
    """
    from gallery.core.library import config as module

    # 九个字段全给上值，写出去的行才与字段表一样长。
    state = module.LibraryUIState(
        browse_mode="albums",
        metadata_visible=True,
        group_facet="camera",
        sort_facet="captured",
        sort_descending=False,
        calendar_level="month",
        thumbnail_edge=512,
        search_scope="level",
        group_layout="sections",
    )
    body = state.to_toml().split(f"[{module.UI_KEY}]", 1)[1]

    written = [line.split("=")[0].strip() for line in body.splitlines() if "=" in line]
    assert written == [key for key, _, _ in module._UI_FIELDS]
