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

from gallery.ui.window.jobs import _GROUPS, _JOBS, LEVEL, LEVEL_CALENDAR, LIBRARY, JobsMixin

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
    """注册表里每个名字都得**在类级别声明过**，且 ``_init_jobs`` 把它置空。

    注册表与字段是两处知识：多一个名字会写进一个没人声明过的属性（读回来是 ``AttributeError``），
    少一个则那个任务没有取消时机 —— 两种都不报错，只是到那天才炸。

    **声明在类级别而不在 :meth:`_init_jobs` 里**（理由见 ``jobs.py`` 那段注释）：那些 mixin 各自
    在回调末尾写 ``self._x_job = None``，而声明写在方法里时 mypy 会把**那一句**当成
    「这个 mixin 把它定义成了 ``None``」，于是同一个字段在两个基类里类型不一致。
    """
    tree = ast.parse((_source_root() / "window" / "jobs.py").read_text("utf-8"))
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JobsMixin"
    )
    # 类级别的形状是 ``_x_job: int | None``（纯声明，没有值），所以取的是 ``Name.id``。
    declared = {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    init = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_init_jobs"
    )
    initialised = {
        target.attr
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
    }
    assert set(_JOBS) == declared, "注册表与类级别的声明对不上"
    assert set(_JOBS) == initialised, "有任务没在 `_init_jobs` 里置空（开窗时它压根不存在）"


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
