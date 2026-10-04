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

from waiting import DEFAULT_TIMEOUT_SECONDS

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.facade import Library  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.tasks import TaskRunner  # noqa: E402
from gallery.ui.thumbs.pipeline import ThumbnailPipeline, _Job  # noqa: E402


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

    assert wait_for(lambda: bool(got), timeout=DEFAULT_TIMEOUT_SECONDS)
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

    assert wait_for(lambda: bool(got), timeout=DEFAULT_TIMEOUT_SECONDS)
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

    assert wait_for(lambda: bool(got), timeout=DEFAULT_TIMEOUT_SECONDS)
    _, image, edge = got[0]
    assert edge == 0
    assert max(image.width(), image.height()) == 512  # type: ignore[attr-defined]
    runner.wait_for_done(2000)


# ---- 相册封面：读不出来的那一张不该霸着封面 -----------------------------------


def _unreadable(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """让这些路径一打开就失败 —— 堵在 ``Path.open`` 这个真实接缝上。

    云盘上的占位照片就是这个样子：文件在、``stat`` 得到大小与时间，而**读不出来**。
    换成别的入口（比如直接替掉 ``Library.thumbnail``）测的就不是产品代码了。
    """
    real_open = Path.open
    blocked = {str(path) for path in paths}

    def guarded(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if str(self) in blocked:
            raise OSError("文件可能还没下载完")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)


def _dominant(image: object) -> tuple[int, int, int]:
    """取这张图左上角的颜色 —— 用来确认封面**真的换成了另一张照片**。

    照片是纯色铺的，JPEG 压过之后各通道会差个位，所以断言比的是「谁的通道更大」，
    不是「等于多少」。
    """
    assert hasattr(image, "pixelColor")
    point = image.pixelColor(2, 2)  # type: ignore[attr-defined]
    return (point.red(), point.green(), point.blue())


def test_an_album_moves_on_when_its_first_photo_cannot_be_read(
    pipeline: tuple[ThumbnailPipeline, TaskRunner],
    tmp_path: Path,
    wait_for: Callable[..., bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相册封面读不出来时**换下一张**，别让整张卡片挂在一张永远出不来图上。

    现场：云盘把照片下到一半就放进文件夹，第一张读不出来。封面于是变成它，卡片一直
    是一张黑的 —— 而且换不掉，因为每一次都挑中同一张。
    """
    from PIL import Image

    from gallery.core.library.photos import FolderRef

    thumbs, runner = pipeline
    album = tmp_path / "相册"
    album.mkdir()
    broken = album / "下到一半.jpg"
    good = album / "还在下.jpg"
    for name, color in ((broken, (200, 40, 40)), (good, (40, 200, 40))):
        Image.new("RGB", (600, 400), color).save(name)
    _unreadable(monkeypatch, broken)
    entry = FolderRef.from_path(album)
    got = _collect(thumbs)

    thumbs.set_window([entry])

    assert wait_for(lambda: bool(got), timeout=DEFAULT_TIMEOUT_SECONDS), "相册卡片没有拿到封面"
    assert got[0][0] == album, "封面换人时这一格不该报错退出（那会把整张卡片判为失败）"
    # 真的换人了：封面是**第二张**（绿的那张），不是读不出来的那张（红）。
    red, green, _blue = _dominant(got[0][1])
    assert green > red, "封面还是那张读不出来的照片 —— 根本没换人"
    runner.wait_for_done(2000)


def test_an_album_whose_every_photo_fails_says_why(
    pipeline: tuple[ThumbnailPipeline, TaskRunner],
    tmp_path: Path,
    wait_for: Callable[..., bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整本相册都读不出来时要说清楚原因 —— 只画一个文件夹图形等于什么也没说。"""
    from PIL import Image

    from gallery.core.library.photos import FolderRef

    thumbs, runner = pipeline
    album = tmp_path / "整本都拉不下来"
    album.mkdir()
    broken = album / "一张.jpg"
    Image.new("RGB", (600, 400), (200, 40, 40)).save(broken)
    _unreadable(monkeypatch, broken)
    entry = FolderRef.from_path(album)
    failed: list[tuple[Path, str]] = []
    thumbs.failed.connect(lambda path, message: failed.append((path, message)))

    thumbs.set_window([entry])

    assert wait_for(lambda: bool(failed), timeout=DEFAULT_TIMEOUT_SECONDS)
    assert failed[0][0] == album
    assert "读不出" in failed[0][1] or "下载" in failed[0][1]
    runner.wait_for_done(2000)


def test_a_stale_result_does_not_release_the_new_jobs_slot(qt_app, tmp_path: Path) -> None:
    """**旧一代的迟到结果不许摘掉新一代的在途登记。**

    缺陷现场：``shutdown()``（切库 / 关窗）清空了 ``_inflight`` 并换代。上一代还在
    路上的任务若晚回来、还照着老写法无条件 ``pop(entry.path)``，摘掉的就是新一代
    为同一格登记的那一条 —— ``_inflight`` 于是少数了（超过 ``_max_inflight`` 也不再
    补），而 ``cancel()`` 更是再也够不着那个真正在跑的新任务。
    """
    del qt_app
    prepare_library(tmp_path)
    from PIL import Image

    path = tmp_path / "一张.jpg"
    Image.new("RGB", (800, 600), (200, 120, 40)).save(path)
    entry = PhotoRef.from_path(path)
    runner = TaskRunner()
    pipeline = ThumbnailPipeline(runner=runner, library=Library.open(tmp_path))

    # 手动造两个「在途登记」：先记旧的一代，再换代并登记新的那一代。
    pipeline._generation += 1
    old_generation = pipeline._generation
    pipeline._inflight[entry.path] = 111
    pipeline.shutdown()
    pipeline._generation += 1
    pipeline._inflight[entry.path] = 222

    # 旧一代的回调迟到（直接调私有回调，不去跑真的线程池）。
    pipeline._on_result(entry, old_generation, _Job(job_id=111), None)

    assert pipeline._inflight.get(entry.path) == 222, "新任务的在途登记不该被旧回调摘掉"

    # 而**自己那一次**的回调仍然要能正常摘掉，否则在途表永远清不干净。
    pipeline._on_result(entry, pipeline._generation, _Job(job_id=222), None)
    assert entry.path not in pipeline._inflight

    pipeline.shutdown()
