"""重排之前那道「这次会不会变」的前置条件（``_reorder_level`` 开头那道）。

那道前置条件必须排在排序**之前**、而且比的是**输入**而不是结果：判据要是比 ``ordered``，那已经是刚
花几十毫秒（三万张）算出来的东西，「监听器报了个自己写的回环、什么其实都没变」那条路上就会
每次都白付一次整层排序。

这组用例盯的是两件事：

- 没变的时候**连排序都不调用**（省下来的就是那一次整层排序）
- 变了的时候**一定真的重排**（前置条件不能为了快而把该做的活忽略掉）

第二条更要紧：前置条件判错的后果是屏幕上的顺序悄悄不对，只当是程序临时不可用。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until, window_is_idle  # noqa: E402

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata import batch  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window import ordering as ordering_module  # noqa: E402

DATES = {
    "三月一张.jpg": "2024:03:16 10:00:00",
    "四月一张.jpg": "2024:04:16 09:00:00",
    "四月两张.jpg": "2024:04:16 18:00:00",
}
NAMES = tuple(DATES)


@pytest.fixture
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            raw: dict[str, object] = {}
            when = DATES.get(Path(path).name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path], fake_facet_source: None
) -> MainWindow:
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    # 两个相册：**相册模式**那一层里它们是相册卡片，而相册的顺序只受「降序」影响
    # （它们不进排序字段，见 ADR 0049）。平铺模式压根没有相册卡片，量不到。
    for album in ("Yokohama", "Amsterdam"):
        (root / album).mkdir()
        make_photo(f"{album}.jpg", directory=root / album)
    prepare_library(root)
    session_base = tmp_path / "会话"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    win = MainWindow(session_base=session_base, initial_library=root)
    win.resize(900, 600)
    win.show()
    qt_app.processEvents()
    assert wait_until(qt_app, lambda: window_is_idle(win)), "扫描没有收工"
    qt_app.processEvents()
    return win


def _album_names(window: MainWindow) -> list[str]:
    """这一层里相册卡片的名字（照片不算）。"""
    return [entry.name for entry in window._model.entries() if isinstance(entry, FolderRef)]


@pytest.fixture
def count_sorts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """数「这一层真的排了一次」。**判据是排序函数被调用** —— 那就是省下来的时间本身。

    刻意不去读被测代码自己的常量或内部字段当基准：那样注入违规也未必变红（同一个同义反复
    两头兜）。
    """
    calls: list[str] = []
    for name in ("sort_photos", "build_groups"):
        real = getattr(ordering_module, name)

        def counting(
            *args: object, _real: object = real, _name: str = name, **kwargs: object
        ) -> object:
            calls.append(_name)
            return _real(*args, **kwargs)  # type: ignore[operator]

        monkeypatch.setattr(ordering_module, name, counting, raising=True)
    return calls


def test_a_reorder_that_changes_nothing_skips_the_sort(
    window: MainWindow, count_sorts: list[str]
) -> None:
    """什么都不没变 → 连排序都不调用。

    先空跑一次把前置条件的「上一次输入」记牢（开窗那次的记录可能赶在元数据读回来之前，
    那样 ``_facet_values`` 就已经换了一份，前置条件理应放行）。之后才是要盯的那一次。
    """
    assert window._library is not None
    window._reorder_level(window._library)
    count_sorts.clear()

    window._reorder_level(window._library)

    assert count_sorts == [], "输入逐字没变却重排了一次：又白付了一次整层排序"


def test_changing_the_sort_direction_still_reorders(
    window: MainWindow, count_sorts: list[str]
) -> None:
    """改了排序方向 → 一定要真排。前置条件要是把这个也忽略掉，屏幕上的顺序就悄悄错了。"""
    assert window._library is not None
    window._reorder_level(window._library)
    before = list(window._model.entries())
    count_sorts.clear()

    window._sort_descending = not window._sort_descending
    window._reorder_level(window._library)

    assert count_sorts != [], "改了排序方向却没排：顺序该变的没变"
    assert list(window._model.entries()) != before, "排了，但顺序没换过来"


def test_changing_the_direction_also_moves_the_album_cards(
    window: MainWindow, count_sorts: list[str]
) -> None:
    """翻方向时**相册卡片也要换位置**（ADR 0049）。

    相册不参与排序字段，所以前置条件的指纹里没有它们的那一份 —— 少了这一段，相册卡片会在
    「照片翻了、相册没翻」的状态下停住，而且此后没有任何东西会纠正它。
    """
    library = window._library
    assert library is not None
    window._reorder_level(library)
    albums_before = _album_names(window)
    assert len(albums_before) == 2, "这一层里得有相册卡片才量得到相册的顺序"
    count_sorts.clear()

    window._sort_descending = not window._sort_descending
    window._reorder_level(library)

    # **只量相册那一段**：照片本来就会重排，拿整层比的话即使相册根本没动也照样是「变了」。
    assert _album_names(window) == albums_before[::-1], "翻了方向，相册卡片却没换位置"


def test_a_fresh_facet_read_still_reorders(window: MainWindow, count_sorts: list[str]) -> None:
    """**元数据重读过一次 → 一定要真排。**

    这是那道前置条件最容易判错的一处：重读回来的 ``_facet_values`` 内容可能和上一次**完全一样**
    （同一批照片又读了一遍 EXIF），可它确实是一份新对象。判据记 ``id(values)`` 是不行的 ——
    Python 会复用已释放对象的地址，换库之后新那份完全可能落在同一个地址上，前置条件就会以为
    「没变」而跳过排序，而那一层恰恰刚重扫过。
    """
    assert window._library is not None
    window._reorder_level(window._library)
    count_sorts.clear()

    window._facet_values = dict(window._facet_values)
    window._reorder_level(window._library)

    assert count_sorts != [], "元数据重读了一遍却没重排：那份新数据可能带了新的排序值"


def test_a_fresh_event_mapping_still_reorders(window: MainWindow, count_sorts: list[str]) -> None:
    """**事件映射换了一份 → 一定要真排**（ADR 0043 决策 7）。

    与上面那条元数据同源，只是第二份输入：用户记完 / 移出一张照片之后，那份「照片各属于哪些
    事件」是新对象，而内容可能与上一次一模一样。前置条件漏了它，用户看到的就是「记完什么都没发生」。
    """
    assert window._library is not None
    window._reorder_level(window._library)
    count_sorts.clear()

    window._event_values = dict(window._event_values)
    window._reorder_level(window._library)

    assert count_sorts != [], "事件映射换了一份却没重排：刚记进去的那几张位置就是错的"


def test_a_new_photo_still_reorders(window: MainWindow, count_sorts: list[str]) -> None:
    """多了一张照片 → 一定要真排。"""
    assert window._library is not None
    window._reorder_level(window._library)
    count_sorts.clear()

    window._model.append([PhotoRef(path=Path("/tmp/多出来的一张.jpg"), size=1, mtime_ns=1)])
    window._reorder_level(window._library)

    assert count_sorts != [], "多了一张照片却没重排：新照片的位置就是错的"
