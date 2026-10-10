"""后台任务的**取消分组**钉在这一份（ADR 0051 第一根轴的实施）。

这一份钉的是**结构**而不是行为，理由与 ADR 0050 那份一样：分组表本身就是病因。
「哪些任务在换层时被取消」原先只存在于读代码的人脑子里，而漏一处的症状是**静默**的 ——
把「层」那一组写成「取消全部」，就会在换层时掐掉一个进行中的文件写操作（约束 1「只增不改」），
症状是文件搬到一半。

所以钉三件事：**写操作不在换层那组**、**每一组只由它该被调用的那一处调用**、
以及**取消与「那遍算不算起过」没有被拆成两半**。
"""

from __future__ import annotations

import ast
from pathlib import Path

from gallery.ui.window.jobs import LEVEL, LEVEL_CALENDAR, LIBRARY, JobsMixin
from gallery.ui.window.state.jobs import _GROUPS, _JOBS

#: 换层时**绝对不能**取消的任务：它正在往用户的文件夹里落文件。
MUST_SURVIVE_A_LEVEL_CHANGE = ("_write_job",)


def _source_root() -> Path:
    """``src/gallery/ui`` 那一层（``parents[3]``，理由见 ``test_state_sync`` 里同一句）。"""
    return Path(__file__).resolve().parents[3] / "src" / "gallery" / "ui"


def test_the_write_job_survives_a_level_change() -> None:
    """**换层不许取消写操作** —— 它正在动用户文件，掐掉它就是文件搬到一半（约束 1）。

    这是本模块存在的理由：`_write_job` 不在任何一张换层白名单里，于是
    `cancel_tier(LEVEL)` 在结构上就够不着它，而不靠谁记得。
    """
    for group in (LEVEL, LEVEL_CALENDAR):
        for name in MUST_SURVIVE_A_LEVEL_CHANGE:
            assert name not in _GROUPS[group], f"{name} 落进了 {group} 组：换层会掐掉一次文件写操作"


def test_the_library_group_covers_every_job() -> None:
    """切库 / 关窗那一组**必须**是全部 13 个 —— 漏一个就是切库后还在跑旧库的活。"""
    assert set(_GROUPS[LIBRARY]) == set(_JOBS)


def test_the_two_level_groups_keep_the_members_they_have_today() -> None:
    """两张换层白名单**成员不同**，这是现状而不是笔误（ADR 0051「三组取消」）。

    `_reload_level` 取消筛选那一次，`_enter_calendar` 多取消日历那一次。把它们合成一张
    就是行为变更（换层会多取消一次日历读取），所以合成这件事要当作决策写，不能顺手做。
    """
    assert set(_GROUPS[LEVEL]) == {"_scan_job", "_facet_job", "_filter_job"}
    assert set(_GROUPS[LEVEL_CALENDAR]) == {"_scan_job", "_facet_job", "_calendar_job"}


def test_every_registered_job_is_declared_once_and_started_empty() -> None:
    """注册表里每个名字都得**在 :class:`JobSlots` 上声明过**，且开窗时它是空的。

    注册表与字段是两处知识：多一个名字会写进一个没人声明过的属性（读回来是 ``AttributeError``），
    少一个则那个任务没有取消时机 —— 两种都不报错，只是到那天才炸。

    **声明在 :class:`JobSlots` 的类级别，槽由它的 ``__init__`` 置空**（ADR 0058 阶段 1）：
    那 13 个槽搬进了那个对象，而 ``JobsMixin`` 只剩一组同名 property 转发。
    """
    tree = ast.parse((_source_root() / "window" / "state" / "jobs.py").read_text("utf-8"))
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JobSlots"
    )
    # 类级别的形状是 ``_x_job: int | None``（纯声明，没有值），所以取的是 ``Name.id``。
    declared = {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    init = next(
        node
        for node in ast.walk(cls)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    # 置空照着注册表走（``for name in _JOBS: setattr(...)``）—— 断的就是这一点。
    loops_registry = any(
        isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id == "_JOBS"
        for node in ast.walk(init)
    )
    assert set(_JOBS) == declared, "注册表与 `JobSlots` 的声明对不上"
    assert loops_registry, "`JobSlots.__init__` 没照着注册表置空（有槽开窗时压根不存在）"


def test_a_cancel_that_forgets_the_root_is_not_a_cancel() -> None:
    """**取消与「那遍算不算起过」是同一件事的两半**（`_index_job` 与 `_place_job`）。

    少了后一半，被取消的那一遍会被记成「做完了」——而它其实没做完。于是那个库在本进程里
    再也起不来那一遍：面板不出地名、检索索引永远建不起来，而这两处都不报错。
    """
    assert _JOBS["_index_job"].on_cancel == "_forget_indexed_root"
    assert _JOBS["_place_job"].on_cancel == "_forget_place_pass_root"
    # 其余任务是纯读，没有「起过哪些库」那份记账要撤。
    assert [name for name, spec in _JOBS.items() if spec.on_cancel] == [
        "_place_job",
        "_index_job",
    ]


def test_each_tier_is_cancelled_from_exactly_one_call_site() -> None:
    """**每一组只由它该被调用的那一处调用** —— 两处各调一次就会有一处是多出来的。

    具体说：`_reload_level` 调 `LEVEL`、`_enter_calendar` 调 `LEVEL_CALENDAR`、
    `_teardown_library` 调 `LIBRARY`。多出来的调用点意味着某一处正在做一件
    「换层不该做的取消」，而那正是约束 1 的受损方式。
    """
    expected = {
        "LEVEL": "navigation_flow.py",
        "LEVEL_CALENDAR": "calendar_flow.py",
        "LIBRARY": "library_flow.py",
    }
    root = _source_root()
    for constant, module in expected.items():
        offenders = [
            path.name
            for path in root.rglob("*.py")
            if path.name != "jobs.py" and f"_cancel_tier({constant})" in path.read_text("utf-8")
        ]
        assert offenders == [module], f"{constant} 组该只由 {module} 取消，实际有：{offenders}"


def test_the_mixin_offers_the_three_entry_points() -> None:
    """搬进来的三样都在：声明、提交、取消。少一样就说明这一根只搬了一半。"""
    for name in ("_init_jobs", "_start_job", "_cancel_job", "_cancel_tier"):
        assert callable(getattr(JobsMixin, name, None)), f"JobsMixin 缺 {name}"


def test_the_thirteen_slots_have_exactly_one_storage() -> None:
    """**每个槽只有一个存储** —— 转发 property 不许变成第二份（ADR 0058 阶段 1）。

    这条回归测试盯的是「转发层会不会悄悄变成两份状态」：窗口上的 ``_scan_job`` 是个 property，
    值在 ``self._jobs`` 里。**如果有人绕过它把 ``_JOBS`` 复制一份到窗口的 ``__dict__``**，
    就变成两份，症状是「一边改了另一边没动」。

    判据用 AST：``JobsMixin`` 里这 13 个名字必须是 ``property``（有 setter），
    而不是普通属性或方法；而唯一的真存储在 ``JobSlots`` 的 ``__init__`` 里。
    """
    root = _source_root()
    mixin = next(
        node
        for node in ast.parse((root / "window" / "jobs.py").read_text("utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "JobsMixin"
    )
    slots = next(
        node
        for node in ast.parse((root / "window" / "state" / "jobs.py").read_text("utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "JobSlots"
    )

    forwarded = {
        item.name
        for item in mixin.body
        if isinstance(item, ast.FunctionDef)
        and any(
            isinstance(d, ast.Name)
            and d.id == "property"
            or isinstance(d, ast.Attribute)
            and d.attr == "setter"
            for d in item.decorator_list
        )
    }
    missing = set(_JOBS) - forwarded
    assert not missing, f"这 13 个槽在 JobsMixin 上该是转发 property，现在不是：{missing}"

    # 真存储那一份：JobSlots 的类级别声明 + __init__ 里照注册表置空。
    stored = {
        item.target.id
        for item in slots.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    }
    assert set(_JOBS) <= stored, f"JobSlots 上少了这些槽的存储：{set(_JOBS) - stored}"
