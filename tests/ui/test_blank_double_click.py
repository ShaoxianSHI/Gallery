"""双击网格里的空白处：回到上一层。

「上一层」在逐层进入的浏览里是最高频的动作之一，而照片常常铺不满一屏 ——
右下角总有一大片空白，一个格子都没有。访达里双击空白处是「往上走」，
这里沿用同一个手势：**双击没有格子的地方就退回上一层**（在分组里则是退出分组）。

退不回去时（已经在库根、或者在「全部照片」这种没有上一层的视图里）说一句为什么，
不做「按了没反应」——空白双击是个明确的意图，忽略它等于让用户猜自己是不是点歪了。

反过来也有一条硬边界：**双击打开的那一下不能被自己当成双击空白处**。打开相册 /
进入分组是同步的（模型当场清空、版面重排），落点必须在交给基类之前问清楚 ——
否则刚点开的相册会被立刻弹回来，屏幕上只剩一闪。文件末尾有钉这一条的两条测试。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描 / 分组排完的时限。
SCAN_TIMEOUT_SECONDS = 8.0

#: 分组字段：按「所在文件夹」分。这一档只看路径，不读 EXIF，
#: 于是这些测试既不依赖本机装了 exiftool，也不必再造一个假的批量读取器。
FOLDER_FACET = "folder"


def open_window(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_ALBUMS,
    group_facet: str | None = None,
) -> MainWindow:
    """开一个窗口，等当前这一层列完。

    库根放在 ``tmp_path/库``：会话目录和 ``.gallery/`` 都在库外，免得根目录里混进
    「状态」「.gallery」这些不是相册的文件夹，行数对不上。
    """
    session_base = root.parent / "状态"
    save_session(
        SessionState(
            recent_roots=(root,),
            browse_mode=browse_mode,
            group_facet=group_facet,
        ),
        base=session_base,
    )
    # 先把配置目录准备好：没有它时 MainWindow 会问「缓存放哪」，
    # 那个模态框在测试里没人点，窗口就永远卡在构造中。
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    wait_until(window, qt_app, lambda: window._scan_job is None and window._facet_job is None)
    return window


def wait_until(
    window: MainWindow, qt_app: QApplication, ready: Callable[[], bool], *, what: str = "这一层"
) -> None:
    """转到 ``ready()`` 成立为止（扫描与排序都在后台线程上）。"""
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while True:
        qt_app.processEvents()
        if ready():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what}没有在时限内就绪（当前 {window._model.rowCount()} 行）")
        time.sleep(0.02)


def settle(qt_app: QApplication, seconds: float = 0.3) -> None:
    """转一会儿事件循环，不看任何条件。

    用来「再给几圈机会」：想抓的是**不该发生**的那一下（双击进去又被弹回来），
    它同步发生、绝不会在下一轮之前溜掉 —— 但多转几圈才不会漏。
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def _blank_point(grid: PhotoGrid) -> QPoint:
    """网格里一个不属于任何格子的点（采样找，不去猜排版留白）。"""
    rect = grid.viewport().rect()
    for y in range(2, rect.height() - 2, 6):
        for x in range(2, rect.width() - 2, 6):
            point = QPoint(x, y)
            if not grid.indexAt(point).isValid():
                return point
    raise AssertionError("整个视口都被格子铺满了，没有空白可双击")


def double_click(grid: PhotoGrid, point: QPoint) -> None:
    """在 ``point`` 上双击一次：先点一下，再补上双击的那一下。

    刻意不发「孤零零一个双击事件」：那样发出来的双击与真实手势的按下状态对不上，
    落在格子上的那一版本基类会当成没有前一次按下 —— 照片打不开（空白处那半边却照常
    触发，看着就像这个功能只在空白处存在）。真人双击总是「按下、抬起、再按下、抬起」。
    """
    viewport = grid.viewport()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseDClick(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point, 20
    )


def double_click_blank(window: MainWindow, qt_app: QApplication) -> None:
    """在当前这一层的空白处双击一下。"""
    double_click(window._grid, _blank_point(window._grid))
    qt_app.processEvents()


def test_blank_double_click_walks_out_of_an_album(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """在相册里双击空白处：退回上一级相册。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2, what="相册内")

        double_click_blank(window, qt_app)
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2, what="退回后的这一层")

        assert window._current_dir == library
        assert {entry.name for entry in window._model.entries()} == {"海边", "山巅"}
    finally:
        window.close()


def test_blank_double_click_walks_out_of_an_empty_album(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """空相册里连一个格子都没有，双击任何地方都得能退出去。

    这正是最想立刻走人的情形：横幅刚说完「这个文件夹里既没有子文件夹，也没有照片」，
    屏幕上却没有任何可双击的东西。
    """
    library = tmp_path / "库"
    (library / "空的").mkdir(parents=True)
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "空的")
        wait_until(window, qt_app, lambda: window._scan_job is None)
        assert window._model.rowCount() == 0

        double_click(window._grid, window._grid.viewport().rect().center())
        qt_app.processEvents()
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2)

        assert window._current_dir == library
        assert {entry.name for entry in window._model.entries()} == {"空的", "山巅"}
    finally:
        window.close()


# ---- 回归：双击进去的那一下不能又被当成「双击空白处」 --------------------------------


def test_double_clicking_an_album_card_enters_it_and_stays_there(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """双击相册卡片要真的进得去，而且留在里面。

    缺陷现场：点开相册（模型当场清空、版面重排）之后才去问「这一点底下还有没有格子」，
    问到的已经是新的一层，于是刚进去又被「双击空白处」弹回来，屏幕上只剩一闪。
    与相册里有没有照片无关：空相册同样要进得去。
    """
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    (library / "空的").mkdir(parents=True)
    window = open_window(qt_app, library)

    def enter_by_double_click(name: str) -> None:
        """双击根层上叫 ``name`` 的那张卡片，然后确认**留在里面**。"""
        row = next(
            index for index, entry in enumerate(window._model.entries()) if entry.name == name
        )
        center = window._grid.visualRect(window._model.index(row, 0)).center()
        target = library / name

        double_click(window._grid, center)
        wait_until(window, qt_app, lambda: window._current_dir == target, what=name)
        # 再转几圈事件循环：退回去同样是同步的，多给它几次机会照样看得见。
        settle(qt_app)
        assert window._current_dir == target, f"双击「{name}」刚进去就被弹回来了"

        # 退回去，等这一层真的列完（``_current_dir`` 是同步设好的，条目还在路上）。
        window.go_up()
        wait_until(
            window,
            qt_app,
            lambda: window._current_dir == library and window._scan_job is None,
            what="退回根",
        )
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2, what="根层列完")

    try:
        assert window._model.rowCount() == 2
        enter_by_double_click("海边")
        enter_by_double_click("空的")
    finally:
        window.close()


def test_double_clicking_a_group_card_enters_it_and_stays_there(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """组卡片同理：双击要留在组里，而不是进一半就被空白双击弹出去。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "山巅")
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT, group_facet=FOLDER_FACET)
    try:
        wait_until(window, qt_app, lambda: window._model.group_count() == 2, what="分组")
        target = window._model.group_at(0)
        assert isinstance(target, GroupRef)
        center = window._grid.visualRect(window._model.index(0, 0)).center()

        double_click(window._grid, center)
        wait_until(window, qt_app, lambda: window._group_view is not None, what="进组")
        settle(qt_app)

        assert window._group_view is target, "双击组卡片刚进去就被弹回来了"
        assert [entry.name for entry in window._model.entries()] == [
            photo.name for photo in target.photos
        ]
    finally:
        window.close()


def test_blank_double_click_leaves_a_group(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """在分组里双击空白处：退出这个组，回到摆着组卡片的那一层。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "山巅")
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT, group_facet=FOLDER_FACET)
    try:
        wait_until(window, qt_app, lambda: window._model.group_count() == 2, what="分组")
        target = window._model.group_at(0)
        assert isinstance(target, GroupRef)
        window.enter_group(target)
        wait_until(window, qt_app, lambda: window._scan_job is None, what="组内")
        assert window._group_view is target

        double_click_blank(window, qt_app)
        wait_until(window, qt_app, lambda: window._model.group_count() == 2, what="退出分组之后")

        assert window._group_view is None
        assert sorted(entry.name for entry in window._model.entries()) == ["山巅", "海边"]
    finally:
        window.close()


def test_blank_double_click_at_the_library_root_explains_itself(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """已经退不动了就说清楚为什么，不做「按了没反应」。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        assert window._can_go_up() is False

        double_click_blank(window, qt_app)

        assert window._current_dir == library, "已经在库根，不能再往上"
        assert window._banner.text() == "已经是最外面一层了，没有上一级可退。"
    finally:
        window.close()


def test_blank_double_click_does_nothing_while_renaming(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改到一半：这一下归改名，不能顺手把人带走，更不能说「退不动了」。

    那句话在改名的过程中是**错的** —— 明明退得回去，只是这一刻归输入框。
    """
    library = tmp_path / "库"
    picture = make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_until(window, qt_app, lambda: window._model.rowCount() == 1, what="相册内")
        window._grid.select_row(0)
        window.rename_current_entry()
        assert window._grid.is_renaming()

        window._on_background_activated()

        assert window._current_dir == library / "海边"
        assert window._banner.isHidden() is True, "改名的过程中不该说「退不动了」"
        assert picture.exists()
    finally:
        window.close()


def test_double_clicking_a_photo_still_opens_the_big_view(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """回归：双击格子仍然是打开大图，新加的空白双击不能抢走这一下。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_until(window, qt_app, lambda: window._model.rowCount() == 1, what="相册内")
        center = window._grid.visualRect(window._model.index(0, 0)).center()

        double_click(window._grid, center)
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._detail_page
        assert window._current_dir == library / "海边", "双击照片不该顺手退上一层"
    finally:
        window.close()
