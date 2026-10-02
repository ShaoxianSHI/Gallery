"""``ThumbnailPipeline`` 的结果契约（ADR 0015）。

缩放搬进工作线程之后，流水线交回来的不再只是一张图，而是**一张图 + 它是缩到几
才发出来的**。这条边长是整个改动里唯一的新约定，值得钉住两件事：

1. 交回来的图确实已经缩到目标边长了（缩放真的在工作线程做完了）；
2. 边界上是「按提交那一刻的边长」走完的 —— 切档不会让一张图说谎说自己是大档的。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.facade import Library  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.tasks import TaskRunner  # noqa: E402
from gallery.ui.thumbs.pipeline import ThumbnailPipeline  # noqa: E402


@pytest.fixture
def photo(tmp_path: Path) -> PhotoRef:
    from PIL import Image

    path = tmp_path / "一张.jpg"
    image = Image.new("RGB", (800, 600), (200, 120, 40))
    image.save(path, quality=90)
    image.close()
    return PhotoRef.from_path(path)


@pytest.fixture
def pipeline(qt_app, tmp_path: Path):  # type: ignore[no-untyped-def]
    del qt_app
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    runner = TaskRunner()
    return ThumbnailPipeline(library, runner), runner


def _collect(pipeline: ThumbnailPipeline) -> list[tuple[Path, object, int]]:
    got: list[tuple[Path, object, int]] = []
    pipeline.ready.connect(lambda path, image, edge: got.append((path, image, edge)))
    return got


def test_the_image_is_already_scaled_when_it_arrives(
    pipeline: tuple[ThumbnailPipeline, TaskRunner],
    photo: PhotoRef,
    wait_for: Callable[..., bool],
) -> None:
    thumbs, runner = pipeline
    got = _collect(thumbs)

    thumbs.set_window([photo], target_edge=64)

    assert wait_for(lambda: bool(got), timeout=8.0)
    _, image, edge = got[0]
    assert edge == 64
    assert max(image.width(), image.height()) == 64  # type: ignore[attr-defined]
    runner.wait_for_done(2000)


def test_the_reported_edge_is_the_one_it_was_submitted_with(
    pipeline: tuple[ThumbnailPipeline, TaskRunner],
    photo: PhotoRef,
    wait_for: Callable[..., bool],
) -> None:
    """在途任务带着提交时的边长跑完 —— 不在完成时现读当前的档位。"""
    thumbs, runner = pipeline
    got = _collect(thumbs)

    thumbs.set_window([photo], target_edge=64)
    thumbs.set_window([photo], target_edge=288)  # 切档

    assert wait_for(lambda: bool(got), timeout=8.0)
    _, _, edge = got[0]
    assert edge == 64
    runner.wait_for_done(2000)


def test_no_target_edge_means_no_scaling(
    pipeline: tuple[ThumbnailPipeline, TaskRunner],
    photo: PhotoRef,
    wait_for: Callable[..., bool],
) -> None:
    thumbs, runner = pipeline
    got = _collect(thumbs)

    thumbs.set_window([photo])

    assert wait_for(lambda: bool(got), timeout=8.0)
    _, image, edge = got[0]
    assert edge == 0
    assert max(image.width(), image.height()) == 512  # type: ignore[attr-defined]
    runner.wait_for_done(2000)
