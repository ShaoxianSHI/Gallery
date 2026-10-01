"""``gallery.ui.navigation`` 的测试：面包屑与上级推导，都不需要 Qt。"""

from __future__ import annotations

from pathlib import Path

from gallery.ui.navigation import breadcrumb_parts, parent_of


def test_breadcrumb_at_root_has_one_part() -> None:
    root = Path("/照片/库")

    assert breadcrumb_parts(root, root) == [("库", root)]


def test_breadcrumb_lists_every_level() -> None:
    root = Path("/照片/库")
    current = root / "2022-复活节波尔多" / "比拉沙丘"

    parts = breadcrumb_parts(root, current)

    assert [label for label, _ in parts] == ["库", "2022-复活节波尔多", "比拉沙丘"]
    assert [path for _, path in parts] == [
        root,
        root / "2022-复活节波尔多",
        root / "2022-复活节波尔多" / "比拉沙丘",
    ]


def test_breadcrumb_falls_back_to_root_for_outside_paths() -> None:
    root = Path("/照片/库")

    assert breadcrumb_parts(root, Path("/别处/某目录")) == [("库", root)]


def test_breadcrumb_labels_root_without_name() -> None:
    parts = breadcrumb_parts(Path("/"), Path("/"))

    assert parts == [("/", Path("/"))]


def test_parent_of_walks_up_one_level() -> None:
    root = Path("/照片/库")
    current = root / "a" / "b"

    assert parent_of(root, current) == root / "a"
    assert parent_of(root, root / "a") == root


def test_parent_of_stops_at_root() -> None:
    root = Path("/照片/库")

    assert parent_of(root, root) is None


def test_parent_of_refuses_outside_paths() -> None:
    root = Path("/照片/库")

    assert parent_of(root, Path("/别处")) is None
    assert parent_of(root, Path("/照片/库2")) is None
