"""后台任务的注册表与那 14 个槽的**唯一存储**（ADR 0051 / 0058，ADR 0060 与那个 mixin 分开）。

**取消分三组**：``LEVEL`` 换层、``LEVEL_CALENDAR`` 进日历页、``LIBRARY`` 切库 / 关窗。
**前两组是白名单**，所以 ``cancel_tier(LEVEL)`` 在结构上就碰不到 ``_write_job`` ——
那个任务正在动用户文件，换层时掐掉它就是文件搬到一半（约束 1「只增不改」）。
**取消不总是只有取消**：``_index_job`` 与 ``_place_job`` 各带一条必须同行的记账。
"""

from __future__ import annotations

from typing import Literal

__all__ = ["LEVEL", "LEVEL_CALENDAR", "LIBRARY", "JobGroup", "JobSlots", "JobSpec"]

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


#: 14 个任务各归哪一组。**这一处是「取消时机」唯一的来源**（ADR 0051 实施）。
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
    #: 孤儿检查（ADR 0043 决策 9）：generation 已经能挡住结果落地，**取消是为了别让那个模态框
    #: 在用户切到别的库之后才弹出来**。
    "_orphan_job": JobSpec(LIBRARY),
    "_cache_job": JobSpec(LIBRARY),
    "_write_job": JobSpec(LIBRARY),
    # 按番号导入影片（ADR 0063）：抓元数据 / 下封面都是网络 + 写盘，切库与关窗要停 ——
    # **不在换层那组**，与 `_write_job` 同一条理由（它也会往用户的文件夹里落文件）。
    "_movie_import_job": JobSpec(LIBRARY),
}

#: 两张换层的白名单。**它们不含 `_write_job`，也不该含** ——
#: 换层时掐掉一次进行中的文件写操作，受损的是约束 1，症状是文件搬到一半。
_GROUPS: dict[str, tuple[str, ...]] = {
    LEVEL: ("_scan_job", "_facet_job", "_filter_job"),
    LEVEL_CALENDAR: ("_scan_job", "_facet_job", "_calendar_job"),
    LIBRARY: tuple(_JOBS),
}


class JobSlots:
    """那 14 个任务槽的**唯一存储**（ADR 0058 阶段 1）。

    收进来之前它们是窗口上的 13 个普通字段，于是十一个模块各自在回调末尾写
    ``self._scan_job = None``（任务自己跑完，把句柄放掉）—— 类型系统把那种赋值
    当成了「这个 mixin 把它定义成了 ``None``」，同一个字段在两个基类里类型不一致
    （ADR 0056）。**现在的主人只有这一个**：谁想放掉句柄都得说一声 :meth:`finish`。
    """

    _scan_job: int | None
    #: 日历页读整库拍摄时间的任务；与 :attr:`_facet_job` 分开 —— 取消时机不同，会互相作废。
    _calendar_job: int | None
    _facet_job: int | None
    #: 「按条件收窄当前这一层」那一次筛选的任务（ADR 0040）。**与检索那个是分开的两份**：
    #: 它们不会同时进行（条件非空时只走其中一条），但取消时机不同 —— 换层时两个都要作废。
    _filter_job: int | None
    _detail_job: int | None
    _metadata_job: int | None
    #: 「复制元数据」现读元数据用的那次任务（面板的读取消了就没人要那份结果）。
    _metadata_copy_job: int | None
    _search_job: int | None
    _place_job: int | None
    _index_job: int | None
    #: 进行中的那一遍孤儿检查（ADR 0043 决策 9）。切库与关窗都要取消它 ——
    #: 回来得晚的那份结果属于上一个库，而它带来的确认框会问在错的窗口上。
    _orphan_job: int | None
    #: 「清空缩略图缓存」进行中的那一次（O(n) 的 unlink 循环跑在工作线程上，否则冻住窗口）。
    _cache_job: int | None
    #: **会往某个目录里落文件**的后台任务（⌘V / 复制到相册 / 移动到相册，ADR 0018、0019）：
    #: 三者同时只允许一个（会争同一个目标名字）。**不在换层那组** —— 换层时掐掉它就是
    #: 文件搬到一半（约束 1「只增不改」）。
    _write_job: int | None
    #: 按番号导入影片的那一次（ADR 0063）：抓元数据 → 下封面 → 写文件 → 建事件。
    #: 与 :attr:`_write_job` 一样会往用户的文件夹里落文件，同样**不在换层那组**。
    _movie_import_job: int | None

    def __init__(self) -> None:
        """14 个槽全是空的 —— 开窗那一刻没有一次任务在进行中。"""
        for name in _JOBS:
            setattr(self, name, None)

    def get(self, name: str) -> int | None:
        return getattr(self, name)

    def set(self, name: str, job_id: int | None) -> None:
        setattr(self, name, job_id)

    def finish(self, name: str) -> None:
        """任务自己跑完了，把句柄放掉。**这不是取消** —— 取消要掐工作线程。"""
        if name not in _JOBS:
            raise KeyError(f"没有登记的后台任务：{name}")
        setattr(self, name, None)
