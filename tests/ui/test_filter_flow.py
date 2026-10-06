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
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    SEARCH_SCOPE_LEVEL,
    prepare_library,
)
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    SessionState,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.external_button import ICON_EDGE as EXTERNAL_ICON_EDGE  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels.breadcrumb import ChipButton  # noqa: E402
from gallery.ui.search_field import ICON_EDGE, PLACEHOLDER_FILTER, PLACEHOLDER_SEARCH  # noqa: E402

#: 测试库里每张照片的相机（按文件名对应）。没给的就是没写相机。
CAMERAS = {"海边-01.jpg": "Canon EOS R5", "山.jpg": "Sony A7"}

#: 每张照片的拍摄时间。**取「这个月」而不是写死的年份**：日历停在今天上，而
#: :meth:`CalendarPage.photo_count` 只数**当前那一段**里看得见的 —— 日期写死在别处，这一层就
#: 永远是空的，而症状（`photo_count() == 0`）与「日历没喂上照片」一模一样。
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

    ``_filter_job`` 与 ``_search_job`` 一并等掉：切作用域时两者可能交替在途。
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
    """日历那一页**看得见**自己加了哪几条条件 —— 用户原话：「看不到加了什么筛选条件」。

    **这一条是这个需求本身**：筛选时那一页仍然是日历（上面那条），而日历那一页占掉的就是网格页
    那条面包屑（ADR 0033 决策 5）—— 胶囊只摆在面包屑上，就等于「加了但不知道加了什么」。

    量的是**工具条上真的有那几枚**，而不是「模型里的条件列表非空」：后者在日历上照样成立，
    它量不到「用户看不见」。
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
    """在日历上把条件撤光，**工具条上那几枚也要跟着走** —— 用户原话：「UI 上第二个一直存在不消失」。

    **这一条盯的是那条早退路径**：摆胶囊的那一句曾写在 :meth:`_update_breadcrumb` 的末尾，
    而那个方法在「无条件」时 ``hide()`` 完就 ``return`` —— 撤光走的正是那一支，于是控件留在
    屏幕上（模型里已经空了，量模型永远绿）。切到相册视图再切回来就好了，因为切视图会重走
    一遍没有早退的那条路，症状于是变成「要绕一圈才干净」。

    所以这里从**窗口**这一层按用户的手法走一遍（点 ✕、回车、Esc 都试），而不是直接调
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

    点 ✕ 那条已经钉住，但撤光有几条独立的路（点 ✕、输入框里清空回车、``⇧Esc``），而它们各自走
    一遍 ``_apply_conditions``。只钉一条的话，另几条各自坏掉都没人看着 —— 而症状一样。
    """
    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_CALENDAR)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _filter(qt_app, window, "#城市 西安")
    _settle(qt_app, window)

    # **先让输入框空下来**：`dismiss` 的第一件事是清空输入框（那是「这一下」），输入框空了才轮到撤
    # 条件。刚加完条件时框里还留着那一串，于是连按两下只撤了一条 —— 症状是「撤不掉」，
    # 而真因是少按了一下。**按到条件真的撤光为止**，不写死按几下。
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

    > 用户原话：「在相册视图切到日历视图后再增加一个筛选条件会取代之前的筛选条件」。

    根因是 ``_editing_condition`` **粘住**：点胶囊正文会设上它，而下一次回车走的是
    ``self._conditions[editing] = …`` —— **就地替换，条目数不增加**。焦点离开原先不清它，于是
    用户点完胶囊去做别的（切视图、点空白），之后敲的每一条都在顶掉那一条，而屏幕上只看得见
    「条件变了」，看不见「我刚才点过一枚胶囊」。

    **量的是条目数**，不是值对不对：值对了而条目数没变，正是这个 bug（被顶掉的那条恰好与新的
    等价时，值的断言会一路绿）。
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
    # 接着去做了别的事 —— 焦点离开了输入框，编辑态随之清掉（用户原话：「避免用户做了很多
    # 别的操作后忘记之前进了编辑模式」）。**这一步不能省**：留着编辑态才是在测「静默劫持」。
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
    也都是改动它。脱下标时**只脱一层就够**（``self._conditions[a][b]`` 不存在），脱完拿到的
    应当还是 ``self._conditions`` 这个 ``Attribute`` —— 顺手把它拆成 ``self`` 之后就没人能
    拿 ``attr`` 了，那正是这条判据一开始返回 ``False`` 的原因。
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
    ``.sort()`` 不改，``.index()`` 更不改 —— 混进判据会让这条守护变成「凡碰到它就红」。
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


def _the_line_about_conditions(block: str) -> str:
    """从 ``ast.unparse`` 的整块里取出**提到 ``_conditions`` 的那一句**。

    ``if editing is not None: self._conditions[editing] = condition`` 这一整块的文本与源码里
    那一行不是一回事（前者带缩进与换行），而清单按源码写 —— 所以只留相关的那句。
    读（``conditions = tuple(self._conditions)``）不在这几支里，不会被取出来。
    """
    lines = [line.strip() for line in block.splitlines() if "self._conditions" in line]
    return lines[0] if lines else block.strip()


def test_only_three_things_can_take_a_condition_away() -> None:
    """条件只有三个下场：**点 ✕ / 改这一条时回车替换 / 换库**（ADR 0040 决策 12）。

    用户要的是「除了手动点叉之外任何情况下不消失」，而这一条是那句话的**可查形式**：扫出全仓库
    每一处改动 ``self._conditions`` 长度的写法，只允许那三样。清单外的写法一律红。

    **为什么非要扫一遍而不靠几条行为用例**：这个 bug 的形状是「某处 ``_editing_condition``
    粘住 → 下一次提交静默替换」，而任何一条行为用例都只能盯住**它已知的那几条路径**。新加一条
    「清条件」的路（换作用域、换分组、回退到大图页）时，行为用例一条都不会红 —— 只有这份清单会。

    **扫的是 AST 而不是文本**：文档里 ``self._conditions[editing] = …`` 这样的代码片段到处都有
    （写清楚为什么是「就地替换」就得写它），按行扫会把注释与 docstring 一起算进来 —— 那条守护
    第一次跑就抓到了我自己写的一段说明文字。
    """
    allowed = {
        # ---- 初始化 ----
        ("src/gallery/ui/main_window.py", "self._conditions: list[SearchCondition] = []"),
        # ---- 用户明明白白做的那几件 ----
        ("src/gallery/ui/window/search_flow.py", "self._conditions.append(condition)"),  # 加一条
        ("src/gallery/ui/window/search_flow.py", "del self._conditions[position]"),  # 点 ✕
        ("src/gallery/ui/main_window.py", "self._conditions.pop()"),  # ⇧Esc 撤一条
        ("src/gallery/ui/window/search_flow.py", "self._conditions.clear()"),  # 清搜索框
        # 就地替换：长度不变，但它确实改内容 —— 单独放行。
        ("src/gallery/ui/window/search_flow.py", "self._conditions[editing] = condition"),
        # ---- 换场合，不是「用户撤掉了」：切到整库检索时清掉它 ----
        # 整库检索横跨整个库，而筛选只管当前这一层（ADR 0026 决策 1 / ADR 0040 决策 1）——
        # 两者是互不相干的概念，换浏览方式 / 进相册 / 换库时不该叠在一起。
        ("src/gallery/ui/window/library_flow.py", "self._conditions.clear()"),
        ("src/gallery/ui/window/calendar_flow.py", "self._conditions.clear()"),
        ("src/gallery/ui/main_window.py", "self._conditions.clear()"),
    }

    root = Path(__file__).resolve().parents[2]
    found: set[tuple[str, str]] = set()
    for path in sorted((root / "src" / "gallery").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # **判据是「这一句会不会改它」**。会改长度的写法全在下面三支里，而读
            # （``for x in self._conditions``、``len(...)``、下标取值）与改顺序的
            # （``.sort()``）走的不是任何一支，天然不算。
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets: tuple[ast.expr, ...] = (
                    tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)
                )
                if not any(_is_conditions_target(target) for target in targets):
                    continue
            elif isinstance(node, ast.Delete):
                if not any(_is_conditions_target(target) for target in node.targets):
                    continue
            elif isinstance(node, ast.Call):
                # ``self._conditions.append(x)`` —— **方法调用那一支**，与赋值分开判。
                if not _is_conditions_mutation(node):
                    continue
            else:
                continue
            # **只取「涉及它的那一句」**：``ast.unparse`` 出的是整块（含 ``if``/``else``），
            # 同一个字符串于是与清单对不上。清单按源码那一行写，逐行比才有意义。
            code = _the_line_about_conditions(ast.unparse(node))
            found.add((relative, code))
            if (relative, code) not in allowed:
                pytest.fail(
                    f"{relative}:{node.lineno} 多了一处改动筛选条件的地方：{code!r}\n"
                    "条件只该由三件事动：点 ✕（_on_condition_removed）、改这一条时回车"
                    "（_on_search_submitted 里就地替换）、换库 / 换作用域。"
                    "\n真要加第四条，请连同这里与 ADR 0040 决策 12 一起改 —— "
                    "否则就是又一条「条件自己消失了」而没人说得清为什么。"
                )

    # 清单本身也要看着点：允许项被挪走或改名了时这里会红，而不是悄悄放行。
    unused = allowed - found
    assert not unused, f"清单里有几条已经不存在了（挪走或改名了）：{sorted(unused)}"


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

    用户要的就是这一条：「避免用户做了很多别的操作后忘记之前进了编辑模式」。兜底挂在
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

    这一条是上一条的必要反面：放弃修改的闸若不区分「在改已有的一条」与「在敲新的一条」，
    用户敲到一半点别处，**刚敲的字凭空消失** —— 那比原 bug 更糟（它丢的是没确认的东西）。
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

    条件跟着走是 ADR 0040 决策 1（早就在了），编辑态不跟着走是这一条。只断前半句的话，症状是
    「换了视图再敲一条，顶掉的是上一个视图里的那一条」。
    """

    def _no_cancel_on_blur(self: MainWindow) -> None:
        """把**失焦那条兜底**按掉 —— 只让 ``set_browse_mode`` 里的那一处有机会干活。

        **按的是 :meth:`_on_search_active_changed` 而不是 :meth:`_cancel_condition_edit` 本身**：
        后者会把 ``set_browse_mode`` 里那一处也一起按掉（它调的正是这个方法），于是注入违规后
        用例照样绿 —— 第一版就是这么写的。按「失焦的入口」才真的只关掉一条路。
        """
        self._update_action_states()

    root = _library(tmp_path, make_photo)
    window = _open(qt_app, root, tmp_path / "会话", browse_mode=BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)
    _filter(qt_app, window, "#相机 Canon")
    _settle(qt_app, window)
    window._on_condition_edited(0)
    _settle(qt_app, window)

    # **把失焦那条兜底彻底按掉**，只让 ``set_browse_mode`` 里的那一处有机会干活：不这么做的话，
    # 切视图时 ``focus_current_view`` 会把焦点从输入框拿走、失焦那条顺手清掉编辑态，于是即使把
    # ``set_browse_mode`` 里那一处删掉，本用例照样绿 —— 它第一版就是这么写的，注入违规后没红。
    monkeypatch.setattr(MainWindow, "_on_search_active_changed", _no_cancel_on_blur)
    window._editing_condition = 0
    window.set_browse_mode(BROWSE_MODE_CALENDAR)
    qt_app.processEvents()

    assert _chips(window) == ["相机：Canon"], f"切视图不该动条件：{_chips(window)}"
    assert window._editing_condition is None, "切视图没有放弃修改"
    window.close()


def test_switching_the_view_keeps_the_conditions_and_appends_the_next_one(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """切视图之后敲的新条件是**追加** —— 与上一条一起把「切视图」这半边钉住。

    上一条按掉了失焦那条兜底，所以它量的是「``set_browse_mode`` 自己有没有放弃」；这一条走真实
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
    """换浏览方式**保留**条件（ADR 0040 决策 1）—— 整库检索在这一步会清掉它们。

    反过来的症状是「换一次视图筛选就没了」，而用户会以为是自己按了什么。
    """
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


def test_escape_leaves_the_folder_without_touching_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """在子相册里按 `Esc`：**退出去，条件一条都不少** —— 用户报的那个 bug。

    用户原话：「点开一个格子后，esc 退出一层就会清空一个筛选条件」，相册与日历**两边都**有。

    根因是判据的**顺序**：那一版把条件排在文件夹 / 那一格之前（ADR 0026 决策 9「条件又深一级」），
    于是只要有条件就永远退不出位置 —— 按 Esc 看到的是一条条件被撤掉而**位置纹丝不动**，而那两件
    事在屏幕上分不出来，用户会以为程序卡住了。

    量的是**位置真的退了**与**条件真的没少**：只断一条的话，另一条坏掉都没人看着 —— 而症状一样。
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

    assert window._current_dir == root, "Esc 没能退出子相册"
    assert _chips(window) == ["相机：Canon", "城市：西安"], (
        f"Esc 顺手撤掉了条件：{_chips(window)} —— 它只该管位置"
    )
    window.close()


def test_escape_returns_to_the_calendar_without_touching_the_conditions(
    qt_app: QApplication, tmp_path: Path, make_photo
) -> None:
    """日历里点开一格之后按 `Esc`：**退回日历，条件一条都不少**。

    与相册那条同一个根因（判据顺序），但**这条路要单独钉**：日历那一格走的是
    ``_drill_photos``，相册走的是 ``_current_dir``，两条独立的分支 —— 只钉一条的话另一条坏掉
    没人看着，而症状一模一样。
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
    「顺手做点什么」的理由了。退不掉而顺手撤一条，用户完全无法预期。
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
    接上了没有」是两件事 —— 快捷键没挂上、上下文写错、或者被输入框先吃掉，症状都是「按了没反应」，
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
    模块那份对照表抄的**：凭印象写会写成「山」，而症状是停在「条件没生效」那条红上，真因却是我
    记错了哪张是 Canon。
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


def test_the_button_switches_the_scope_and_the_words(qt_app: QApplication, tmp_path: Path) -> None:
    """按钮一按：占位文字、勾、菜单文案一起换。**三处说同一句话**，否则用户会以为它们是两个设置。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_SEARCH
    assert window._scope_toggle.isChecked() is False

    window._scope_toggle.setChecked(True)
    qt_app.processEvents()

    assert window._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
    assert window._scope_action.isChecked() is True
    assert window._search_action.text() == "筛选…"
    assert window._clear_search_action.text() == "清除筛选条件"
    window.close()


def test_the_icon_is_smaller_than_the_button(qt_app: QApplication, tmp_path: Path) -> None:
    """图标**不许画满整个按钮** —— 22 点画布上画 22 点的东西会顶到边框上。

    钉的是「图标比按钮小」这个关系，不是某个数：真要盯住的是图标画布与按钮边长之间留着
    余量，而两者都由 :data:`~gallery.ui.search_field.TOGGLE_ICON_EDGE` 与
    :data:`~gallery.ui.search_field.TOGGLE_EDGE` 推出来 —— 有人把图标又改回跟着按钮走时，
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


def test_selecting_the_button_fills_it_not_just_its_edge(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """选中时**整枚按钮**填满高亮色，不只是那一圈轮廓 —— 守护。

    **量像素，不量样式表字符串**：只断言 ``"background"`` 出现在样式表里量不到任何东西 ——
    颜色写对了而 Qt 没画（或者被别的规则盖掉）时它照样绿。这一条量的是「按钮中心那一块到底
    是什么颜色」。

    为什么非得填满：22 点的圆角小块上，1 像素的边只占边长的一成，而这一枚按钮**没有文字标签**
    可看 —— 唯一的「现在开着吗」信号就是那一片颜色。
    """
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()
    toggle = window._scope_toggle

    resting = _button_center_color(toggle)
    toggle.setChecked(True)
    qt_app.processEvents()
    selected = _button_center_color(toggle)

    assert resting is not None and selected is not None
    assert selected != resting, "选中态的中心还是原来那个底色 —— 整枚按钮没被填满"
    # 填的必须是高亮那一档，而它与菜单里选中项同源（``menu_style`` 的 ``:selected``）。
    highlight = QPalette(QApplication.instance().palette()).color(QPalette.ColorRole.Highlight)
    assert _close(selected, highlight, 0.06), (
        f"选中态填的是 {selected.name()}，要高亮的 {highlight.name()}（菜单选中项也是那一档）"
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
    """两个颜色差得不算远（``lightnessF`` 上的差，阈值写死一个 ``tolerance``）。"""
    return abs(left.lightnessF() - right.lightnessF()) <= tolerance and (
        abs(left.red() - right.red()) <= 2
        and abs(left.green() - right.green()) <= 2
        and abs(left.blue() - right.blue()) <= 2
    )


def test_the_menu_item_and_the_button_are_one_setting(qt_app: QApplication, tmp_path: Path) -> None:
    """从菜单切过去，按钮也要亮 —— 两条路走同一个入口，所以两处永远一致。"""
    window = MainWindow(session_base=tmp_path / "会话")
    window.show()
    qt_app.processEvents()

    window._scope_action.setChecked(True)
    qt_app.processEvents()

    assert window._search_scope == SEARCH_SCOPE_LEVEL
    assert window._scope_toggle.isChecked() is True
    assert window._search_field._edit.placeholderText() == PLACEHOLDER_FILTER
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
    assert again._scope_toggle.isChecked() is True
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

    而且它们是会动用户文件的那一类（红线 1），不该在用户看不清全貌时发生。
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
    """用户看到空结果就再也不会筛选第二次了 —— 与整库检索同一道闸（ADR 0026 决策 5）。

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

    走的是「重跑一遍」那条路（与整库检索同一处），所以这条量的是**结果对不对**，不是增量
    逻辑本身 —— 增量分支本来就不该存在（另写一份正是 ADR 0026 决策 7 点名的 bug 来源）。
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
    # **不再显示**（平铺模式下没有条件时面包屑整条收起来，ADR 0026 决策 9）。
    # 断的是「可见」而不是「子控件清空了」：``set_path`` 在隐藏时不会被调，面包屑里留着上一轮
    # 那几枚控件 —— 它整条不可见，留着无害，而拿 ``findChildren`` 去断会量到一件与用户无关的事。
    assert window._breadcrumb.isVisible() is False
    window.close()
