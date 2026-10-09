"""``OrderingState`` 的形状回归（ADR 0058 阶段 2）。

断的是「**转发层没有变成第二份存储**」：窗口上 ``_sort_facet`` 是个 property，
值在 ``self._ordering`` 里。这两件事看起来一样，但只有一份的时候改了排序字段才会
处处跟着变。
"""

from __future__ import annotations

import ast
import pathlib

from gallery.ui.window.state.ordering_state import OrderingState

#: 这一族全部字段的名字 —— 注册表式的单一来源，测试与实现都对着它。
SLOTS = (
    "_group_facet",
    "_sort_facet",
    "_sort_descending",
    "_facet_values",
    "_event_values",
    "_place_resolver",
    "_reorder_fingerprint",
    "_reorder_values",
    "_reorder_events",
    "_reorder_photos",
    "_reorder_albums",
)

#: 转发的那几个。``_reorder_photos`` / ``_reorder_albums`` 只有一个写者，留给窗口。
FORWARDED = tuple(n for n in SLOTS if n not in {"_reorder_photos", "_reorder_albums"})


def _source_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3] / "src" / "gallery" / "ui"


def _forwarded_names(source: str) -> set[str]:
    """``OrderingMixin`` 里那些**带 property 或 setter 装饰器**的名字。

    **同一份解析，两条用例共用** —— 自检那条也走它，判据才不会走偏。
    """
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OrderingMixin")
    found: set[str] = set()
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Name)
                and deco.id == "property"
                or isinstance(deco, ast.Attribute)
                and deco.attr == "setter"
            ):
                found.add(node.name)
    return found


def test_the_state_owns_every_slot_of_the_family() -> None:
    """**这一族的每个字段都在 ``OrderingState`` 上声明过** —— 少一个就是没人拥有的。

    这是「一份状态一个主人」那条判据的直接落点：以前它们是 ``main_window`` /
    ``ordering`` / ``library_flow`` 三个模块各自在写的窗口字段。
    """
    tree = ast.parse((_source_root() / "window" / "state" / "ordering_state.py").read_text("utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OrderingState")
    declared = {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert not set(SLOTS) - declared, f"OrderingState 上少了这些槽：{set(SLOTS) - declared}"


def test_the_mixin_forwards_instead_of_storing() -> None:
    """**转发的那几个必须真的是 property** —— 变成普通属性就是第二份存储。"""
    source = (_source_root() / "window" / "ordering.py").read_text("utf-8")
    missing = set(FORWARDED) - _forwarded_names(source)
    assert not missing, f"这些该是 OrderingMixin 上的转发 property，现在不是：{missing}"


def test_the_guard_would_notice_a_second_storage() -> None:
    """这条回归测试自己必须验过一次：把转发 property 换成普通属性，上面那条要红。"""
    source = (_source_root() / "window" / "ordering.py").read_text("utf-8")
    injected = source.replace(
        "    @property\n    def _sort_descending(self) -> bool:",
        "    _sort_descending: bool\n    def _unused_descending(self) -> bool:",
        1,
    ).replace(
        "    @_sort_descending.setter\n"
        "    def _sort_descending(self, value: bool) -> None:\n"
        "        self._ordering._sort_descending = value\n",
        "",
        1,
    )
    assert injected != source, "没找到那个转发 property（形状变了？）"
    assert "_sort_descending" not in _forwarded_names(injected), "注入没生效"
    # 干净树上这一条是通过的 —— 断的是「注入之后不通过」。
    assert "_sort_descending" in _forwarded_names(source)


def test_the_state_starts_empty_and_the_sort_facet_is_usable() -> None:
    """**开箱就有一个能用的排序字段** —— 不是 ``None``（菜单里没有「不排序」可退回）。"""
    state = OrderingState()
    assert state._group_facet is None, "默认不分组"
    assert state._sort_descending is False, "所有字段默认一律升序（ADR 0049）"
    assert state._sort_facet, "排序字段必须是真字段 id，不能是空的"
    assert state._facet_values == {} and state._event_values == {}
    assert state._place_resolver is None, "地名解析器是用起来才造的"
    assert state._reorder_fingerprint is None and state._reorder_values is None


def test_forgetting_the_reorder_input_clears_all_seven() -> None:
    """**换库时那七个「上一次重排的输入」要一起清掉** —— 漏一个就拿旧库的输入判「没变」。

    症状很具体：换库之后这一层的顺序是上一个库的，而且只在「facet 值恰好没换」时发作。
    """
    state = OrderingState()
    state._facet_values = {1: 2}  # type: ignore[dict-item]
    state._event_values = {1: 2}  # type: ignore[dict-item]
    state._reorder_fingerprint = ("x",)
    state._reorder_values = {}  # type: ignore[assignment]
    state._reorder_events = {}  # type: ignore[assignment]
    state._reorder_photos = (1,)  # type: ignore[assignment]
    state._reorder_albums = (1,)  # type: ignore[assignment]

    state.forget_reorder_input()

    assert state._facet_values == {}
    assert state._event_values == {}
    assert state._reorder_fingerprint is None
    assert state._reorder_values is None
    assert state._reorder_events is None
    assert state._reorder_photos == ()
    assert state._reorder_albums == ()


def test_the_window_only_builds_this_family_in_one_place() -> None:
    """**``MainWindow.__init__`` 里建这一族只有 ``_init_ordering()`` 一句**。

    散开建就意味着「哪几个字段是一族的」这件事只存在于读代码的人脑子里
    —— 而少建一个的症状是那个槽压根不存在，读回来是 ``None``，不报错。
    """
    tree = ast.parse((_source_root() / "main_window.py").read_text("utf-8"))
    init = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    calls = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_init_ordering"
    ]
    assert len(calls) == 1, f"`_init_ordering()` 在 `__init__` 里出现了 {len(calls)} 次"
