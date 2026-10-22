"""在**当前这一层**里筛选（ADR 0040）在窗口里的整条链。

对位 :mod:`tests.ui.test_search_flow`（那是整库检索，ADR 0026）：同一个胶囊、同一套条件语法，
差别只在那些条件**管到哪儿**。所以这一份测试盯的是那些「只在筛选下才成立」的行为 ——
视图不换、卡片按子树收留、换视图条件跟着走、写操作让路、面包屑照旧报路径。

**元数据读取与地点反查都用假的**（理由与 ``test_search_flow.py`` 一样）：测试不依赖本机装了
exiftool，也不该因为机器上碰巧装了就把行为测偏。
"""

from __future__ import annotations

import ast
import datetime
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from outline_helpers import assert_no_darker_dots  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    SEARCH_SCOPE_LEVEL,
    SEARCH_SCOPE_LIBRARY,
    prepare_library,
)
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    SessionState,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.chips import ChipButton  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.rename_edit import field_background  # noqa: E402
from gallery.ui.window.widgets.external_button import ICON_EDGE as EXTERNAL_ICON_EDGE  # noqa: E402
from gallery.ui.window.widgets.search_field import (  # noqa: E402
    ICON_EDGE,
    PLACEHOLDER_FILTER,
    PLACEHOLDER_SEARCH,
    TOGGLE_ICON_EDGE,
    magnifier,
    scope_arrows,
)

#: 测试库里每张照片的相机（按文件名对应）。没给的就是没写相机。
CAMERAS = {"海边-01.jpg": "Canon EOS R5", "山.jpg": "Sony A7"}

#: 每张照片的拍摄时间。**取「这个月」而不是硬编码的年份**：日历停在今天上，而
#: :meth:`CalendarPage.photo_count` 只数**当前那一段**里看得见的。
TODAY = datetime.date.today()

#: 每张照片拍在今天往前多少天（``#拍摄时间`` 那一族在日历测试里要用）。
DAYS_AGO: dict[str, int] = {"海边-01.jpg": 3, "海边-02.jpg": 3, "山.jpg": 40}


def _captured_string(name: str) -> str | None:
    """那张照片的 ``EXIF:DateTimeOriginal``（取**中午**：零点压在区间的边界上，
    ``>=`` 与 ``<`` 差一秒就会少算它一张 —— 那种失败与真正的缺陷长得一模一样）。"""
    days = DAYS_AGO.get(name)
    if days is None:
        return None
    moment = datetime.datetime.combine(TODAY - datetime.timedelta(days=days), datetime.time(12, 0))
    return moment.strftime("%Y:%m:%d %H:%M:%S")


@pytest.fixture(autouse=True)
def fake_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """把地点反查换成假的，免得联网、也不依赖本机下没下过城市数据。"""

    def place_for(
        self: object, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace:
        return ResolvedPlace(city="西安", region="陕西省", country="中国")

    monkeypatch.setattr(LazyPlaceResolver, "place_for", place_for)
    monkeypatch.setattr(LazyPlaceResolver, "ready", property(lambda _self: True))


@pytest.fixture(autouse=True)
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """把批量读取换成假的：只给相机与拍摄时间。"""

    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            name = Path(path).name
            raw: dict[str, object] = {}
            camera = CAMERAS.get(name)
            if camera:
                raw["EXIF:Model"] = camera
            when = _captured_string(name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


def _open(
    qt_app: QApplication,
    root: Path,
    session_base: Path,
    *,
    browse_mode: str | None = BROWSE_MODE_FLAT,
) -> MainWindow:
    """开一个窗口。

    ``browse_mode=None`` 表示**别碰那个库的偏好** —— 「重开这个库还记着上次的设置」那条用例
    要的就是这个：``set_library_ui`` 是**整张 ``[ui]`` 表覆盖写**，所以每次都写等于把上一次
    设的作用域擦掉，那条断言于是变成「配置从没被写进去过」，量不到任何东西。
    """
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    if browse_mode is not None:
        set_library_ui(root, browse_mode=browse_mode)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(1000, 700)
    window.show()
    qt_app.processEvents()
    return window


def _library(tmp_path: Path, make_photo: Callable[..., Path]) -> Path:
    """测试库：根下两张、一个空的 ``家庭``、``旅行/2024`` 下一张。

    ``家庭`` **故意是空的**：相册卡片那条判据要验的就是「一张都没有的子相册不该摆出来」，
    库里若只有非空的相册，那条断言与「卡片全都不摆」是同一件事，测不出差别。
    """
    root = tmp_path / "照片"
    for directory, names in (
        (root, ("海边-01.jpg", "海边-02.jpg")),
        (root / "旅行" / "2024", ("山.jpg",)),
    ):
        for name in names:
            make_photo(name, directory=directory)
    (root / "家庭").mkdir(parents=True, exist_ok=True)
    prepare_library(root)
    return root


def _settle(qt_app: QApplication, window: MainWindow) -> None:
    """转到「这一层排好了、后台那一遍也跑完了」为止。

    ``_filter_job`` 与 ``_search_job`` 一并等掉：切作用域时两者可能交替进行中。
    """
    wait_for_idle(
        qt_app,
        window,
        also=("_search_job", "_filter_job", "_staged", "_index_job"),
        what="筛选、排序与索引",
    )


def _names(window: MainWindow) -> list[str]:
    return sorted(entry.name for entry in window._model.entries())


def _chips(window: MainWindow) -> list[str]:
    return [condition.chip_text for condition in window._conditions]


def _trail(window: MainWindow) -> list[str]:
    """面包屑那一行的全部文字，按摆放顺序（分隔符不算）。"""
    from PySide6.QtWidgets import QWidget

    parts: list[str] = []
    for widget in window._breadcrumb.findChildren(QWidget):
        text = widget.text().strip() if hasattr(widget, "text") else ""
        if text and text != "▸":
            parts.append(text)
    return parts


def _filter(
    qt_app: QApplication,
    window: MainWindow,
    text: str,
    *,
    scope: str = SEARCH_SCOPE_LEVEL,
) -> None:
    """切到筛选作用域，在胶囊里敲一串然后回车 —— 等价于用户做的事。

    **回车之后把光标从输入框里拿走**：界面在输入期间会让开会动文件的命令（那是三个输入框
    共用的一条规矩，ADR 0025）。于是「筛选时 ⌘V 是灰的」那条断言会在**任何**作用域下为真 ——
    测的其实是输入框的规矩，不是筛选的。焦点回到网格上才是用户做完一件事的样子。
    """
    window.set_search_scope(scope)
    qt_app.processEvents()
    window.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText(text)
    window._search_field.submit()
    qt_app.processEvents()
    window._grid.setFocus()
    qt_app.processEvents()
    _settle(qt_app, window)


# ---- 视图不换（这一族是筛选存在的理由）--------------------------------------------


def test_the_calendar_stays_a_calendar(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """**月视图里加一条条件，仍然停在月视图** —— 这是筛选与检索最要紧的区别。

    整库检索会接管这一层、换成网格页（ADR 0026 决策 1）；筛选不许。这一条如果反过来，用户在
    日历上按一次筛选就永远回不去日历了，而菜单里的勾还留着日历。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    assert window._stack.currentWidget() is window._calendar_page

    _filter(qt_app, window, "#相机 Canon")

    assert window._stack.currentWidget() is window._calendar_page, "筛选把日历页换成了网格页"
    window.close()


def test_the_calendar_only_shows_the_matching_photos(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历上只剩命中的那些照片 —— 由喂进分桶的那份引用决定，日历自己不改动任何绘制逻辑。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    assert window._calendar_page.photo_count() == 2, "前提不成立：三张里有两张在这个月"

    _filter(qt_app, window, "#相机 Canon")

    assert window._calendar_page.photo_count() == 1
    window.close()


def test_the_conditions_are_visible_on_the_calendar(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历那一页**看得见**自己加了哪几条条件。

    **这一条是这个需求本身**：筛选时那一页仍然是日历（上面那条），而日历那一页占掉的就是网格页
    那条面包屑（ADR 0033 决策 5）—— 胶囊只摆在面包屑上，就等于「加了但不知道加了什么」。

    量的是**工具条上真的有那几枚**，而不是「模型里的条件列表非空」：后者在日历上照样成立，
    它量不到「看不见」。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    chips = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(chips) == 1, "日历工具条上没有条件胶囊（这是那个需求本身）"
    assert "相机" in chips[0].text(), chips[0].text()
    window.close()


def test_clearing_a_condition_on_the_calendar_clears_the_toolbar_too(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在日历上把条件撤光，**工具条上那几枚也要跟着走**。

    **这一条盯的是那条早退路径**：摆胶囊的那一句写在 :meth:`_update_breadcrumb` 的末尾，而那个
    方法在「无条件」时 ``hide()`` 完就 ``return`` —— 撤光走的正是那一支，于是控件留在屏幕上
    （模型里已经空了，量模型永远绿）。

    所以这里从**窗口**这一层按界面上真走的路走一遍（点 ✕、回车、Esc 都试），而不是直接调
    ``set_conditions([])`` —— 后者绕开了那条早退，用例会一路绿。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)
    assert len(_calendar_chips(window)) == 2, "前提不成立：两枚胶囊没摆出来"

    # 走用户的手法：点 ✕ 逐条撤。
    window._on_condition_removed(0)
    _settle(qt_app, window)
    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == [], "条件已经撤光"
    assert _calendar_chips(window) == [], (
        f"条件已经撤光，日历工具条上还留着 {_calendar_chips(window)} ——"
        " 切换视图能清掉，所以症状是「要绕一圈才干净」"
    )
    window.close()


def test_clearing_conditions_in_the_search_field_clears_the_toolbar(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """**在搜索框里撤光**也要清掉工具条上那几枚 —— 与点 ✕ 同一个毛病的两条路。

    点 ✕ 那条已经锁定，但撤光有几条独立的路（点 ✕、输入框里清空回车、``⇧Esc``），而它们各自走
    一遍 ``_apply_conditions``。只钉一条的话，另几条各自坏掉都没人看着 —— 而症状一样。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)

    # **先让输入框空下来**：`dismiss` 的第一件事是清空输入框（那是「这一下」），输入框空了才轮到
    # 撤条件。**按到条件真的撤光为止**，不硬编码按几下。
    while _chips(window):
        window._search_field.dismiss()
        _settle(qt_app, window)

    assert _chips(window) == [], "前提不成立：没能撤光条件"
    assert _calendar_chips(window) == [], f"撤光后工具条上还留着 {_calendar_chips(window)}"
    window.close()


def _calendar_chips(window: MainWindow) -> list[str]:
    """日历工具条上那几枚胶囊的文字。"""
    return [chip.text() for chip in window._calendar_page._bar._chips.findChildren(ChipButton)]


def test_typing_a_new_condition_after_glancing_at_a_chip_appends_it(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """点了一下胶囊正文、没接着改，然后敲了一条新的 → **追加，不是替换**。

    危险在 ``_editing_condition`` **粘住**：点胶囊正文会设上它，而下一次回车走的是
    ``self._conditions[editing] = …`` —— 就地替换、条目数不增加，屏幕上只看得见「条件变了」，
    看不见「我刚才点过一枚胶囊」。

    **量的是条目数**：被顶掉的那条恰好与新的等价时，值的断言会一路绿。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    assert len(_chips(window)) == 1, "前提不成立：第一条条件没加上"

    # 用户在日历工具条上点了一下那枚胶囊的**正文**（不是 ✕），没接着改。
    window._on_condition_edited(0)
    _settle(qt_app, window)
    # 接着去做了别的事 —— 焦点离开了输入框，编辑态随之清掉。**这一步不能省**：
    # 留着编辑态才是在测「静默劫持」。
    window._grid.setFocus()
    _settle(qt_app, window)

    # 然后照常敲一条新的。
    _filter(qt_app, window, "#城市 西安")

    assert len(_chips(window)) == 2, (
        f"敲新条件把原来的顶掉了：{_chips(window)} ——"
        " 点过胶囊之后新敲的应当是追加（点过但没改的那一次不该劫持下一次提交）"
    )
    assert _chips(window) == ["相机：Canon", "城市：西安"], _chips(window)
    window.close()


def _is_conditions_target(node: ast.expr) -> bool:
    """这一个赋值 / ``del`` 的目标是不是 ``self._conditions``（或其下标）。

    **要认下标**是因为 ``self._conditions[i] = …``（就地替换）与 ``del self._conditions[i]``
    也都是改动它。脱下标时**只脱一层就够**（``self._conditions[a][b]`` 不存在），且脱完必须还
    留着那个 ``Attribute`` —— 顺手拆成 ``self`` 之后就没人拿得到 ``attr`` 了。
    """
    if isinstance(node, ast.Subscript):
        node = node.value
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_conditions"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


#: 会改 ``_conditions`` 长度的那些方法。``.sort()`` / ``.reverse()`` 改顺序不改长度，不算。
CONDITION_MUTATORS = frozenset({"append", "extend", "clear", "pop", "remove", "insert"})


def _is_conditions_mutation(node: ast.Call) -> bool:
    """这一次调用会不会改 ``self._conditions`` 的长度。

    **只认直接挂在 ``self._conditions`` 上的那几个方法**：``.append(x)`` 改长度，
    ``.sort()`` 不改，``.index()`` 更不改 —— 混进判据会让这条回归测试变成「凡碰到它就红」。
    """
    call = node.func
    return (
        isinstance(call, ast.Attribute)
        and call.attr in CONDITION_MUTATORS
        and isinstance(call.value, ast.Attribute)
        and call.value.attr == "_conditions"
        and isinstance(call.value.value, ast.Name)
        and call.value.value.id == "self"
    )


def _changes_the_conditions(node: ast.AST) -> bool:
    """这一句会不会改动条件列表 —— 赋值 / ``del`` / 改长度的调用，三支合起来。

    **读不算**：迭代、``len(...)``、下标取值与 ``.sort()``（改顺序不改长度）都走不到这三支。
    """
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets: tuple[ast.expr, ...] = (
            tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)
        )
        return any(_is_conditions_target(target) for target in targets)
    if isinstance(node, ast.Delete):
        return any(_is_conditions_target(target) for target in node.targets)
    if isinstance(node, ast.Call):
        return _is_conditions_mutation(node)
    return False


def _the_line_about_conditions(block: str) -> str:
    """从 ``ast.unparse`` 的整块里取出**提到 ``_conditions`` 的那一句**。

    ``if editing is not None: self._conditions[editing] = condition`` 这一整块的文本与源码里
    那一行不是一回事（前者带缩进与换行），而清单按源码写 —— 所以只留相关的那句。
    读（``conditions = tuple(self._conditions)``）不在这几支里，不会被取出来。
    """
    lines = [line.strip() for line in block.splitlines() if "self._conditions" in line]
    return lines[0] if lines else block.strip()


#: 唯一允许改动条件列表的那个模块（ADR 0052 / 0060）。四个字段与它们的写入口都在那里，
#: 所以「谁可以改条件」现在是一句模块名，而不再是三个文件里的六处写法。
#: ADR 0060 把它与那个只读 mixin 分开住，于是 ``window/filters.py`` 里一个都不剩。
FILTERS_MODULE = "src/gallery/ui/window/state/filters.py"

#: 那一个模块里**确实存在**的写法，逐条列出来。清单的价值是双向的：清单外多一处会红，
#: 清单里少一条（被挪走或改名了）也会红 —— 后者防的是「这条测试从此扫不到东西」。
EXPECTED_MUTATIONS = (
    # 初始化
    "self._conditions: list[SearchCondition] = []",
    # 明确是用户动作的那几件
    "self._conditions.append(condition)",  # 加一条
    "del self._conditions[position]",  # 点 ✕
    "self._conditions.pop()",  # ⇧Esc 撤一条
    "self._conditions.clear()",  # 清搜索框 / 换库
    # 就地替换：长度不变，但它确实改内容 —— 单独放行。
    "self._conditions[editing] = condition",
    # 弹掉搜索帧之后，把底下那一帧自己那份筛选整体装回（ADR 0041 决策 2）。
    # 这与「点 ✕ 撤一条」是两件事：那一条从列表里删一条，这一条装回某一帧存的那份。
    "self._conditions = list(conditions) if self._scope == SEARCH_SCOPE_LEVEL else []",
)


def test_only_the_filters_module_changes_the_conditions() -> None:
    """条件只由 ``ui/window/state/filters.py`` 改动 —— 四个字段与写入口都在那里（ADR 0052）。

    「除了手动点叉之外任何情况下条件不消失」的**可查形式**：扫出全仓库每一处改动
    ``self._conditions`` 的写法，只允许出现在那一个模块里，且只允许 :data:`EXPECTED_MUTATIONS`
    里列出的那几种。

    **为什么非要扫一遍而不靠几条行为用例**：这个 bug 的形状是「某处 ``_editing_condition``
    粘住 → 下一次提交静默替换」，而任何一条行为用例都只能盯住**它已知的那几条路径**。新加一条
    「清条件」的路（换作用域、换分组、回退到大图页）时，行为用例一条都不会红 —— 只有这份清单会。

    **扫的是 AST 而不是文本**：文档里 ``self._conditions[editing] = …`` 这样的代码片段到处都有
    （写清楚为什么是「就地替换」就得写它），按行扫会把注释与 docstring 一起算进来。
    """
    root = Path(__file__).resolve().parents[2]
    found: set[str] = set()
    elsewhere: list[str] = []
    for path in sorted((root / "src" / "gallery").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not _changes_the_conditions(node):
                continue
            # **只取「涉及它的那一句」**：``ast.unparse`` 出的是整块（含 ``if``/``else``），
            # 同一个字符串于是与源码对不上。清单按源码那一行写，逐行比才有意义。
            code = _the_line_about_conditions(ast.unparse(node))
            if relative != FILTERS_MODULE:
                elsewhere.append(f"{relative}:{node.lineno} {code!r}")
                continue
            found.add(code)

    assert not elsewhere, (
        "这些地方自己动了条件列表；四个字段与它们的写入口归 ui/window/state/filters.py 管"
        f"（ADR 0052）：\n{chr(10).join(elsewhere)}"
    )
    # 清单本身也要看着点：允许项被挪走或改名了时这里会红，而不是悄悄扫不到东西。
    assert found == set(EXPECTED_MUTATIONS), (
        f"state/filters.py 里改动条件的写法与清单对不上："
        f"多出 {sorted(found - set(EXPECTED_MUTATIONS))}，"
        f"少了 {sorted(set(EXPECTED_MUTATIONS) - found)}"
    )


def test_only_one_place_ends_a_library_search() -> None:
    """「退出整库检索」只有**一个**入口：``_leave_search_frame``。

    **清条件与弹搜索帧是同一件事的两半**，所以必须走同一个入口 —— 这条盯的是那个「半」：

    只清条件而不弹帧，眼下常常看不出差别（换浏览方式时栈已经被换根清空了，于是清条件就够了）。
    但那是**靠别人顺手清了栈**才成立的隐性耦合：谁把换根挪到条件清理之后，症状就是「条件没了、
    栈上还挂着一个什么都不接管的搜索帧」，而 Esc 弹掉的正是它 —— 用户看到的是「按 Esc 什么都没
    发生」，界面上还看不出任何异常。

    与 :func:`test_only_the_filters_module_changes_the_conditions` 的分工：那条盯「谁可以改条件」，
    这条盯「改条件时栈跟不跟得上」。

    **换库（``library_flow.py``）是另一个正当场合**：那里栈也被 ``_set_root`` 清空了，而条件是
    **上一座库的**（ADR 0026 决策 10），压根不存在「要不要弹搜索帧」这个问题 —— 整条栈都没了。
    所以它不在本条的眼里，由上一条清单管着。
    """
    source_root = Path(__file__).resolve().parents[2] / "src" / "gallery"
    assert source_root.is_dir(), f"扫的目录不存在：{source_root}"

    # **相对路径以 ``src/gallery`` 为基准**（写成 ``parents[3]`` 的话前缀会带上工作区目录名，
    # 于是 ``exempt`` 全不匹配而这条恒红 —— 与「扫零个文件而恒绿」是同一种失效，两头都要防）。
    exempt = {
        "ui/window/search_flow.py",  # 那个入口自己住在这儿
        "ui/window/library_flow.py",  # 换库：整条栈都已清空
    }
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        if relative in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "clear"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_filters"
            ):
                continue
            offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "这些地方自己清条件而没走 _leave_search_frame（栈上的搜索帧会留下）：\n"
        + "\n".join(offenders)
        + "\n退出整库检索走 SearchFlowMixin._leave_search_frame —— 它清条件、弹搜索帧、"
        "恢复底下那一层自己的筛选，并刷新面包屑。"
    )


def test_a_chip_click_is_released_by_the_next_real_action(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """三种「走开」**都要能把编辑态放掉**，而不是只保一条。

    上面几条分别钉了失焦 / 点空白 / 切视图。这里钉的是它们**共用的那一半**：三条走同一个
    :meth:`_cancel_condition_edit`，而那条路一旦被改成只认其中一种（很容易发生 —— 失焦是后加的，
    改动时最容易漏掉另外两条的语义），点空白与切视图那两条各自会变红，但**最要命的那种仍然
    守得住**：用户点完胶囊、去点了别处、再敲新条件。

    所以这里把三种走法都走一遍，每一轮都断言「敲新条件是追加、条目数 +1」。
    """
    root = _library(tmp_path, make_photo)

    def fresh(mode: str):
        window = _open(qt_app, root, tmp_path / f"会话{mode}", browse_mode=mode)
        _settle(qt_app, window)
        _filter(qt_app, window, "#相机 Canon")
        _settle(qt_app, window)
        return window

    # 每一种走法都用**一个新窗口**：上一轮留下的浏览方式会让下一轮的「切视图」变成空操作，
    # 而空操作照样绿 —— 这正是这类测试最容易自欺的地方。
    walk_aways = {
        "失焦": (BROWSE_MODE_CALENDAR, lambda w: w._grid.setFocus()),
        "点空白": (BROWSE_MODE_CALENDAR, lambda w: w._on_background_activated()),
        "切视图": (BROWSE_MODE_ALBUMS, lambda w: w.set_browse_mode(BROWSE_MODE_CALENDAR)),
    }
    for label, (mode, walk_away) in walk_aways.items():
        window = fresh(mode)
        window._on_condition_edited(0)
        _settle(qt_app, window)
        assert window._editing_condition == 0, f"{label}：前提不成立，没进编辑态"

        walk_away(window)
        _settle(qt_app, window)
        assert window._editing_condition is None, f"{label}：走开之后还挂在编辑态"

        _filter(qt_app, window, "#城市 西安")
        assert len(_chips(window)) == 2, f"{label}：敲新条件把原来的顶掉了 → {_chips(window)}"
        window.close()


def test_leaving_the_search_field_abandons_the_pending_change(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """**焦点离开输入框 = 放弃**改这一条（ADR 0040 决策 12）。

    用户要的就是这一条：「避免用户做了很多别的操作后忘记之前进了编辑模式」。回退挂在
    ``editing_changed(False)`` 上而不是焦点事件上 —— 离屏下量不出焦点的先后顺序，而信号是
    「焦点已经离开」这件事本身。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    window._on_condition_edited(0)
    _settle(qt_app, window)
    assert window._editing_condition == 0, "前提不成立：没进编辑态"

    window._grid.setFocus()  # 焦点离开输入框
    _settle(qt_app, window)

    assert window._editing_condition is None, "失焦之后还挂在编辑态"
    assert _chips(window) == ["相机：Canon"], f"放弃修改不该动条件本身：{_chips(window)}"
    window.close()


def test_leaving_the_field_while_typing_a_new_one_keeps_what_was_typed(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """**正在输入一条全新的条件**时失焦，输入框里敲的字要留着。

    这一条是上一条的必要反面：放弃修改的前置条件若不区分「在改已有的一条」与「在敲新的一条」，
    用户敲到一半点别处，**刚敲的字凭空消失** —— 丢的是没确认的东西。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    assert window._editing_condition is None, "前提不成立：上一条条件残留着编辑态"

    window.focus_search()
    window._search_field._edit.setText("#城市 西安")  # 敲到一半
    qt_app.processEvents()

    window._grid.setFocus()  # 点别处
    _settle(qt_app, window)

    assert window._search_field.text() == "#城市 西安", (
        f"敲到一半的字被清掉了：{window._search_field.text()!r}"
    )
    window.close()


def test_clicking_the_blank_abandons_the_pending_change(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """点网格空白处 = 放弃改这一条（ADR 0040 决策 12）。

    与 :meth:`_on_background_activated` 里那个 ``is_renaming()`` 的早退同一位置、同一理由：
    「改到一半」这个状态下，用户的这一下先归输入框，不顺手把人带走。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    child = root / "海边"
    window.enter_folder(child)
    _settle(qt_app, window)
    window._on_condition_edited(0)
    _settle(qt_app, window)
    assert window._editing_condition == 0, "前提不成立：没进编辑态"

    window._on_background_activated()
    _settle(qt_app, window)

    assert window._editing_condition is None, "点空白没有放弃修改"
    assert _chips(window) == ["相机：Canon"], f"放弃修改不该动条件本身：{_chips(window)}"
    window.close()


def test_switching_the_view_keeps_the_conditions_but_drops_the_pending_change(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切浏览方式：**条件跟着走，改到一半那一次不跟着走**（ADR 0040 决策 12）。

    条件跟着走是 ADR 0040 决策 1，编辑态不跟着走是这一条。只断前半句的话，「换了视图再敲一条」
    顶掉的就是上一个视图里的那一条。
    """

    def _no_cancel_on_blur(self: MainWindow) -> None:
        """把**失焦那条回退**按掉 —— 只让 ``set_browse_mode`` 里的那一处有机会干活。

        **按的是 :meth:`_on_search_active_changed` 而不是 :meth:`_cancel_condition_edit` 本身**：
        后者会把 ``set_browse_mode`` 里那一处也一起按掉（它调的正是这个方法）。按「失焦的入口」
        才真的只关掉一条路。
        """
        self._update_action_states()

    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    window._on_condition_edited(0)
    _settle(qt_app, window)

    # 把失焦那条回退彻底按掉，只让 ``set_browse_mode`` 里那一处有机会干活：否则切视图时
    # ``focus_current_view`` 会顺手清掉编辑态，把那一处删掉本用例照样绿。
    monkeypatch.setattr(MainWindow, "_on_search_active_changed", _no_cancel_on_blur)
    window._filters.begin_edit(0)
    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    qt_app.processEvents()

    assert _chips(window) == ["相机：Canon"], f"切视图不该动条件：{_chips(window)}"
    assert window._editing_condition is None, "切视图没有放弃修改"
    window.close()


def test_switching_the_view_keeps_the_conditions_and_appends_the_next_one(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """切视图之后敲的新条件是**追加** —— 与上一条一起把「切视图」这半边锁定。

    上一条按掉了失焦那条回退，所以它量的是「``set_browse_mode`` 自己有没有放弃」；这一条走真实
    路径，量的是**最终结果**：条件还在、新敲的接在后面而不是顶掉它。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    window._on_condition_edited(0)
    _settle(qt_app, window)

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#城市 西安")

    assert _chips(window) == ["相机：Canon", "城市：西安"], (
        f"切视图之后新敲的顶掉了原来的：{_chips(window)}"
    )
    window.close()


def test_the_conditions_follow_the_user_onto_the_calendar(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """条件可以**在网格页上改**，改完切到日历页要立刻看见那一份。

    钉的是「判据是条件现在是什么样，而不是日历页此刻在不在线」—— 条件可能是在别处改的
    （例如从「全部照片」切过来之前），而日历那一页的工具条要跟着。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    chips = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(chips) == 1, "在网格页上加的条件，切到日历页不见了"
    window.close()


def test_removing_a_condition_from_the_calendar_works(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在日历工具条上点 ✕ 撤掉一条 —— 那几枚不是摆设。

    走的是**同一个** ``_on_condition_removed``（两个控件接到同一个处理函数上），所以
    「日历上撤掉了、网格页上还留着」这种不一致不可能发生。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)
    assert len(window._calendar_page._bar._chips.findChildren(ChipButton)) == 2

    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == ["城市：西安"], "日历上撤掉了，条件列表里还在"
    remaining = window._calendar_page._bar._chips.findChildren(ChipButton)
    assert len(remaining) == 1, "日历工具条上还留着撤掉的那一枚"
    window.close()


def test_switching_the_calendar_level_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """`Y` / `M` / `W` 与筛选互不干扰：条件跟着走，切层不重算命中。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window._calendar_page.set_level("month")
    qt_app.processEvents()
    window._calendar_page.set_level("year")
    qt_app.processEvents()
    window._calendar_page.set_level("month")
    qt_app.processEvents()
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert window._calendar_page.photo_count() == 1, "切到年再切回月，筛选丢了"
    window.close()


def test_switching_the_browse_mode_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """换浏览方式**保留**条件（ADR 0040 决策 1）—— 整库检索在这一步会清掉它们。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    assert _names(window) == ["海边-01.jpg"]

    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "换浏览方式把条件清掉了"
    assert window._calendar_page.photo_count() == 1

    window.set_browse_mode(BROWSE_MODE_FLAT)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_entering_an_album_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """进相册保留条件 —— 它收窄的正是「当前这一层」，所以要跟着走进那一层。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "进相册把条件清掉了（那是整库检索的行为）"
    # 那一层里唯一一张是 Sony，Canon 在根下 —— 于是这里该是空的。
    assert _names(window) == []
    window.close()


def test_entering_a_group_keeps_the_filter(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """进出分组同样保留条件 —— 分组也是「这一层」。

    **``go_up`` 退出分组，条件一条都不动**（ADR 0040 决策 11）：按 Esc 退一层，看到的必须是
    「我退了」，而不是「我撤了一条条件」—— 那两件事在屏幕上分不出来。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)
    _filter(qt_app, window, "jpg")

    window.go_up()
    _settle(qt_app, window)

    assert window._group_view is None, "Esc 退分组没退出去"
    assert _chips(window) == ["文件名：jpg"], "Esc 顺手撤掉了一条条件 —— 它只该管位置"
    window.close()


# ---- 相册卡片按子树收留（ADR 0040 决策 3）----------------------------------------


def test_only_the_albums_with_a_hit_stay(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """相册模式下：**不含命中的子相册卡片消失**，含命中的留下当导航。

    卡片不是照片，所以一条都不命中照片的子相册不该摆出来；而摆着一排点进去全是空的卡片，
    等于骗用户白点一次。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    before = {entry.name for entry in window._model.entries()}
    assert {"旅行", "家庭"} <= before

    _filter(qt_app, window, "#相机 Sony")

    # **这一层是库根**：命中的是 ``旅行/2024/山.jpg``，它在卡片那一层之下，所以照片本身
    # 不出现在这一层里 —— 这一层摆的是「卡片」。卡片留不留才是这一条要断的事。
    names = {entry.name for entry in window._model.entries()}
    assert "家庭" not in names, "这个子相册里一张都没有，卡片不该摆着"
    assert "旅行" in names, "命中的照片在它子树里，卡片要留着当导航"
    window.close()


def test_the_album_card_goes_away_when_its_hit_is_beneath_it(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """命中在**更深处**时，祖先那张卡片照样留着 —— 卡片的含义是它的整棵子树。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)

    window.enter_folder(root / "旅行")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Sony")

    # 在 ``旅行`` 这一层里，命中的是 ``旅行/2024/山.jpg`` —— 它在下一层里。
    names = {entry.name for entry in window._model.entries()}
    assert names == {"2024"}, f"只剩下一层的卡片不对：{names}"
    window.close()


def test_the_photos_of_this_level_survive_even_with_no_card(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """命中的照片就在**这一层**上时它照旧留着 —— 它不归属任何子相册卡片。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    names = {entry.name for entry in window._model.entries()}
    assert "海边-01.jpg" in names
    assert "海边-02.jpg" not in names, "它没写相机，不该命中"
    window.close()


# ---- 撤销与清空（与整库检索同一套心智）--------------------------------------------


def test_escape_exits_the_search_after_walking_through_a_group(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """搜索结果里按 ``Esc`` 能退出搜索 —— **进过分组再出来也一样**。

    根因不是「Esc 在分组里不管用」，而是搜索结果压根不在 ``go_up`` 的那一串 ``if`` 里 ——
    它既不是文件夹也不是分组。「搜索后直接按 Esc 能退出」是**巧合**：那时焦点还在搜索框里，
    走的是搜索框自己的 Esc（``dismiss`` → ``pop_last_condition``）；进一趟分组，焦点回到网格，
    那条路就断了。

    钉的是「**往返一次之后**仍然退得回去」：只断「刚搜完按 Esc」的话，把 ``go_up`` 写成
    「有搜索帧就清条件」也能过 —— 那样弹掉分组弹的是分组，搜索还留着。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "海边", scope=SEARCH_SCOPE_LIBRARY)
    _settle(qt_app, window)
    assert window._can_go_up() is True, "前提不成立：搜索结果里本来就退不出去"

    # 往返一次分组：只断「刚搜完」的话，弹掉分组弹的是分组，搜索帧还留着。
    photo = PhotoRef(path=root / "海边-01.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)
    assert window._group_view is not None, "前提不成立：没进到分组里"
    window.go_up()
    _settle(qt_app, window)
    assert window._group_view is None, "前提不成立：没退出分组"

    assert window._can_go_up() is True, "进出一次分组之后就再也退不出搜索了"

    window.go_up()
    _settle(qt_app, window)

    assert _chips(window) == [], f"Esc 没能退出搜索，条件还在：{_chips(window)}"
    # 退出搜索之后停在**相册视图的根**。断的是「回到了这一层」而不是「那一层恰好有哪些条目」
    # —— 根上既有相册卡片也有散在根下的照片，硬编码那份清单就等于把断言骑在测试库的目录结构上。
    assert window._level_stack.frames == (), (
        f"退出搜索之后栈上还有东西：{window._level_stack.frames}"
    )
    assert "家庭" in _names(window) and "旅行" in _names(window), (
        f"退出搜索之后没有退回相册根：{_names(window)}"
    )
    assert not window._is_library_search(), "退出搜索之后仍说自己在整库检索"
    window.close()


#: 「从哪一档换到哪一档」——**三档两两之间的转移全列**，而不是只从相册出发。只断那一条转移会漏掉
#: 两件事：一是 ``set_browse_mode`` 撞上当前这一档会**早退**（什么也不做），二是「从日历切回相册」
#: 走的是另一条分支，同一个漏掉的刷新。
ALL_BROWSE_TRANSITIONS = [
    (BROWSE_MODE_ALBUMS, BROWSE_MODE_CALENDAR),
    (BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT),
    (BROWSE_MODE_CALENDAR, BROWSE_MODE_ALBUMS),
    (BROWSE_MODE_CALENDAR, BROWSE_MODE_FLAT),
    (BROWSE_MODE_FLAT, BROWSE_MODE_ALBUMS),
    (BROWSE_MODE_FLAT, BROWSE_MODE_CALENDAR),
]

_BROWSE_NAMES = {
    BROWSE_MODE_ALBUMS: "相册",
    BROWSE_MODE_FLAT: "全部照片",
    BROWSE_MODE_CALENDAR: "日历",
}


@pytest.mark.parametrize(("frm", "to"), ALL_BROWSE_TRANSITIONS)
def test_switching_the_view_leaves_no_condition_chip_behind(
    qt_app: QApplication, tmp_path: Path, make_photo, frm: str, to: str
) -> None:
    """换浏览方式之后，**界面上不许还挂着上一层的条件胶囊**。

    **模型早就清了，界面没清** —— 典型的是「状态改了但没人去刷新那一行」，与栈无关
    （切到日历时栈已经是空的了）。

    真因：:meth:`MainWindow._sync_calendar_conditions` 是**唯一**摆这两处胶囊的地方，而它只由
    :meth:`MainWindow._update_breadcrumb` 调；后者又只由 :meth:`MainWindow._reload_level` 调。
    ``set_browse_mode`` 的三条分支里**只有「切到相册」那条**经过 ``_reload_level``，另两条走
    ``_enter_calendar`` 与直接换页 —— 于是胶囊留在屏幕上，要来回切一次才消失。

    **六个转移全断**：只断「相册→日历」的话，「切到全部照片也残留」正好漏过去 —— 而它恰恰
    说明病根不在日历上，而在「谁负责刷新」。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=frm)
    try:
        _settle(qt_app, window)
        _filter(qt_app, window, "#相机 Canon", scope=SEARCH_SCOPE_LIBRARY)
        _settle(qt_app, window)
        assert _chips(window) == ["相机：Canon"], "前提不成立：没搜出条件来"
        where = f"{_BROWSE_NAMES[frm]}→{_BROWSE_NAMES[to]}"

        window.set_browse_mode(to)
        _settle(qt_app, window)

        assert _chips(window) == [], f"{where} 之后模型里还留着条件"
        assert _calendar_chips(window) == [], (
            f"{where} 之后日历工具条上还挂着胶囊：{_calendar_chips(window)}"
        )
        assert not any(text.endswith("✕") for text in _trail(window)), (
            f"{where} 之后面包屑上还挂着条件：{_trail(window)}"
        )
    finally:
        window.close()


def test_escape_leaves_the_folder_without_touching_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在子相册里按 `Esc`：**退出去，条件一条都不少** —— 相册与日历**两边都**有。

    根因是判据的**顺序**：条件排在文件夹 / 那一格之前（ADR 0026 决策 10「条件又深一级」）的话，
    只要有条件就永远退不出位置 —— 按 Esc 看到的是一条条件被撤掉而**位置纹丝不动**，而那两件事在
    屏幕上分不出来。

    量的是**位置真的退了**与**条件真的没少**：只断一条的话，另一条坏掉都没人看着。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)
    child = root / "海边"
    window.enter_folder(child)
    _settle(qt_app, window)
    assert window._current_dir == child, "前提不成立：没进到子相册里"
    assert _chips(window) == ["相机：Canon", "城市：西安"]

    window.go_up()
    _settle(qt_app, window)

    # ``_current_dir`` 是栈的投影、**在库根时是 ``None``**；被各处真正用的是
    # :meth:`_current_folder`（口径 ``_current_dir or root``），所以断它。
    assert window._current_folder() == root, "Esc 没能退出子相册"
    assert _chips(window) == ["相机：Canon", "城市：西安"], (
        f"Esc 顺手撤掉了条件：{_chips(window)} —— 它只该管位置"
    )
    window.close()


def test_escape_returns_to_the_calendar_without_touching_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历里点开一格之后按 `Esc`：**退回日历，条件一条都不少**。

    与相册那条同一个根因（判据顺序），但**这条路要单独钉**：日历那一格走的是
    ``_drill_photos``，相册走的是 ``_current_dir``，两条独立的分支 —— 只钉一条另一条就没人看着。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    window._enter_calendar_drill([], "3 天前")
    _settle(qt_app, window)
    assert window._drill_photos is not None, "前提不成立：没点开那一格"

    window.go_up()
    _settle(qt_app, window)

    assert window._drill_photos is None, "Esc 没能退回日历"
    assert _chips(window) == ["相机：Canon"], (
        f"Esc 顺手撤掉了条件：{_chips(window)} —— 它只该管位置"
    )
    window.close()


def test_escape_does_nothing_when_there_is_nowhere_to_go(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在最外层（有条件、没进任何东西）按 `Esc`：**什么也不做**，也不撤条件。

    **这一条钉的是「退不动就安静地不动」**：撤条件那条路挪到 ``⇧Esc`` 之后，``Esc`` 就没有
    「顺手做点什么」的理由了。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    assert window._can_go_up() is False, "前提不成立：这一层其实退得回去"

    window.go_up()
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "退不动的时候 Esc 撤掉了一条条件"
    window.close()


def test_shift_escape_is_the_key_that_pops_a_condition(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """``⇧Esc`` 这一键**真的按得动** —— 钉的是键，不只是那个方法。

    **量的是按键事件而不是直接调方法**：上面几条走的是 ``pop_last_condition()``，那与「这个键
    接上了没有」是两件事 —— 快捷键没挂上、上下文写错、或者被输入框先忽略掉，症状都是「按了没反应」，
    而上面几条照样绿。

    按键发在**网格**上（条件生效时焦点通常在这儿），不在输入框里。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)
    assert len(_chips(window)) == 2, "前提不成立：两条条件没加上"

    QTest.keyClick(window._grid, Qt.Key.Key_Escape, Qt.KeyboardModifier.ShiftModifier)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], f"⇧Esc 没能撤掉最后一条：{_chips(window)}"
    window.close()


def test_shift_escape_removes_the_conditions_one_by_one(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """``⇧Esc`` 逐条撤条件，撤光之后这一层恢复完整 —— 一个动作都不多。

    **这一条从 ``Esc`` 挪到了 ``⇧Esc``**（ADR 0040 决策 11，用户选定的）：Esc 退位置而不再撤条件。

    命中的那张是 ``海边-01.jpg``（``CAMERAS`` 里 Canon 是它、山是 Sony）—— 断言里的名字是**照着
    模块那份对照表抄的**，不是凭印象。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    assert _names(window) == ["海边-01.jpg"], "前提不成立：条件没生效"

    window.pop_last_condition()
    _settle(qt_app, window)

    assert _chips(window) == []
    assert _names(window) == ["山.jpg", "海边-01.jpg", "海边-02.jpg"]
    window.close()


def test_clearing_every_condition_restores_the_level(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.clear_search_conditions()
    _settle(qt_app, window)

    assert _chips(window) == []
    assert len(_names(window)) == 3
    # 条件空了，菜单里那一条**整个消失**（与整库检索同一套，ADR 0014 决策 4）。
    assert window._clear_search_action.isVisible() is False
    window.close()


# ---- 面包屑（ADR 0040 决策 4）----------------------------------------------------


def test_the_breadcrumb_keeps_the_path_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选时面包屑**照旧报完整路径** —— 范围没变，藏掉路径是在说一件不存在的事。

    与 :func:`tests.ui.test_search_flow.test_the_breadcrumb_says_the_search_spans_the_library`
    互为反证：整库检索时报库名（范围真的变了），筛选时报路径（范围没变）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)
    assert _trail(window) == ["照片", "旅行", "2024"]

    _filter(qt_app, window, "#相机 Sony")

    trail = _trail(window)
    assert trail[:3] == ["照片", "旅行", "2024"], f"路径段不见了：{trail}"
    assert any("筛选后（1）" in part for part in trail), trail
    window.close()


def test_the_breadcrumb_still_drops_the_group_while_searching(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """整库检索仍然只报库名 —— 两种作用域各写各的，一条都没被顺手改掉。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)

    _filter(qt_app, window, "jpg", scope="library")

    trail = _trail(window)
    assert trail[0] == "照片"
    assert "Canon" not in trail, f"整库检索不该报分组：{trail}"
    assert any("搜索结果" in part for part in trail), trail
    window.close()


def test_the_breadcrumb_keeps_the_group_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选时那一组**说的是真的** —— 它就是这一层的范围，该报。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    photo = PhotoRef(path=root / "旅行" / "2024" / "山.jpg", size=1, mtime_ns=1)
    window.enter_group(GroupRef(facet="camera", key="canon", label="Canon", photos=(photo,)))
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Sony")

    trail = _trail(window)
    assert "Canon" in trail, f"筛选时该报当前那一组：{trail}"
    assert _names(window) == ["山.jpg"]
    window.close()


# ---- 切换按钮与作用域（ADR 0040 决策 5、6）----------------------------------------


def test_the_button_switches_the_scope_and_the_words(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """按钮一按：占位文字与菜单文案一起换。**这几处说同一句话**，否则用户会以为是两个设置。

    按钮**按一次掉个头**（ADR 0042 决策 1）：它是动作，不是可勾选的开关，所以这里发的是真的
    ``click`` 而不是 ``setChecked``。**要开着库** —— 没有库时按钮是置灰的
    （``_update_action_states``），而置灰的按钮 ``click()`` 什么也不发，于是对着空窗口按一下
    量到的是「没反应」。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_SEARCH

    window._scope_toggle.click()
    qt_app.processEvents()

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
    assert window._search_action.text() == "筛选…"
    assert window._clear_search_action.text() == "清除筛选条件"
    window.close()


def test_pressing_the_button_twice_comes_back(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """**按两下回到原处** —— 「掉个头」是这个意思，而一个只进不退的开关就不是。

    这条护的是「按钮不留在任何一态上」：它自己不记状态，第二次按下去时外面按当前作用域算，
    于是两头都回得去。**按的是真的按钮**，所以它同时断「信号接上了、按钮没被置灰」。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    window._scope_toggle.click()
    qt_app.processEvents()
    assert window._search_scope == SEARCH_SCOPE_LEVEL

    window._scope_toggle.click()
    qt_app.processEvents()
    assert window._search_scope == SEARCH_SCOPE_LIBRARY
    window.close()


def test_the_icon_is_smaller_than_the_button(qt_app: QApplication, tmp_path: Path) -> None:
    """图标**不许画满整个按钮** —— 22 点画布上画 22 点的东西会顶到边框上。

    钉的是「图标比按钮小」这个关系，不是某个数：真要盯住的是图标画布与按钮边长之间留着
    余量，而两者都由 :data:`~gallery.ui.window.widgets.search_field.TOGGLE_ICON_EDGE` 与
    :data:`~gallery.ui.window.widgets.search_field.TOGGLE_EDGE` 推出来 ——
    有人把图标又改回跟着按钮走时，
    这条会立刻变红。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    button_edge = window._scope_toggle.width()
    pixmap = window._scope_toggle.icon().pixmap(button_edge, button_edge)
    icon_edge = pixmap.width() / (pixmap.devicePixelRatio() or 1.0)

    assert icon_edge < button_edge, (
        f"图标 {icon_edge:g} 点画在 {button_edge:g} 点的按钮里 —— 顶到边框上了"
    )
    # 那一档不是随手定的：标题条上另外两个图标（放大镜、外部应用）都是 13。
    assert icon_edge == ICON_EDGE == EXTERNAL_ICON_EDGE
    window.close()


def test_the_icon_is_two_arrows_pointing_opposite_ways(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """图标是**上下两支反向的横向箭头**（上面那支朝左、下面那支朝右），不是漏斗。

    量的是**墨迹落在哪一侧**：箭头那一头多出分叉的两笔，于是「箭头所在的那一侧」一定更重。
    只断「画布上有墨」量不到朝向 —— 两支都朝右也是满画布的墨，断出来照样绿。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    pixmap = window._scope_toggle.icon().pixmap(TOGGLE_ICON_EDGE, TOGGLE_ICON_EDGE)
    image = pixmap.toImage()
    assert not image.isNull(), "按钮上根本没有图标"
    top_left, top_right, bottom_left, bottom_right = _quadrant_weight(image)

    assert top_left > top_right, f"上面那支箭头不是朝左的：{_quadrant_weight(image)}"
    assert bottom_right > bottom_left, f"下面那支箭头不是朝右的：{_quadrant_weight(image)}"
    window.close()


def test_the_arrows_stay_narrower_than_the_magnifier(qt_app: QApplication) -> None:
    """两支箭头叠在一起的墨迹**不许比放大镜更宽** —— 两份图形叠在 13 点画布里，占满就
    读成「图标被框住了」。

    量的是**墨迹的包围盒**而不是画布：两枚图标的画布一样是 13，量画布量不出这件事。
    """
    app_theme = theme.current_theme()
    arrows_box = _ink_box(scope_arrows(app_theme, 1.0))
    magnifier_box = _ink_box(magnifier(app_theme, 1.0))

    assert arrows_box[2] - arrows_box[0] <= magnifier_box[2] - magnifier_box[0], (
        f"两支箭头的墨迹 {arrows_box} 比放大镜的 {magnifier_box} 还宽"
    )


def test_the_outline_has_no_darker_dots_at_the_eight_divisions(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """轮廓上**那八个等分点不许比别处更深**（圆弧与直边相接的四处 + 每段圆弧的 45°）。

    Qt 把样式表那一圈描成「四段直边 + 四段圆弧」，接缝处两半的抗锯齿重叠；而输入面这一族的
    边框是半透明的（``text_at(0.28)``），重叠就叠成更深的点 —— 落在那八个位置上，用户一眼
    就看见轮廓上「脏」了八个点。实测：轮廓中位 184，那八处 133（255 × 0.72² = 132，
    正是同一层边框被调了两遍）。

    **量的是那一圈本身**：胶囊里还坐着放大镜、外面还有底色，量别处量不到这件事。

    量法在 :mod:`tests.ui.outline_helpers`（三处都要量它，只有那一份）；浮层那两处的同类
    断言在 ``tests/ui/test_menu_style.py``。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    fill = field_background(theme.current_theme())
    background = (fill.red() + fill.green() + fill.blue()) // 3
    for widget in (window._search_field, window._scope_toggle):
        assert_no_darker_dots(widget, background)
    window.close()


def _ink_box(pixmap: QPixmap, threshold: int = 24) -> tuple[int, int, int, int]:
    """着墨像素的包围盒（左、上、右、下）。

    阈值 24 而不是「非全透明」：抗锯齿边缘那圈淡到几乎看不见，把它算进去等于把整张画布
    都算成有墨，这条断言就成了恒真。
    """
    image = pixmap.toImage()
    points = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > threshold
    ]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    assert points, "这张图上一个像素都没着墨"
    return min(xs), min(ys), max(xs), max(ys)


def _quadrant_weight(image: QImage) -> tuple[int, int, int, int]:
    """画布四象限各有多少个着墨的像素（左上、右上、左下、右下）。

    阈值取 24 而不是「非全透明」：抗锯齿边缘的像素淡到几乎看不见，把它们算进去等于把
    每一象限都摊成同一个数，这条断言就恒真了。
    """
    width, height = image.width(), image.height()
    counts = [0, 0, 0, 0]
    for y in range(height):
        for x in range(width):
            if image.pixelColor(x, y).alpha() > 24:
                counts[(0 if x < width // 2 else 1) + (0 if y < height // 2 else 2)] += 1
    return counts[0], counts[1], counts[2], counts[3]


def test_the_button_has_no_hover_hint(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """按钮**没有悬浮提示**，切过一轮之后仍然没有。

    它是一次性动作，而「此刻是哪一头」紧挨着就写在胶囊的占位文字里（「搜索照片…」/
    「筛选照片…」）—— 再挂一句提示是把同一件事在两处说，而按钮自己**根本没有状态可说**
    （ADR 0042 决策 2）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    assert window._scope_toggle.toolTip() == ""
    window._scope_toggle.click()
    qt_app.processEvents()
    assert window._scope_toggle.toolTip() == "", "切过去之后提示又回来了"
    window.close()


def test_the_button_stays_the_same_in_both_scopes(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """两种作用域下按钮**长得一模一样** —— 回归测试「它没有选中态」（ADR 0042 决策 1）。

    **量像素，不量样式表字符串**：只断 ``"background"`` 出现过量不到任何东西 —— 颜色写对了而
    Qt 没画（或者被别的规则盖掉）时它照样绿。这条量的是「按钮中心那一块到底是什么颜色」，
    并且**两种作用域各量一次**。

    为什么这一条要紧：一个可勾选的按钮，用户会盯着它判断「现在是筛选还是搜索」；而这一枚按下去
    就不留在任何一态上（占位文字与菜单里那一项的勾才说当前是哪一头），留着填充色就是在骗人 ——
    按完之后按钮亮着，而下一次按它到底是切过去还是切回来就说不清了。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    toggle = window._scope_toggle

    in_library = _button_center_color(toggle)
    toggle.click()
    qt_app.processEvents()
    in_level = _button_center_color(toggle)

    assert in_library is not None and in_level is not None
    # 回退：两档真的切开了（按钮没被置灰）。少了这一行，两次量到同一个状态也会照样绿。
    assert window._search_scope == SEARCH_SCOPE_LEVEL
    assert _close(in_library, in_level, 0.01), (
        f"按钮的中心在两档之间变了：{in_library.name()} → {in_level.name()}"
        "（这一枚是一次性动作，不摆状态）"
    )
    # 两档都不该是高亮色：那一片颜色是弹出菜单里「选中项」在用的（``menu_style`` 的
    # ``:selected``），按钮借用它就是在冒充一个可勾选项。
    highlight = QPalette(QApplication.instance().palette()).color(QPalette.ColorRole.Highlight)
    assert not _close(in_level, highlight, 0.06), (
        f"筛选那一档填的是高亮色 {in_level.name()} —— 按钮不该有选中态"
    )
    window.close()


def _button_center_color(button: object) -> QColor | None:
    """按钮**中心那一块**画出来的颜色。

    **中心而不是四角**：圆角按钮的四角是圆角外的透明区，量那里量到的是父控件的底色，于是
    「有没有填满」与「圆角画得对不对」两件事会搅在一起。中心是任何圆角半径下都必然被填到的那
    一块。

    用 ``render()`` 而不是屏幕像素：它会**强制**重画，所以量到的是此刻真的会被画出来的东西
    （对照 ``PhotoGrid`` 那条教训：屏幕上的陈旧像素在 ``grab()`` 里看不见）。

    ``render(None)`` 返回 ``None``（Qt 在没有目标矩形时不给画布），所以走
    :meth:`QWidget.grab` 之前先确认控件自己已经有一个尺寸 —— 离屏下没跑过布局时它是 640×480
    的默认值，中心那一块量的就不是这枚按钮了。
    """
    size = button.size()  # type: ignore[attr-defined]
    if size.width() < 2 or size.height() < 2:
        return None
    image = button.grab().toImage()  # type: ignore[attr-defined]
    if image.isNull() or image.width() < 2:
        return None
    return image.pixelColor(image.width() // 2, image.height() // 2)


def _close(left: QColor, right: QColor, tolerance: float) -> bool:
    """两个颜色差得不算远（``lightnessF`` 上的差，阈值硬编码一个 ``tolerance``）。"""
    return abs(left.lightnessF() - right.lightnessF()) <= tolerance and (
        abs(left.red() - right.red()) <= 2
        and abs(left.green() - right.green()) <= 2
        and abs(left.blue() - right.blue()) <= 2
    )


def test_the_file_menu_no_longer_offers_the_scope(qt_app: QApplication, tmp_path: Path) -> None:
    """作用域只从胶囊右边那一枚按钮切换（ADR 0069）。

    菜单里不再摆那个勾选项，「此刻是哪一头」只说在胶囊的占位文字里。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    file_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "文件" and action.menu() is not None
    )
    assert "筛选当前视图" not in [action.text() for action in file_menu.actions()]
    window.close()


def test_switching_the_scope_keeps_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """切作用域**不清条件**：同一个胶囊里的同一批条件，用户改的只是「它们管到哪儿」。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon", scope="library")
    assert _names(window) == ["海边-01.jpg"]

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"], "切作用域把条件清掉了"
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_the_scope_is_remembered_per_library(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """作用域是**库级**偏好（ADR 0040 决策 6）：重开这个库还该是筛选。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    qt_app.processEvents()
    window.close()

    again = _open(qt_app, root, tmp_path / "会话", browse_mode=None)
    _settle(qt_app, again)

    assert again._search_scope == SEARCH_SCOPE_LEVEL
    # 按钮不再摆状态（ADR 0042 决策 1），所以「记住了」只能从**胶囊那句话**上看。
    assert again._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
    again.close()


def test_each_library_keeps_its_own_scope(qt_app: QApplication, tmp_path: Path, make_photo) -> None:
    """A 库用筛选不该传染给 B 库 —— 与分组、排序那几项同一套（ADR 0038 决策 4）。"""
    first = _library(tmp_path / "A", make_photo)
    second = _library(tmp_path / "B", make_photo)
    window = _open(qt_app, first, tmp_path / "会话")
    _settle(qt_app, window)

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    qt_app.processEvents()
    window.open_library(second)
    _settle(qt_app, window)

    assert window._search_scope == "library", "B 库被 A 库的用法传染了"
    window.close()


# ---- 层标识（ADR 0040 决策 2）------------------------------------------------------


def test_a_filtered_level_is_not_a_search_level(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """「加一条条件」要算**换了层** —— 否则焦点恢复会往一个已被滤掉的行上跳。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    before = window._current_level_key()

    _filter(qt_app, window, "#相机 Canon")
    filtered = window._current_level_key()

    assert filtered != before
    _filter(qt_app, window, "#相机 Canon", scope="library")
    searched = window._current_level_key()

    assert searched != filtered, "筛选层与整库检索层撞成了同一个标识"
    window.close()


# ---- 筛选时让路的命令（ADR 0040 决策 7）--------------------------------------------


@pytest.fixture
def pasteable_clipboard(qt_app: QApplication, tmp_path: Path) -> Iterator[None]:
    """剪贴板里**真有文件**。

    ``_can_paste`` 会真去读一次剪贴板（读一次约 1 微秒），所以空剪贴板下 ⌘V 本来就一直是灰的
    —— 那会让「筛选把它关掉了」这条断言变成**空转**：前后都灰，红了也看不出是哪一句的功劳。
    """
    from gallery.ui import desktop

    desktop.copy_files_to_clipboard([tmp_path / "待粘.jpg"])
    qt_app.processEvents()
    try:
        yield
    finally:
        QGuiApplication.clipboard().clear()


def test_writing_commands_are_withheld_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo, pasteable_clipboard: None
) -> None:
    """筛选生效时 ⌘V 与 ⇧⌘N **禁掉**：这一层显示的不是全部内容，粘进去的可能一条都不出现。

    而且它们是会动用户文件的那一类（不删除、不移动用户照片那条），不该在用户看不清全貌时发生。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    assert window._paste_action.isEnabled() is True, "前提不成立：⌘V 本来就按不动"
    assert window._new_album_action.isEnabled() is True

    _filter(qt_app, window, "#相机 Canon")

    assert window._paste_action.isEnabled() is False, "筛选时还让粘贴，用户粘进去的照片不会显示"
    assert window._new_album_action.isEnabled() is False
    window.close()


def test_writing_commands_come_back_when_the_filter_is_cleared(
    qt_app: QApplication, tmp_path: Path, make_photo, pasteable_clipboard: None
) -> None:
    """撤光条件后那两条**立刻回来** —— 不许留在关着的状态上（用户看不出为什么按不动）。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window.clear_search_conditions()
    _settle(qt_app, window)

    assert window._paste_action.isEnabled() is True
    assert window._new_album_action.isEnabled() is True
    window.close()


# ---- 空层与索引没建（两个假阴性陷阱，ADR 0040 决策 3）-----------------------------


def test_filtering_before_the_index_is_built_still_finds_things(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """用户看到空结果就再也不会筛选第二次了 —— 与整库检索同一道前置条件（ADR 0026 决策 5）。

    文件表一行都没有时一条都命中不了，而用户看到的会是「当前这一层里没有符合这些条件的照片」
    —— 那不是答案，是索引还没建。那一遍跑完要自己把这次筛选补上。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    wait_for_idle(qt_app, window, also=("_search_job", "_filter_job", "_staged"), what="扫描")

    window.set_search_scope(SEARCH_SCOPE_LEVEL)
    window.focus_search()
    qt_app.processEvents()
    window._search_field._edit.setText("#相机 Canon")
    window._search_field.submit()
    _settle(qt_app, window)

    assert _chips(window) == ["相机：Canon"]
    assert _names(window) == ["海边-01.jpg"]
    window.close()


def test_an_empty_filtered_level_says_which_layer_is_empty(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """空层提示说的是「**这一层**里没有」而不是「这些条件下没有」—— 后者在筛选下是骗人的。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    window.enter_folder(root / "旅行" / "2024")
    _settle(qt_app, window)

    _filter(qt_app, window, "#相机 Canon")

    assert _names(window) == []
    assert "这一层" in window._banner.text(), window._banner.text()
    window.close()


# ---- 监听器（ADR 0040 决策 6）------------------------------------------------------


def test_a_new_photo_shows_up_only_if_it_matches(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """筛选中来了新照片：命中的留下，不命中的不进这一层。

    走的是「重跑一遍」那条路（与整库检索同一处），所以这条量的是**结果对不对** —— 增量分支
    本来就不该存在（另写一份正是 ADR 0026 决策 7 点名的 bug 来源）。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    assert _names(window) == ["海边-01.jpg"]

    make_photo("新来的.jpg", directory=root / "旅行" / "2024")
    window._on_watch_changed(_added_change(root / "旅行" / "2024" / "新来的.jpg"))
    _settle(qt_app, window)

    assert _names(window) == ["海边-01.jpg"], "没写相机的新照片不该出现在 Canon 筛选里"
    window.close()


def _added_change(path: Path) -> tuple:
    """一次「新建了文件」的监听变化（测试自己造，不起真的监听器）。"""
    from gallery.core.library.watcher import ChangeKind, LibraryChange

    change = LibraryChange(kind=ChangeKind.ADDED, path=path)
    return (change,)


def test_the_condition_chips_stay_clickable_while_filtering(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """面包屑右端那几枚胶囊**照旧能点**（改 / 撤掉），否则筛选就没有出口了。"""
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话")
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")

    window._on_condition_removed(0)
    _settle(qt_app, window)

    assert _chips(window) == []
    assert len(_names(window)) == 3
    # 断的是「可见」而不是「子控件清空了」：``set_path`` 在隐藏时不会被调，面包屑里留着上一轮
    # 那几枚控件 —— 它整条不可见，留着无害，拿 ``findChildren`` 去断会量到一件与界面无关的事。
    assert window._breadcrumb.isVisible() is False
    window.close()
