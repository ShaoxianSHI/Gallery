"""``gallery.core.library.session`` 的测试。

会话状态不是数据：损坏、乱填、缺字段都必须静默回退，绝不能因为它打不开软件。

**这里只测应用级的那几样**（最近打开过哪些库、窗口几何、与库无关的偏好）。库级的界面偏好在
``config.toml`` 里，见 ``test_config.py`` —— 拆开的那一刀是 ADR 0038 决策 4。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.library.session import (
    MAX_RECENT_ROOTS,
    SESSION_VERSION,
    Preferences,
    SessionState,
    WindowGeometry,
    legacy_ui_state,
    load_session,
    normalize_background,
    save_session,
)


def test_defaults_when_missing(tmp_path: Path) -> None:
    state = load_session(base=tmp_path)

    assert state == SessionState()
    assert state.recent_roots == ()
    assert state.window is None


def test_round_trip(tmp_path: Path) -> None:
    state = SessionState(
        recent_roots=(Path("/照片/一"), Path("/照片/二")),
        window=WindowGeometry(x=10, y=20, width=1280, height=800, maximized=True),
    )

    save_session(state, base=tmp_path)

    assert load_session(base=tmp_path) == state
    assert [path.name for path in tmp_path.iterdir()] == ["Gallery"]


def test_corrupt_file_falls_back(tmp_path: Path) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text("{ 这不是 JSON", encoding="utf-8")

    assert load_session(base=tmp_path) == SessionState()


def test_unexpected_shapes_fall_back(tmp_path: Path) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)

    for payload in ["[]", '"文本"', "null", '{"recent_roots": "不是列表", "window": 3}']:
        file.write_text(payload, encoding="utf-8")
        assert load_session(base=tmp_path) == SessionState()


def test_invalid_window_is_dropped_but_roots_kept(tmp_path: Path) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(
        json.dumps(
            {
                "version": 1,
                "recent_roots": ["/照片/一"],
                "window": {"x": 0, "y": 0, "width": 0, "height": 100},
            }
        ),
        encoding="utf-8",
    )

    state = load_session(base=tmp_path)

    assert state.window is None
    assert state.recent_roots == (Path("/照片/一"),)


def test_recent_roots_are_deduped_and_capped(tmp_path: Path) -> None:
    state = SessionState()
    for index in range(MAX_RECENT_ROOTS + 3):
        state = state.with_recent_root(Path(f"/照片/{index}"))

    assert len(state.recent_roots) == MAX_RECENT_ROOTS
    assert state.recent_roots[0] == Path(f"/照片/{MAX_RECENT_ROOTS + 2}")

    again = state.with_recent_root(Path("/照片/5"))
    assert again.recent_roots[0] == Path("/照片/5")
    assert len(again.recent_roots) == MAX_RECENT_ROOTS
    assert again.recent_roots.count(Path("/照片/5")) == 1


def test_forgetting_every_root_leaves_an_empty_list() -> None:
    state = SessionState(recent_roots=(Path("/照片/一"), Path("/照片/二")))

    assert state.without_recent_roots().recent_roots == ()


def test_non_string_roots_are_ignored(tmp_path: Path) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"recent_roots": [1, "", "/照片/一"]}), encoding="utf-8")

    assert load_session(base=tmp_path).recent_roots == (Path("/照片/一"),)


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    base = tmp_path / "不存在的" / "目录"

    save_session(SessionState(recent_roots=(Path("/照片/一"),)), base=base)

    assert paths.app_state_file(base=base).is_file()


def test_the_written_file_holds_only_the_app_level_fields(tmp_path: Path) -> None:
    """**写出去的只有应用级的东西**（ADR 0038 决策 4、ADR 0073）。

    库级的七个字段由 :func:`legacy_ui_state` 读，但**再也不会被写回来** ——
    留着它们就等于同一个值有两处来源，下次读的时候说不清听谁的。
    """
    save_session(SessionState(recent_roots=(Path("/照片/一"),)), base=tmp_path)

    payload = json.loads(paths.app_state_file(base=tmp_path).read_text(encoding="utf-8"))

    assert set(payload) == {"version", "recent_roots", "window", "preferences"}
    assert payload["preferences"] == {
        "animation_speed": "normal",
        "appearance": "system",
        "light_background": "",
        "dark_background": "",
        "slideshow_seconds": 3,
        "open_last_library": True,
        "show_file_extensions": True,
        "external_orders": {},
    }


def test_preferences_round_trip(tmp_path: Path) -> None:
    preferences = Preferences(
        animation_speed="off",
        appearance="dark",
        slideshow_seconds=8,
        open_last_library=False,
        show_file_extensions=False,
    )

    save_session(SessionState(preferences=preferences), base=tmp_path)

    assert load_session(base=tmp_path).preferences == preferences


def test_an_old_session_without_preferences_gets_the_defaults(tmp_path: Path) -> None:
    """加字段不是版本变更：老文件没有这一项，读出来就是默认值。"""
    path = paths.app_state_file(base=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": SESSION_VERSION, "recent_roots": ["/照片/一"]}),
        encoding="utf-8",
    )

    state = load_session(base=tmp_path)

    assert state.preferences == Preferences()
    assert state.recent_roots == (Path("/照片/一"),)


def test_unknown_preference_values_fall_back_one_by_one(tmp_path: Path) -> None:
    """一项坏了只回它自己的默认值，不牵连其余几项（手改过的文件很常见）。"""
    path = paths.app_state_file(base=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "preferences": {
                    "animation_speed": "turbo",
                    "appearance": "深色",
                    "slideshow_seconds": 0,
                    "open_last_library": "no",
                    "show_file_extensions": "yes",
                },
            }
        ),
        encoding="utf-8",
    )

    preferences = load_session(base=tmp_path).preferences

    assert preferences.resolved_animation_speed() == "normal"
    assert preferences.resolved_appearance() == "system"
    assert preferences.resolved_slideshow_seconds() == 3
    assert preferences.open_last_library is True
    assert preferences.show_file_extensions is True


def test_the_external_order_is_stored_per_page(tmp_path: Path) -> None:
    """四个位置各存一份；存进去的是应用 id，界面按它排（ADR 0080 / 0094）。"""
    preferences = (
        Preferences()
        .with_external_order("detail", ("imageglass", "preview"))
        .with_external_order("video", ("iina", "finder"))
        .with_external_order("calendar", ("google_calendar", "apple_calendar"))
    )

    save_session(SessionState(preferences=preferences), base=tmp_path)
    loaded = load_session(base=tmp_path).preferences

    assert loaded.order_for("detail") == ("imageglass", "preview")
    assert loaded.order_for("video") == ("iina", "finder")
    assert loaded.order_for("calendar") == ("google_calendar", "apple_calendar")
    assert loaded.order_for("grid") == ()


def test_an_empty_order_means_the_builtin_one() -> None:
    """「恢复默认顺序」写的是空元组 —— 空元组本身不必进文件。"""
    preferences = Preferences().with_external_order("grid", ("qspace", "finder"))

    restored = preferences.with_external_order("grid", ())

    assert restored.order_for("grid") == ()
    assert restored.external_orders == ()


def test_only_the_four_known_pages_can_hold_an_order() -> None:
    """页面名写错要当场报错，不能静默存成一条永远不会被读到的记录。"""
    with pytest.raises(ValueError):
        Preferences().with_external_order("welcome", ("preview",))


def test_the_two_backgrounds_round_trip_per_slot(tmp_path: Path) -> None:
    """两档各存一份，写出去的是规范化之后的 ``#rrggbb``（ADR 0097）。"""
    preferences = (
        Preferences().with_background("#F5F2EC", dark=False).with_background("#101018", dark=True)
    )

    save_session(SessionState(preferences=preferences), base=tmp_path)
    loaded = load_session(base=tmp_path).preferences

    assert loaded.background_for(dark=False) == "#f5f2ec"
    assert loaded.background_for(dark=True) == "#101018"


def test_a_background_on_the_wrong_side_is_ignored() -> None:
    """浅色那一档只收浅色：正文色不跟着底走，挑反了就是浅字压浅底，而且不会有任何地方报错。"""
    assert Preferences().with_background("#101010", dark=False).background_for(dark=False) is None
    assert Preferences().with_background("#f0f0f0", dark=True).background_for(dark=True) is None
    # 手改过的文件同样只回「没设过」，不是报错
    assert Preferences(light_background="#101010").background_for(dark=False) is None


def test_the_lightness_split_is_the_one_the_dialog_checks_against() -> None:
    """分界是 HSL 的 ``L``：``#808080`` 落在浅色那一侧、``#7f7f7f`` 落在深色那一侧。"""
    assert normalize_background("#808080", dark=False) == "#808080"
    assert normalize_background("#808080", dark=True) is None
    assert normalize_background("#7f7f7f", dark=True) == "#7f7f7f"
    assert normalize_background("#7f7f7f", dark=False) is None


@pytest.mark.parametrize("value", ["", "#fff", "f5f2ec", "#gggggg", "#12345", None, 12])
def test_background_values_that_are_not_colours_fall_back(value: object) -> None:
    assert normalize_background(value, dark=False) is None


def test_a_background_value_tolerates_surrounding_space() -> None:
    assert normalize_background("  #F5F2EC  ", dark=False) == "#f5f2ec"


def test_a_broken_order_is_dropped_per_page(tmp_path: Path) -> None:
    path = paths.app_state_file(base=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "preferences": {
                    "external_orders": {
                        "grid": ["qspace", 3, "finder"],
                        "calendar": ["google_calendar"],
                        "detail": "不是列表",
                        "welcome": ["preview"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    preferences = load_session(base=tmp_path).preferences

    assert preferences.order_for("grid") == ("qspace", "finder")
    assert preferences.order_for("calendar") == ("google_calendar",)
    assert preferences.order_for("detail") == ()
    assert preferences.order_for("welcome") == ()


def test_a_file_from_another_version_is_not_parsed_as_this_one(tmp_path: Path) -> None:
    """**版本号要真的被读** —— 它当初写下来的理由就是「用于迁移或丢弃」。

    过去 ``SESSION_VERSION`` 只写不读，于是一份结构不同的老文件会被照当前结构
    解析出一个半懂不懂的状态：字段名对不上就默默用默认值，看起来像「上次的界面
    设置自己丢了」，而真相是这份文件根本不是这一版写的。
    """
    path = paths.app_state_file(base=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": SESSION_VERSION + 1,
                "recent_roots": [str(tmp_path / "别的版本的库")],
            }
        ),
        encoding="utf-8",
    )

    state = load_session(base=tmp_path)

    assert state.recent_roots == ()
    assert legacy_ui_state(base=tmp_path) is None


# ---- 一次性迁移：旧会话里的库级偏好 --------------------------------------------------


def test_the_library_preferences_are_migrated_out_of_the_old_session(tmp_path: Path) -> None:
    """升级不静默重置排序偏好（ADR 0038 决策 4）。

    老版本把这七项写在 ``state.json`` 里；打开一个库时要能把它们捞出来搬进那个库的
    ``config.toml``，否则用户升级之后会发现「我上次按地点排的，怎么变了」。
    """
    paths.app_state_file(base=tmp_path).parent.mkdir(parents=True)
    paths.app_state_file(base=tmp_path).write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "recent_roots": ["/照片/一"],
                "browse_mode": "calendar",
                "metadata_visible": True,
                "group_facet": "city",
                "calendar_level": "week",
                "sort_facet": "captured",
                "sort_descending": True,
                "thumbnail_edge": 288,
            }
        ),
        encoding="utf-8",
    )

    state = legacy_ui_state(base=tmp_path)

    assert state is not None
    assert state.browse_mode == "calendar"
    assert state.sort_facet == "captured"
    assert state.sort_descending is True
    assert state.thumbnail_edge == 288
    assert state.calendar_level == "week"
    assert state.group_facet == "city"
    assert state.metadata_visible is True


def test_there_is_nothing_to_migrate_without_the_legacy_fields(tmp_path: Path) -> None:
    """新版本写的 ``state.json`` 里没有那七项 —— 于是不迁（省掉一次多余的写盘）。"""
    save_session(SessionState(recent_roots=(Path("/照片/一"),)), base=tmp_path)

    assert legacy_ui_state(base=tmp_path) is None


def test_nothing_to_migrate_when_the_old_file_is_unreadable(tmp_path: Path) -> None:
    paths.app_state_file(base=tmp_path).parent.mkdir(parents=True)
    paths.app_state_file(base=tmp_path).write_text("{ 坏的", encoding="utf-8")

    assert legacy_ui_state(base=tmp_path) is None


def test_nothing_to_migrate_when_every_legacy_value_is_bogus(tmp_path: Path) -> None:
    """全是手改坏的：与其迁一堆默认值过去，不如让界面按自己的默认来。"""
    paths.app_state_file(base=tmp_path).parent.mkdir(parents=True)
    paths.app_state_file(base=tmp_path).write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "browse_mode": "tree",
                "sort_facet": "编出来的字段",
                "thumbnail_edge": "288",
            }
        ),
        encoding="utf-8",
    )

    assert legacy_ui_state(base=tmp_path) is None


def test_migration_keeps_the_ones_that_are_still_valid(tmp_path: Path) -> None:
    """一半坏一半好时，**好的那几个照样迁** —— 不能因为一个字段坏了就全丢。"""
    paths.app_state_file(base=tmp_path).parent.mkdir(parents=True)
    paths.app_state_file(base=tmp_path).write_text(
        json.dumps(
            {
                "version": SESSION_VERSION,
                "browse_mode": "根本没有这个",
                "sort_facet": "captured",
                "thumbnail_edge": 176,
            }
        ),
        encoding="utf-8",
    )

    state = legacy_ui_state(base=tmp_path)

    assert state is not None
    assert state.sort_facet == "captured"
    assert state.thumbnail_edge == 176
    assert state.browse_mode is None
