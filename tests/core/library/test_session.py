"""``gallery.core.library.session`` 的测试。

会话状态不是数据：损坏、乱填、缺字段都必须静默回退，绝不能因为它打不开软件。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.library.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    DEFAULT_SORT_FACET,
    MAX_RECENT_ROOTS,
    SORT_FACET_NONE,
    SessionState,
    WindowGeometry,
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


def test_non_string_roots_are_ignored(tmp_path: Path) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"recent_roots": [1, "", "/照片/一"]}), encoding="utf-8")

    assert load_session(base=tmp_path).recent_roots == (Path("/照片/一"),)


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    base = tmp_path / "不存在的" / "目录"

    save_session(SessionState(recent_roots=(Path("/照片/一"),)), base=base)

    assert paths.app_state_file(base=base).is_file()


def test_thumbnail_edge_round_trip(tmp_path: Path) -> None:
    state = SessionState(thumbnail_edge=288)

    save_session(state, base=tmp_path)

    assert load_session(base=tmp_path).thumbnail_edge == 288


def test_thumbnail_edge_defaults_to_none(tmp_path: Path) -> None:
    assert load_session(base=tmp_path).thumbnail_edge is None
    assert SessionState().thumbnail_edge is None


@pytest.mark.parametrize("bad", [0, -176, "288", 1.5, True, None, [288]])
def test_invalid_thumbnail_edge_falls_back(tmp_path: Path, bad: object) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"thumbnail_edge": bad}), encoding="utf-8")

    assert load_session(base=tmp_path).thumbnail_edge is None


def test_with_thumbnail_edge_keeps_other_fields(tmp_path: Path) -> None:
    state = SessionState(recent_roots=(Path("/照片/一"),))

    updated = state.with_thumbnail_edge(112)

    assert updated.thumbnail_edge == 112
    assert updated.recent_roots == (Path("/照片/一"),)
    assert state.thumbnail_edge is None  # 原对象不可变


def test_browse_mode_round_trip(tmp_path: Path) -> None:
    state = SessionState(browse_mode=BROWSE_MODE_ALBUMS)

    save_session(state, base=tmp_path)

    assert load_session(base=tmp_path).browse_mode == BROWSE_MODE_ALBUMS


def test_browse_mode_defaults_to_none(tmp_path: Path) -> None:
    assert load_session(base=tmp_path).browse_mode is None
    assert SessionState().browse_mode is None


@pytest.mark.parametrize("bad", ["tree", "", "FLAT", 3, None, ["albums"], True])
def test_invalid_browse_mode_falls_back(tmp_path: Path, bad: object) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"browse_mode": bad}), encoding="utf-8")

    assert load_session(base=tmp_path).browse_mode is None


def test_with_browse_mode_keeps_other_fields() -> None:
    state = SessionState(thumbnail_edge=288)

    updated = state.with_browse_mode(BROWSE_MODE_FLAT)

    assert updated.browse_mode == BROWSE_MODE_FLAT
    assert updated.thumbnail_edge == 288
    assert state.browse_mode is None


def test_metadata_visible_round_trip(tmp_path: Path) -> None:
    save_session(SessionState(metadata_visible=True), base=tmp_path)

    assert load_session(base=tmp_path).metadata_visible is True


def test_metadata_visible_defaults_to_none(tmp_path: Path) -> None:
    assert load_session(base=tmp_path).metadata_visible is None
    assert SessionState().metadata_visible is None


@pytest.mark.parametrize("bad", ["true", 1, 0, None, [], {}])
def test_invalid_metadata_visible_falls_back(tmp_path: Path, bad: object) -> None:
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"metadata_visible": bad}), encoding="utf-8")

    assert load_session(base=tmp_path).metadata_visible is None


def test_with_metadata_visible_keeps_other_fields() -> None:
    state = SessionState(browse_mode=BROWSE_MODE_ALBUMS)

    updated = state.with_metadata_visible(True)

    assert updated.metadata_visible is True
    assert updated.browse_mode == BROWSE_MODE_ALBUMS
    assert state.metadata_visible is None


# ---- 排序字段与它的默认值 ------------------------------------------------------------


def test_no_sort_facet_means_the_default_one() -> None:
    """从没选过排序字段：按拍摄时间排（``DEFAULT_SORT_FACET``）。"""
    assert SessionState().resolved_sort_facet() == DEFAULT_SORT_FACET == "captured"


@pytest.mark.parametrize("bad", ["没有这个字段", "keyword", "", 3, None, ["captured"]])
def test_never_chosen_and_bogus_ids_fall_back_to_the_default(tmp_path: Path, bad: object) -> None:
    """老的会话文件里那一项是 ``null``（那时表示「不排序」）—— 一律当「没选过」。

    分不清「没选过」与「选了不排序」的场合一律偏向默认值：新排序对所有人都生效，
    真想不排序的人重新点一次即可（那次会记成 ``SORT_FACET_NONE``）。
    """
    file = paths.app_state_file(base=tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"sort_facet": bad}), encoding="utf-8")

    assert load_session(base=tmp_path).resolved_sort_facet() == DEFAULT_SORT_FACET


def test_the_chosen_sort_facet_is_remembered(tmp_path: Path) -> None:
    save_session(SessionState(sort_facet="iso"), base=tmp_path)

    assert load_session(base=tmp_path).resolved_sort_facet() == "iso"


def test_choosing_no_sorting_survives_a_restart(tmp_path: Path) -> None:
    """显式选了「不排序」就不能被下一次启动的默认值拽回去 —— 那正是哨兵存在的理由。"""
    state = SessionState().with_ordering(None, None, False)

    assert state.sort_facet == SORT_FACET_NONE  # 界面上传的是 None，会话里记的是哨兵

    save_session(state, base=tmp_path)

    assert load_session(base=tmp_path).resolved_sort_facet() is None


def test_with_ordering_keeps_other_fields() -> None:
    state = SessionState(thumbnail_edge=288, browse_mode=BROWSE_MODE_FLAT)

    updated = state.with_ordering("captured_year", "captured", True)

    assert updated.group_facet == "captured_year"
    assert updated.sort_facet == "captured"
    assert updated.sort_descending is True
    assert updated.thumbnail_edge == 288
    assert updated.browse_mode == BROWSE_MODE_FLAT
    assert state.group_facet is None
    assert state.sort_facet is None
