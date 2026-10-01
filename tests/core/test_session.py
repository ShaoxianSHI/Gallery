"""``gallery.core.session`` 的测试。

会话状态不是数据：损坏、乱填、缺字段都必须静默回退，绝不能因为它打不开软件。
"""

from __future__ import annotations

import json
from pathlib import Path

from gallery.core import paths
from gallery.core.session import (
    MAX_RECENT_ROOTS,
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
