"""``gallery.core.library.session`` 的测试。

会话状态不是数据：损坏、乱填、缺字段都必须静默回退，绝不能因为它打不开软件。

**这里只测应用级的那几样**（最近打开过哪些库、窗口几何、与库无关的偏好）。库级的界面偏好在
``config.toml`` 里，见 ``test_config.py`` —— 拆开的那一刀是 ADR 0038 决策 4。
"""

from __future__ import annotations

import json
from pathlib import Path

from gallery.core import paths
from gallery.core.library.session import (
    MAX_RECENT_ROOTS,
    SESSION_VERSION,
    Preferences,
    SessionState,
    WindowGeometry,
    legacy_ui_state,
    load_session,
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
        "slideshow_seconds": 3,
        "open_last_library": True,
    }


def test_preferences_round_trip(tmp_path: Path) -> None:
    preferences = Preferences(
        animation_speed="off",
        appearance="dark",
        slideshow_seconds=8,
        open_last_library=False,
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
