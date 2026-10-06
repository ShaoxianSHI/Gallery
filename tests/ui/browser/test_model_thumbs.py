"""模型里的缩略图记账：缩放搬到工作线程之后，尺寸是谁说了算（ADR 0015）。

缩放不再由模型做（见 ``ThumbnailPipeline``），所以模型退化成两件事：

1. 把流水线报上来的**边长**原样记下来；
2. 边长和当前档位对不上时，**如实说还需要** —— 切档瞬间在途的结果就是靠这条
   自我顶替的，格子不会停在旧尺寸上。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402

from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.model import (  # noqa: E402
    DEFAULT_PIXMAP_CACHE,
    PhotoListModel,
)


@pytest.fixture
def model(qt_app) -> PhotoListModel:  # type: ignore[no-untyped-def]
    del qt_app
    model = PhotoListModel(icon_edge=176)
    model.append([PhotoRef(path=Path("/库/一张.jpg"), size=100, mtime_ns=1)])
    return model


def _image(edge: int) -> QImage:
    return QImage(edge, edge, QImage.Format.Format_RGBA8888)


def test_a_matching_edge_is_accepted_as_is(model: PhotoListModel) -> None:
    path = Path("/库/一张.jpg")

    model.set_thumbnail(path, _image(176), 176)

    assert model.has_thumbnail(path)
    assert model.needs_thumbnail(path) is False


def test_a_stale_edge_is_treated_as_needing_a_refetch(model: PhotoListModel) -> None:
    """切档瞬间回来的旧尺寸结果：格子不能被它填歪，必须重取。

    这条是「缩放在工作线程做」的正确性兜底 —— 在途任务带着提交时的边长跑完，
    报出来的边长与当前档位不符，模型就当它没到货。
    """
    path = Path("/库/一张.jpg")

    model.set_thumbnail(path, _image(64), 64)

    assert model.needs_thumbnail(path) is True


def test_switching_tiers_invalidates_the_cache(model: PhotoListModel) -> None:
    path = Path("/库/一张.jpg")
    model.set_thumbnail(path, _image(176), 176)

    model.set_icon_edge(288)

    assert model.needs_thumbnail(path) is True


def test_zero_edge_falls_back_to_scaling_here(model: PhotoListModel) -> None:
    """``edge=0``（流水线没缩）时模型仍然按当前档位缩一次，记账也记当前档位。"""
    path = Path("/库/一张.jpg")

    model.set_thumbnail(path, _image(512), 0)

    pixmap = model.thumbnail_pixmap(path)
    assert pixmap is not None
    assert max(pixmap.width(), pixmap.height()) == 176
    assert model.needs_thumbnail(path) is False


def test_zero_edge_does_not_enlarge(model: PhotoListModel) -> None:
    path = Path("/库/一张.jpg")

    model.set_thumbnail(path, _image(64), 0)

    pixmap = model.thumbnail_pixmap(path)
    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (64, 64)


# ---- 「还在这一层吗」：在途结果的成败该用同一个判据 ------------------------------


def test_a_photo_on_this_level_is_still_shown(model: PhotoListModel) -> None:
    assert model.shows_path(Path("/库/一张.jpg")) is True


def test_a_path_that_left_this_level_is_not_shown(model: PhotoListModel) -> None:
    """改名 / 移走 / 删掉之后，旧路径就不在这一层了。

    这是缩略图流水线的在途结果该被丢掉的依据：解码读的是旧路径，文件已经不在了，
    失败是必然的，而那**不是**「文件损坏」—— 成功回填与失败上报都必须按这一条判断。
    """
    assert model.shows_path(Path("/库/旧名.jpg")) is False


def test_a_group_card_cover_counts_as_shown(qt_app) -> None:  # type: ignore[no-untyped-def]
    """分组卡片的封面不在 ``_row_by_path`` 里，但它照样在屏幕上，不能当成「不在了」。"""
    del qt_app
    photo = PhotoRef(path=Path("/库/封面.jpg"), size=100, mtime_ns=1)
    model = PhotoListModel(icon_edge=176)
    model.reorder([GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,))])

    assert model.shows_path(Path("/库/封面.jpg")) is True


def _used_bytes(model: PhotoListModel) -> int:
    return sum(item.pixmap.width() * item.pixmap.height() * 4 for item in model._pixmaps.values())


def test_the_memory_hold_is_capped_by_bytes_not_by_count(qt_app, tmp_path: Path) -> None:
    """**内存按字节封顶**，不按张数 —— 每张是 ``边长² × 4`` 字节。

    只按条目数淘汰的话，「按 176px 估算约 30 MB」那句只在 176 那一档成立：用户选「大」
    档只看一个键，那一份内存就白白涨上去。

    **判据写死绝对数字，不用 ``PIXMAP_MEMORY_BUDGET`` 那个常量。** 拿被测对象自己的
    常量当基准是同义反复：把常量调到天上，代码与断言两边一起变大，这条用例就成了
    永远为绿的那种守护 —— 本仓库吃过好几次这个亏。
    """
    del qt_app
    ceiling = 64 * 1024 * 1024  # 与 PIXMAP_MEMORY_BUDGET 同档，但在这里写死
    for edge in (288, 176):
        model = PhotoListModel(icon_edge=edge)
        refs = [
            PhotoRef(path=tmp_path / f"{edge}-{index}.jpg", size=1, mtime_ns=index)
            for index in range(900)
        ]
        model.append(refs)
        for ref in refs:
            model.set_thumbnail(ref.path, _image(edge), edge)

        used = _used_bytes(model)
        assert used <= ceiling, (
            f"{edge} 档留下 {used / 1024 / 1024:.1f} MB，超过 {ceiling / 1024 / 1024:.0f} MB"
            " —— 字节上限没生效（只按张数封顶就会这样）"
        )
        assert model._pixmaps, "不该全被淘汰掉"
        assert len(model._pixmaps) < len(refs), "不该全留着"


def test_the_count_cap_still_applies_for_tiny_images(qt_app, tmp_path: Path) -> None:
    """**字节很小的时候张数仍然封顶** —— 只按字节算的话会一直存下去。

    :data:`PIXMAP_MEMORY_BUDGET` 是主要那个上限，:data:`DEFAULT_PIXMAP_CACHE` 是兜底：
    边长极小时每张只占几 KB，字节离预算还远得很，而张数无界。
    """
    del qt_app
    model = PhotoListModel()
    refs = [
        PhotoRef(path=tmp_path / f"t{index}.jpg", size=1, mtime_ns=index)
        for index in range(DEFAULT_PIXMAP_CACHE + 50)
    ]
    model.append(refs)
    for ref in refs:
        model.set_thumbnail(ref.path, _image(4), 4)

    assert len(model._pixmaps) <= DEFAULT_PIXMAP_CACHE, (
        f"留下 {len(model._pixmaps)} 张，超过张数上限 {DEFAULT_PIXMAP_CACHE}"
    )
