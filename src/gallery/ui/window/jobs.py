"""后台任务的**生命周期**（ADR 0051 第一根轴）。# guard: allow-long-comment（数据安全约束）

13 个 ``*_job`` 字段的活法完全一样：提交时存句柄、结束时置 ``None``、取消时先判空。
原先那套写法散在十个模块里，于是「哪些任务属于哪一层」与「取消一个还要顺手做哪件事」
两处知识都只存在于读代码的人脑子里。收进这里之后，它们各占注册表里的一行。

**取消分三组**：``LEVEL`` 换层、``LEVEL_CALENDAR`` 进日历页、``LIBRARY`` 切库 / 关窗。
**前两组是白名单**，所以 ``cancel_tier(LEVEL)`` 在结构上就碰不到 :attr:`_write_job` ——
那个任务正在动用户文件，换层时掐掉它就是文件搬到一半（约束 1）。

**取消不总是只有取消**：``_index_job`` 与 ``_place_job`` 各带一条必须同行的记账
（把「这个库起过一遍」那份集合里的根拿掉）。少了它，被取消的那一遍会被记成「做完了」，
而它其实没做完 —— 于是那个库在本进程里再也起不来那一遍。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from gallery.ui.tasks import TaskContext

__all__ = ["LEVEL", "LEVEL_CALENDAR", "LIBRARY", "JobsMixin"]

#: 换层时取消的那一组（`_reload_level`）。**白名单**：写进去一个就多一份「换层会掐掉它」的责任。
LEVEL: Literal["level"] = "level"

#: 进日历页时取消的那一组（`_enter_calendar`）。与上面**成员不同**（多日历、少筛选）——
#: 两处本来就不一样，合成一组就是行为变更，所以分成两张表。
LEVEL_CALENDAR: Literal["level_calendar"] = "level_calendar"

#: 切库 / 关窗时取消的那一组：全部。
LIBRARY: Literal["library"] = "library"

JobGroup = Literal["level", "level_calendar", "library"]


class JobSpec:
    """一个任务属于哪一组，以及取消它时**必须同做**的那件事。"""

    __slots__ = ("group", "on_cancel")

    def __init__(self, group: JobGroup, on_cancel: str | None = None) -> None:
        self.group = group
        #: 「取消之后还要调的方法名」。取消与「那遍算不算起过」是同一件事的两半，
        #: 注册表只记前一半就等于把坑留在原地。
        self.on_cancel = on_cancel


#: 13 个任务各归哪一组。**这一处是「取消时机」唯一的来源**（ADR 0051 实施）。
#:
#: `_place_job` / `_search_job` / `_detail_job` / 两个元数据任务都只在切库时成组取消；
#: 它们平时由各自的调用点按名字取消（大图页翻图、面板显隐），那不是一组，不进下面的白名单。
#:
#: `_facet_job` 与 `_calendar_job` 分开存：取消时机不同，合成一个字段就会互相作废。
_JOBS: dict[str, JobSpec] = {
    "_scan_job": JobSpec(LEVEL),
    "_facet_job": JobSpec(LEVEL),
    "_filter_job": JobSpec(LEVEL),
    "_calendar_job": JobSpec(LEVEL_CALENDAR),
    "_detail_job": JobSpec(LIBRARY),
    "_metadata_job": JobSpec(LIBRARY),
    "_metadata_copy_job": JobSpec(LIBRARY),
    "_search_job": JobSpec(LIBRARY),
    "_place_job": JobSpec(LIBRARY, "_forget_place_pass_root"),
    "_index_job": JobSpec(LIBRARY, "_forget_indexed_root"),
    # 孤儿检查（ADR 0043 决策 9）：generation 已经能挡住结果落地，**取消是为了别让那个模态框
    # 在用户切到别的库之后才弹出来**。
    "_orphan_job": JobSpec(LIBRARY),
    "_cache_job": JobSpec(LIBRARY),
    "_write_job": JobSpec(LIBRARY),
}

#: 两张换层的白名单。**它们不含 `_write_job`，也不该含** ——
#: 换层时掐掉一次进行中的文件写操作，受损的是约束 1，症状是文件搬到一半。
_GROUPS: dict[str, tuple[str, ...]] = {
    LEVEL: ("_scan_job", "_facet_job", "_filter_job"),
    LEVEL_CALENDAR: ("_scan_job", "_facet_job", "_calendar_job"),
    LIBRARY: tuple(_JOBS),
}


class JobsMixin:
    """13 个后台任务的提交与取消 —— **写入口只有这一处**。

    字段仍是窗口上的普通属性（本包规矩：属性名一个字都不改，测试直接摸它们），
    注册表记的是「名字 → 组 + 取消时的同行动作」。本轴**不接 ``changed`` 信号**，
    它与界面无关（ADR 0051 决策 5）。
    """

    def _init_jobs(self) -> None:
        """声明那 13 个任务槽；``MainWindow.__init__`` 里只留这一句。"""
        self._scan_job: int | None = None
        #: 日历页读整库拍摄时间的任务；与 :attr:`_facet_job` 分开 —— 取消时机不同，会互相作废。
        self._calendar_job: int | None = None
        self._facet_job: int | None = None
        #: 「按条件收窄当前这一层」那一次筛选的任务（ADR 0040）。**与检索那个是分开的两份**：
        #: 它们不会同时进行（条件非空时只走其中一条），但取消时机不同 —— 换层时两个都要作废。
        self._filter_job: int | None = None
        self._detail_job: int | None = None
        self._metadata_job: int | None = None
        #: 「复制元数据」现读元数据用的那次任务（面板的读取消了就没人要那份结果）。
        self._metadata_copy_job: int | None = None
        self._search_job: int | None = None
        self._place_job: int | None = None
        self._index_job: int | None = None
        #: 进行中的那一遍孤儿检查（ADR 0043 决策 9）。切库与关窗都要取消它 ——
        #: 回来得晚的那份结果属于上一个库，而它带来的确认框会问在错的窗口上。
        self._orphan_job: int | None = None
        #: 「清空缩略图缓存」进行中的那一次（O(n) 的 unlink 循环跑在工作线程上，否则冻住窗口）。
        self._cache_job: int | None = None
        # guard: allow-long-comment（约束 1：换层时掐掉它就是文件搬到一半）
        #: **会往某个目录里落文件**的后台任务（⌘V / 复制到相册 / 移动到相册，ADR 0018、0019）：
        #: 三者同时只允许一个（会争同一个目标名字）。**不在换层那组** —— 换层时掐掉它就是
        #: 文件搬到一半（约束 1「只增不改」）。
        self._write_job: int | None = None

    def _start_job(
        self,
        name: str,
        work: Callable[[TaskContext], Any],
        *,
        on_result: Callable[[object], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> None:
        """提交一个后台任务并存下句柄（``name`` 是那 13 个字段名之一）。"""
        if name not in _JOBS:
            raise KeyError(f"没有登记的后台任务：{name}")
        setattr(
            self,
            name,
            self._runner.submit(
                work, on_result=on_result, on_error=on_error, on_progress=on_progress
            ),
        )

    def _cancel_job(self, name: str) -> None:
        """取消一个任务（没在跑就什么也不做）。

        ``on_result`` / ``on_error`` 不会被调用 —— 已投递但还没跑的会被
        ``TaskRunner.abandon`` 丢掉，跑着的那些靠取消令牌在检查点停下。
        """
        if name not in _JOBS:
            raise KeyError(f"没有登记的后台任务：{name}")
        job_id = getattr(self, name)
        if job_id is None:
            return
        self._runner.cancel(job_id)
        setattr(self, name, None)
        spec = _JOBS[name]
        if spec.on_cancel is not None:
            getattr(self, spec.on_cancel)()

    def _cancel_tier(self, group: str) -> None:
        """取消某一组里的全部任务。**别拿它当「取消全部」用** ——
        ``_write_job`` 在动用户文件，换层时掐掉它就是文件搬到一半（约束 1）。
        """
        for name in _GROUPS[group]:
            self._cancel_job(name)
