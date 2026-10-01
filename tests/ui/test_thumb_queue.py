"""``gallery.ui.thumb_queue`` 的测试：排队策略不依赖 Qt。"""

from __future__ import annotations

from pathlib import Path

from gallery.ui.thumb_queue import PREFETCH, VISIBLE, ThumbnailQueue


def paths(*names: str) -> list[Path]:
    return [Path(f"/照片/{name}") for name in names]


def test_add_reports_new_requests() -> None:
    queue = ThumbnailQueue()
    path = Path("/照片/a.jpg")

    assert queue.add(path) is True
    assert queue.add(path) is False
    assert len(queue) == 1
    assert path in queue


def test_pop_next_prefers_visible() -> None:
    queue = ThumbnailQueue()
    for path in paths("a", "b", "c"):
        queue.add(path, priority=PREFETCH)
    queue.add(Path("/照片/重点.jpg"), priority=VISIBLE)

    assert queue.pop_next() == Path("/照片/重点.jpg")
    assert queue.pop_next() == Path("/照片/a")


def test_visible_requests_keep_fifo_order() -> None:
    queue = ThumbnailQueue()
    for path in paths("a", "b", "c"):
        queue.add(path, priority=VISIBLE)

    assert [queue.pop_next() for _ in range(3)] == paths("a", "b", "c")


def test_add_upgrades_priority_but_keeps_position() -> None:
    queue = ThumbnailQueue()
    for path in paths("a", "b"):
        queue.add(path, priority=PREFETCH)

    queue.add(Path("/照片/b"), priority=VISIBLE)

    assert queue.pop_next() == Path("/照片/b")
    assert queue.pop_next() == Path("/照片/a")


def test_add_does_not_downgrade_priority() -> None:
    queue = ThumbnailQueue()
    path = Path("/照片/a.jpg")
    queue.add(path, priority=VISIBLE)

    queue.add(path, priority=PREFETCH)

    assert queue.pop_next() == path


def test_set_window_drops_everything_else() -> None:
    queue = ThumbnailQueue()
    for path in paths("a", "b", "c", "d"):
        queue.add(path)

    queue.set_window(paths("c", "d"), prefetch=paths("e"))

    assert len(queue) == 3
    assert Path("/照片/a") not in queue
    assert queue.pop_next() == Path("/照片/c")
    assert queue.pop_next() == Path("/照片/d")
    assert queue.pop_next() == Path("/照片/e")
    assert queue.pop_next() is None


def test_set_window_marks_visible_first_even_if_added_as_prefetch() -> None:
    queue = ThumbnailQueue()
    queue.add(Path("/照片/b"), priority=PREFETCH)

    queue.set_window(paths("b"), prefetch=paths("a", "c"))

    assert queue.pop_next() == Path("/照片/b")


def test_duplicate_paths_in_window_are_deduped() -> None:
    queue = ThumbnailQueue()

    queue.set_window(paths("a", "a", "b"), prefetch=paths("b"))

    assert len(queue) == 2
    assert queue.pop_next() == Path("/照片/a")
    assert queue.pop_next() == Path("/照片/b")


def test_discard_and_clear() -> None:
    queue = ThumbnailQueue()
    queue.add(Path("/照片/a"))
    queue.add(Path("/照片/b"))

    queue.discard(Path("/照片/a"))
    assert Path("/照片/a") not in queue

    queue.clear()
    assert len(queue) == 0
    assert queue.pop_next() is None


def test_discard_missing_path_is_harmless() -> None:
    ThumbnailQueue().discard(Path("/照片/不存在.jpg"))
