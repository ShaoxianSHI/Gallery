"""``LibrarySlot`` 与 ``AppSession`` 的形状回归（ADR 0058 阶段 3）。

两族收进来之前都是窗口上的字段，被 ``MainWindow.__init__`` 与 ``library_flow``
各写一遍。这一族要断的是「**关库时该清的清了，转发层没变成第二份**」。
"""

from __future__ import annotations

import ast

from source_scan import source_root

from gallery.ui.window.state.app_session import AppSession
from gallery.ui.window.state.library_slot import LibrarySlot

#: 「现在开着哪个库」这一族。
SLOT_FIELDS = ("_library", "_pipeline", "_library_title", "_legacy_ui")

#: 窗口上转发的那几个（``_session_base`` / ``_cache_base`` 只有 getter，只读）。
FORWARDED = SLOT_FIELDS + ("_session",)


def _class_body(module: str, name: str) -> ast.ClassDef:
    tree = ast.parse((source_root() / module).read_text("utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _property_names(body: list[ast.stmt]) -> set[str]:
    """类体里那些**带 property 或 setter 装饰器**的名字。"""
    found: set[str] = set()
    for node in body:
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


def test_the_slot_owns_the_open_library() -> None:
    """**「开着哪个库」这一族每个字段都在 ``LibrarySlot`` 上** —— 少一个就没人拥有。"""
    cls = _class_body("window/state/library_slot.py", "LibrarySlot")
    declared = {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert not set(SLOT_FIELDS) - declared, f"LibrarySlot 上少了：{set(SLOT_FIELDS) - declared}"


def test_the_app_session_owns_the_run_state() -> None:
    """**「这一次运行」这一族在 ``AppSession`` 上** —— 它由 ``__init__`` 的两个形参交进来。"""
    cls = _class_body("window/state/app_session.py", "AppSession")
    declared = {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    expected = {"_session_base", "_cache_base", "_session"}
    assert not expected - declared, f"AppSession 上少了：{expected - declared}"


def test_closing_the_library_clears_all_three_that_move() -> None:
    """**关库要把「库 / 流水线 / 标题」一起清回没开库的样子**。

    ``_legacy_ui`` **不清** —— 它整个进程只读一次（ADR 0038 的库级偏好），
    下一开库还要用。漏掉 ``_pipeline`` 的症状是关库之后缩略图还在往上一个库解码，
    而界面上一个错都看不出来。
    """
    slot = LibrarySlot()
    slot._legacy_ui = object()  # type: ignore[assignment]
    slot._library_title = "某个库"
    slot._pipeline = object()  # type: ignore[assignment]

    slot.clear()

    assert slot._library is None
    assert slot._pipeline is None, "漏清流水线 = 关库后还在往旧库解码"
    assert slot._library_title == "Gallery", "标题要回到产品名"
    assert slot._legacy_ui is not None, "库级偏好整个进程只读一次，不该被关库清掉"


def test_the_mixin_forwards_instead_of_storing() -> None:
    """**那 7 个名字在 ``LibraryFlowMixin`` 上必须是 property** —— 变成普通属性就是第二份。"""
    cls = _class_body("window/library_flow.py", "LibraryFlowMixin")
    missing = set(FORWARDED) - _property_names(cls.body)
    assert not missing, f"这些该是转发 property，现在不是：{missing}"


def test_the_two_paths_are_read_only() -> None:
    """**那两个路径只有 getter** —— 它们是开窗时定一次的，谁也不该在运行中改。"""
    cls = _class_body("window/library_flow.py", "LibraryFlowMixin")
    setters = {
        node.name
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Attribute) and d.attr == "setter" for d in node.decorator_list)
    }
    assert not {"_session_base", "_cache_base"} & setters, "这两个路径不该有 setter"


def test_the_session_is_loaded_from_the_given_base(tmp_path: object) -> None:
    """**会话从传进来的那个目录读** —— 不读成别处的（那会在真实目录里留下窗口几何）。"""
    session = AppSession(session_base=tmp_path)  # type: ignore[arg-type]
    assert session._session_base == tmp_path
    assert session._cache_base is None
    assert session._session is not None, "会话总要有一份（读不到就是空的那份）"


def test_the_window_builds_both_families_in_one_place() -> None:
    """**``MainWindow.__init__`` 里建这两族只有 ``_init_opening(...)`` 一句**。

    散开建就意味着「哪几个字段是一族的」只存在于读代码的人脑子里 —— 而少建一个的
    症状是那个槽压根不存在，读回来是 ``None``，不报错。
    """
    tree = ast.parse((source_root() / "main_window.py").read_text("utf-8"))
    init = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    calls = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_init_opening"
    ]
    assert len(calls) == 1, f"`_init_opening()` 在 `__init__` 里出现了 {len(calls)} 次"
    # 两个路径必须**传进去**，不能在别处各自置一遍。
    assert len(calls[0].args) == 2, "两个路径该作为形参交进去，而不是在窗口上另置"


def test_the_guard_would_notice_a_second_storage() -> None:
    """这条回归测试自己必须验过一次：把转发 property 换成普通属性，上面那条要红。"""
    cls = _class_body("window/library_flow.py", "LibraryFlowMixin")
    assert "_library" in _property_names(cls.body), "干净树上这一条是通过的"
    broken = ast.ClassDef(
        name=cls.name,
        bases=[],
        keywords=[],
        body=[
            ast.AnnAssign(
                target=ast.Name(id="_library", ctx=ast.Store()),
                annotation=None,
                value=None,
                simple=1,
            )
        ],
        decorator_list=[],
    )
    assert "_library" not in _property_names(broken.body), "普通属性不是转发，判据要红"
