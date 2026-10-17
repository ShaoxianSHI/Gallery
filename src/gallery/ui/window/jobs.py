"""后台任务的**生命周期**（ADR 0051）。# guard: allow-long-comment（三组与取消的另一半）

**写入口只有这一处**。注册表与存储住在 :mod:`~gallery.ui.window.state.jobs` 里
（ADR 0058 阶段 1、ADR 0060 分开住），本模块只管「什么时候起、什么时候停」。
14 个 ``*_job`` 字段的活法完全一样：提交时存句柄、结束时置 ``None``、取消时先判空 ——
原先那套写法散在十个模块里，于是「哪些任务属于哪一层」与「取消一个还要顺手做哪件事」
两处知识都只存在于读代码的人脑子里。收进注册表之后，它们各占一行。

**取消分三组**：``LEVEL`` 换层、``LEVEL_CALENDAR`` 进日历页、``LIBRARY`` 切库 / 关窗。
**前两组是白名单**，所以 ``cancel_tier(LEVEL)`` 在结构上就碰不到 ``_write_job`` ——
那个任务正在动用户文件，换层时掐掉它就是文件搬到一半（约束 1）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gallery.ui.tasks import TaskContext
from gallery.ui.window.state.jobs import (
    _GROUPS,
    _JOBS,
    LEVEL,
    LEVEL_CALENDAR,
    LIBRARY,
    JobSlots,
)

__all__ = ["LEVEL", "LEVEL_CALENDAR", "LIBRARY", "JobsMixin"]


class JobsMixin:
    """guard: allow-long-comment（转发层为什么必须留着，值得说满）—— 14 个后台任务的提交与取消。

    窗口上仍留着一组**同名 property** 转发过去：本包规矩属性名一个字都不改，
    ``tests/ui/waiting.py`` 用 ``getattr(window, name)`` 取它们（59 处）。
    **转发不产生第二份存储** —— 真正的值只在 ``self._jobs`` 里。

    本轴**不接 ``changed`` 信号**，它与界面无关（ADR 0051 决策 5）。
    """

    _jobs: JobSlots

    def _init_jobs(self) -> None:
        """建这一族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._jobs = JobSlots()

    # ---- 那 14 个槽的转发 property ------------------------------------------
    # guard: allow-long-comment（14 组读写对，拆开就没法一眼看出「名字 ↔ 槽」一一对应）
    # **为什么留这一层** —— ``tests/ui/waiting.py`` 按名字 ``getattr(window, name)``，
    # 测试里另有 56 处直接摸 ``window._scan_job``（含几处 ``= 999`` 假扮进行中）。
    # 属性名一个字都不改是本包的规矩，而删掉它们等于删测试。

    @property
    def _scan_job(self) -> int | None:
        return self._jobs.get("_scan_job")

    @_scan_job.setter
    def _scan_job(self, job_id: int | None) -> None:
        self._jobs.set("_scan_job", job_id)

    @property
    def _calendar_job(self) -> int | None:
        return self._jobs.get("_calendar_job")

    @_calendar_job.setter
    def _calendar_job(self, job_id: int | None) -> None:
        self._jobs.set("_calendar_job", job_id)

    @property
    def _facet_job(self) -> int | None:
        return self._jobs.get("_facet_job")

    @_facet_job.setter
    def _facet_job(self, job_id: int | None) -> None:
        self._jobs.set("_facet_job", job_id)

    @property
    def _filter_job(self) -> int | None:
        return self._jobs.get("_filter_job")

    @_filter_job.setter
    def _filter_job(self, job_id: int | None) -> None:
        self._jobs.set("_filter_job", job_id)

    @property
    def _detail_job(self) -> int | None:
        return self._jobs.get("_detail_job")

    @_detail_job.setter
    def _detail_job(self, job_id: int | None) -> None:
        self._jobs.set("_detail_job", job_id)

    @property
    def _metadata_job(self) -> int | None:
        return self._jobs.get("_metadata_job")

    @_metadata_job.setter
    def _metadata_job(self, job_id: int | None) -> None:
        self._jobs.set("_metadata_job", job_id)

    @property
    def _metadata_copy_job(self) -> int | None:
        return self._jobs.get("_metadata_copy_job")

    @_metadata_copy_job.setter
    def _metadata_copy_job(self, job_id: int | None) -> None:
        self._jobs.set("_metadata_copy_job", job_id)

    @property
    def _search_job(self) -> int | None:
        return self._jobs.get("_search_job")

    @_search_job.setter
    def _search_job(self, job_id: int | None) -> None:
        self._jobs.set("_search_job", job_id)

    @property
    def _place_job(self) -> int | None:
        return self._jobs.get("_place_job")

    @_place_job.setter
    def _place_job(self, job_id: int | None) -> None:
        self._jobs.set("_place_job", job_id)

    @property
    def _index_job(self) -> int | None:
        return self._jobs.get("_index_job")

    @_index_job.setter
    def _index_job(self, job_id: int | None) -> None:
        self._jobs.set("_index_job", job_id)

    @property
    def _orphan_job(self) -> int | None:
        return self._jobs.get("_orphan_job")

    @_orphan_job.setter
    def _orphan_job(self, job_id: int | None) -> None:
        self._jobs.set("_orphan_job", job_id)

    @property
    def _cache_job(self) -> int | None:
        return self._jobs.get("_cache_job")

    @_cache_job.setter
    def _cache_job(self, job_id: int | None) -> None:
        self._jobs.set("_cache_job", job_id)

    @property
    def _write_job(self) -> int | None:
        return self._jobs.get("_write_job")

    @_write_job.setter
    def _write_job(self, job_id: int | None) -> None:
        self._jobs.set("_write_job", job_id)

    @property
    def _movie_import_job(self) -> int | None:
        return self._jobs.get("_movie_import_job")

    @_movie_import_job.setter
    def _movie_import_job(self, job_id: int | None) -> None:
        self._jobs.set("_movie_import_job", job_id)

    def _start_job(
        self,
        name: str,
        work: Callable[[TaskContext], Any],
        *,
        on_result: Callable[[object], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> None:
        """提交一个后台任务并存下句柄（``name`` 是那 14 个槽名之一）。"""
        if name not in _JOBS:
            raise KeyError(f"没有登记的后台任务：{name}")
        self._jobs.set(
            name,
            self._runner.submit(
                work, on_result=on_result, on_error=on_error, on_progress=on_progress
            ),
        )

    def _finish_job(self, name: str) -> None:
        """**任务自己跑完了**，把句柄放掉（回调末尾调它，不是取消）。

        与 :meth:`_cancel_job` 的区别是这一条**不掐工作线程** —— 那是取消干的事。
        """
        self._jobs.finish(name)

    def _cancel_job(self, name: str) -> None:
        """取消一个任务（没在跑就什么也不做）。

        ``on_result`` / ``on_error`` 不会被调用 —— 已投递但还没跑的会被
        ``TaskRunner.abandon`` 丢掉，跑着的那些靠取消令牌在检查点停下。
        """
        if name not in _JOBS:
            raise KeyError(f"没有登记的后台任务：{name}")
        job_id = self._jobs.get(name)
        if job_id is None:
            return
        self._runner.cancel(job_id)
        self._jobs.finish(name)
        spec = _JOBS[name]
        if spec.on_cancel is not None:
            getattr(self, spec.on_cancel)()

    def _cancel_tier(self, group: str) -> None:
        """取消某一组里的全部任务。**别拿它当「取消全部」用** ——
        ``_write_job`` 在动用户文件，换层时掐掉它就是文件搬到一半（约束 1）。
        """
        for name in _GROUPS[group]:
            self._cancel_job(name)
