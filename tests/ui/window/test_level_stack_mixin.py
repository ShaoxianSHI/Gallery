"""那六个字段**不再是字段** —— 它们是栈的投影，而栈只有四个写入口。

**这一份是 ADR 0041 实施步骤 2 的回归测试**，盯的是「手工同步彻底消失」这件事本身。

原先「我在哪」由六个可变字段表达，而它们之间有一堆没人声明的互斥关系，于是每加一条路径都要
靠人记住「这六个该一起清哪些」。漏掉一个的症状是**看起来完全正确的死路**（按 Esc 没反应），
而不是报错。所以这一份不是钉行为，是钉**结构**：那六个名字一旦重新变成可写字段，
互斥关系就又要靠人记了。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from source_scan import source_root

from gallery.core.library.photos import GroupRef
from gallery.ui.window.level_stack_mixin import LevelStackMixin
from gallery.ui.window.state.level_stack import (
    AlbumFrame,
    CalendarFrame,
    GroupFrame,
    LevelStack,
    PhotoFrame,
    RootKind,
    SearchFrame,
)

#: 「我在哪」的六个字段（ADR 0041）。它们**都该是只读属性**。
LEVEL_FIELDS = (
    "_browse_mode",
    "_current_dir",
    "_group_view",
    "_drill_photos",
    "_from_calendar",
    "_drill_label",
)

#: 栈的写入口。**只有这四个** —— 别的地方一律不许直接动 ``_level_stack``。
STACK_WRITERS = ("_push_frame", "_pop_frame", "_pop_to", "_set_root")


def _class_defining(path: Path, name: str) -> ast.ClassDef | None:
    """哪一个类定义了 ``name`` 这个属性。"""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == name:
                return node
    return None


@pytest.mark.parametrize("field", LEVEL_FIELDS)
def test_the_level_fields_are_read_only_properties(field: str) -> None:
    """每个字段都是 ``property``，**没有 setter** —— 赋值会当场抛 ``AttributeError``。

    量的是「它是 property 且没有 ``fset``」，不是「它现在读出来对不对」：读出来的值由栈决定，
    而这条要防的是**有人又开始往它上面赋值**。
    """
    from gallery.ui.main_window import MainWindow

    assert isinstance(getattr(MainWindow, field, None), property), (
        f"{field} 又变回普通字段了 —— 它必须是栈的投影（只读属性）"
    )
    attribute = getattr(MainWindow, field)
    assert attribute.fset is None, (
        f"{field} 有 setter —— 一旦可写，「几处手工同步」就回来了，"
        "而互斥关系没有任何一处代码声明（ADR 0041）"
    )


def test_the_stack_is_the_only_place_the_level_lives() -> None:
    """全仓库**没有一处**给那六个字段赋值 —— 栈是唯一来源。

    扫 AST 而不是扫文本：文档里 ``self._group_view = …`` 这样的片段到处都有（写清楚
    「为什么这里原本要清它」就得写它），按行扫会把注释与 docstring 一起算进来。
    """
    offenders: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        relative = path.relative_to(source_root().parents[3]).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: tuple[ast.expr, ...] = ()
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)
            elif isinstance(node, ast.Delete):
                targets = tuple(node.targets)
            else:
                continue
            for target in targets:
                if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
                    continue
                if target.value.id == "self" and target.attr in LEVEL_FIELDS:
                    offenders.append(f"{relative}:{target.lineno}  {target.attr}")
    assert not offenders, (
        "这些地方又给「我在哪」的字段赋值了 —— 它们要是栈的投影：\n"
        + "\n".join(offenders)
        + "\n改栈用 _push_frame / _pop_frame / _pop_to / _set_root 那四个入口。"
    )


def test_the_stack_is_only_written_through_those_four_methods() -> None:
    """``self._level_stack = …`` 只允许出现在 ``level_stack_mixin.py`` 与建栈的那一处。

    放行**整个 mixin 文件**而不是行号范围 —— 行号会随注释增删漂移（实测：四个写入口分布在
    163…190 之间，硬编码范围之后只是挪一行就红，而那红与被守的东西无关）。
    「四个写入口都在那个文件里」由 :func:`test_the_writers_exist_and_live_in_one_mixin`
    单独钉着，所以这里放行整个文件不构成漏洞。

    那一处例外是 ``MainWindow.__init__`` 里的建栈（还没有栈才能初始化它）。
    """
    offenders: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        relative = path.relative_to(source_root()).as_posix()
        if relative == "window/level_stack_mixin.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            targets = tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and target.attr == "_level_stack"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    continue
                # 建栈那一处：**语句在 ``__init__`` 里**（按父节点判断，不按行号）。
                if _inside_init(node, tree):
                    continue
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "这些地方直接换了整条栈：\n"
        + "\n".join(offenders)
        + "\n只有 __init__ 里建栈那处与 level_stack_mixin 的四个写入口可以 —— "
        "直接赋值会绕过「换浏览方式顺手清掉上面一切」那一条。"
    )


def _inside_init(node: ast.AST, tree: ast.Module) -> bool:
    """这一个赋值语句是不是在某个 ``__init__`` 里面。

    **走 AST 判父节点，不按行号**：行号会随注释增删漂移，而「在 ``__init__`` 里」这件事
    与行号无关。
    """
    for parent in ast.walk(tree):
        for _field, value in ast.iter_fields(parent):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if child is node and isinstance(parent, ast.FunctionDef):
                    return parent.name == "__init__"
    return False


def test_the_writers_exist_and_live_in_one_mixin() -> None:
    """那四个写入口**都在 ``LevelStackMixin`` 上**，不在别处再实现一遍。"""
    from gallery.ui.window.level_stack_mixin import LevelStackMixin

    for name in STACK_WRITERS:
        assert callable(getattr(LevelStackMixin, name, None)), f"少了写入口 {name}"

    root = source_root()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative == "window/level_stack_mixin.py":
            continue
        holder = _class_defining(path, "_push_frame")
        assert holder is None, (
            f"{relative} 又实现了一遍 _push_frame —— 栈的写入口只该有一处，"
            "两处就意味着两种改栈的方式"
        )


def _host_with(stack: LevelStack) -> LevelStackMixin:
    """一个只带那条栈的宿主（不必起真的窗口 —— 这几个属性不碰 Qt）。"""

    class _Host(LevelStackMixin):
        pass

    host = _Host()
    host._level_stack = stack
    return host


def _host_with_library(root: Path, stack: LevelStack) -> LevelStackMixin:
    """同上，但带一个只报 ``root`` 的库 —— ``_push_album_chain`` 要从它那里取库根。"""

    class _Library:
        def __init__(self, library_root: Path) -> None:
            self.root = library_root

    class _Host(LevelStackMixin):
        pass

    host = _Host()
    host._library = _Library(root)  # type: ignore[attr-defined]
    host._level_stack = stack
    return host


def test_the_group_view_reads_the_top_frame_not_the_deepest_one() -> None:
    """``_group_view`` 是**栈顶那一帧** —— 因为搜索结果上不压分组（ADR 0041 决策 2a）。

    这条量的是「判断写在哪」：栈上若是「相册 → 分组」而搜索帧压在分组之上，早先那个
    「从底往上找最后一个分组帧」的版本会让 ``_group_view`` 仍非空，而它有 29 个调用点 ——
    任何一处把它读成「我在分组里」都会做出分组专用行为（典型的是写操作只作用在组内照片上，
    那是删除那条）。用户定的「搜索结果平铺」把那整类歧义消掉了，所以判据只能是栈顶。
    """
    group = GroupRef("camera", "canon", "Canon", ())
    host = _host_with(LevelStack(root=RootKind.ALBUMS).push(GroupFrame(group)))
    assert host._group_view == group, "栈顶就是分组却读不到那一组"

    # 照片帧压上去之后，分组就不再是「当前所在的那一层」了。
    host._level_stack = host._level_stack.push(PhotoFrame(Path("/lib/a.jpg")))
    assert host._group_view is None, "栈顶是照片，却还说自己在一个分组里"


def test_the_search_frame_is_the_only_whole_library_predicate() -> None:
    """「条件正接管这一层」只问「栈顶是不是搜索帧」，不再看条件与作用域两个字段。"""
    host = _host_with(LevelStack(root=RootKind.ALBUMS))
    assert host._is_in_search_frame is False, "没有搜索帧却说有"

    host._level_stack = host._level_stack.push(SearchFrame(conditions=()))
    assert host._is_in_search_frame is True, "压了搜索帧却没认出来"

    # 搜索帧之上再压别的，它就不再是「接管这一层」的那一层了。
    host._level_stack = host._level_stack.push(AlbumFrame(Path("/lib/a")))
    assert host._is_in_search_frame is False, "栈顶不是搜索帧却说有"


def test_the_album_chain_and_the_calendar_chain_cannot_mix() -> None:
    """日历格下面不可能是相册 —— **类型上的互斥**，不靠 ``if`` 判（ADR 0041 决策 1）。

    量的是「日历模式下读不到相册路径」：靠 ``if`` 也能做到，但那时那句话散在几个地方，
    少一处就漏；而这里是「换根清空上面一切」的自然结果。
    """
    on_calendar = _host_with(
        LevelStack(root=RootKind.CALENDAR).push(CalendarFrame(photos=(), label="那一格"))
    )
    assert on_calendar._calendar_frame is not None
    assert on_calendar._from_calendar is True

    with_album = _host_with(LevelStack(root=RootKind.CALENDAR).push(AlbumFrame(Path("/lib/x"))))
    assert with_album._calendar_frame is None, "换根之后还留着日历格 —— 清栈没做干净"
    assert with_album._from_calendar is False


def test_switching_albums_replaces_the_whole_pile_not_just_the_album_frames() -> None:
    """换相册要弹掉的是「相册 + 压在它上面的分组/照片」那一摞，不只是相册帧。

    **只看栈顶是不是 ``AlbumFrame`` 就弹不出底下那条链**（栈顶是分组），于是旧链留着：
    目标是库根时 ``relative.parts`` 是空的、什么也不压，栈整个没变 —— 点面包屑上的根目录
    于是「没反应」。这里量的是弹完之后**再退一次不会退回那个分组**：落地那一层对不对，
    光看返回值是量不出来的（旧链压在底下时，落地照样看着对）。
    """
    root = Path("/lib")
    trip = root / "旅行"
    home = root / "家庭"
    group = GroupRef("captured_year", "2024", "2024 年", ())
    host = _host_with_library(root, LevelStack(root=RootKind.ALBUMS).push(AlbumFrame(trip)))

    host._push_album_chain(trip)  # 先退到相册本身
    host._level_stack = host._level_stack.push(GroupFrame(group))

    host._push_album_chain(root)
    assert host._level_stack.frames == (), "点库根弹不干净 —— 那个分组还在栈上"
    assert host._group_view is None
    assert host._current_dir is None

    host._level_stack = LevelStack(root=RootKind.ALBUMS).push(AlbumFrame(trip))
    host._level_stack = host._level_stack.push(GroupFrame(group))
    host._push_album_chain(home)
    assert [type(frame) for frame in host._level_stack.frames] == [AlbumFrame]
    host._level_stack = host._level_stack.pop()
    assert host._group_view is None, "退一层就退回那个分组 —— 旧的那一帧还压在底下"


def test_an_empty_cell_is_not_the_same_as_not_in_a_cell() -> None:
    """**空元组与 ``None`` 是两件事** —— 合成一处会让「在那一格里但空的」变成「不在那一格」。

    那一格展开的内容是空的，而 :meth:`MainWindow._reload_level` 里要按「有没有日历格」决定
    清不清它 —— 判据一旦变成「照片是不是空的」，那一格就会一直留着，而人以为还在那一格里。
    """
    empty = _host_with(
        LevelStack(root=RootKind.CALENDAR).push(CalendarFrame(photos=(), label="空的那一格"))
    )
    assert empty._drill_photos == (), "空的那一格报成 None —— 与「不在那一格」混成一处了"
    assert empty._calendar_frame is not None, "可栈上明明有那一格"
    assert empty._drill_label == "空的那一格"

    # 不在那一格时才报 None。
    nowhere = _host_with(LevelStack(root=RootKind.CALENDAR))
    assert nowhere._drill_photos is None, "不在那一格却报了一个照片列表"


def test_the_page_is_written_only_by_the_sync_method() -> None:
    """**换页只有一处** —— ``ui/window/`` 里只有 ``level_stack_mixin.py`` 能换页。

    页面是栈的函数（``page_role_of``）—— 别处一旦自己挑一页，「我在哪」就又变成两份记账，
    而两份记录迟早对不上（症状：日历页摆在前面、标题却写着大图页）。
    """
    root = source_root() / "window"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative == "level_stack_mixin.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "setCurrentWidget":
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "这些地方自己换了页，该交给 `_sync_page_to_stack()` 按栈摆：\n" + "\n".join(offenders)
    )
