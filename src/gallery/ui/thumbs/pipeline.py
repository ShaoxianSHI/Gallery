"""缩略图流水线：把「当前窗口需要的图」变成主线程上可用的 ``QImage``。

界面线程只调用 :meth:`ThumbnailPipeline.set_window`，解码、缩放、编码落盘全在线程池里，结果用代次号
过滤（切库之后旧库的任务跑完也不会画到新界面上）。缩放也在工作线程做（ADR 0015）：``QImage.scaled``
线程安全，界面线程只剩一次 ``QPixmap.fromImage``。``_failed`` / ``_empty`` / ``_covers`` 是「这个
文件读不出来」的结论记忆，只在切库时清。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from gallery.core.library.facade import Library
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.ui.imaging import to_scaled_qimage
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumbs.queue import VISIBLE, ThumbnailQueue

#: ``target_edge`` 的「不缩放」取值，也是 ``ready`` 第三个参数的「没有缩放过」。
NO_EDGE = 0


@dataclass(frozen=True, slots=True)
class ScaledThumbnail:
    """一张在工作线程里就缩好的缩略图。"""

    image: object
    edge: int


@dataclass(slots=True)
class _Job:
    """装「一次投递」的盒子：它的 job_id、档位，以及这一格最终用了谁的像素。

    ``TaskRunner.submit`` 要先拿到回调，回调又要知道自己是谁 —— 那时 ``job_id`` 还不存在，
    于是用一个只装一次的盒子把 id 递进去。``cover`` 只对相册有意义：封面是现去找的，失败时
    要记住「是这一张不行」（见 ``_on_error``）。
    """

    job_id: int | None = None
    edge: int = NO_EDGE
    cover: Path | None = None


@dataclass(slots=True)
class _Cover:
    """一个相册「封面挑到谁」的那份记忆。

    ``skipped`` 是当过封面却读不出来的照片，换封面时跳过它们；``message`` 是最近一次失败的
    中文说法 —— 整个相册的照片都试完之后正是拿它把原因说出来。
    """

    skipped: set[Path] = field(default_factory=set)
    message: str = ""


class ThumbnailPipeline(QObject):
    """按窗口优先级把缩略图喂给界面。"""

    #: 一张缩略图准备好了：``(路径, QImage, 实际缩到的长边)``。
    ready = Signal(object, object, int)
    #: 一张缩略图解码失败：``(路径, 中文错误)``。
    failed = Signal(object, str)

    def __init__(self, library: Library, runner: TaskRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._runner = runner
        self._queue = ThumbnailQueue()
        self._refs: dict[Path, PhotoRef] = {}
        self._inflight: dict[tuple[Path, int], int] = {}
        self._failed: dict[Path, int] = {}
        self._empty: dict[Path, int] = {}
        self._covers: dict[Path, _Cover] = {}
        self._generation = 0
        #: 当前要缩到多长。在**提交任务时**定格进闭包（见 :meth:`_start`）。
        self._target_edge = NO_EDGE
        # 进行中的任务不超过线程数，保证线程池永远满载，同时让「可见」优先级真正生效。
        self._max_inflight = max(1, runner.thread_count)

    def set_window(
        self,
        visible: Sequence[GridEntry],
        prefetch: Sequence[GridEntry] = (),
        *,
        target_edge: int = NO_EDGE,
    ) -> None:
        """声明当前窗口：``visible`` 先出图，``prefetch`` 次之，其余请求全部丢弃。

        条目可能是照片、相册或分组卡片，三者在这里都被归一成「一条路径 + 一个能出图的引用」。
        ``target_edge`` 是缩略图要缩到多长，在工作线程里做，``0`` 表示不缩放。
        """
        self._target_edge = max(NO_EDGE, int(target_edge))
        targets = [target for entry in visible if (target := _thumbnail_target(entry))]
        ahead = [target for entry in prefetch if (target := _thumbnail_target(entry))]
        # 已在解码的路径不再重新入队 —— 不同档位互相独立，由 _dispatch
        # 按 (path, edge) 区分；同档位进行中的请求自然被挡住。
        inflight_paths = {path for path, _ in self._inflight}
        self._queue.set_window(
            [target.path for target in targets if target.path not in inflight_paths],
            prefetch=[target.path for target in ahead if target.path not in inflight_paths],
        )
        self._remember_window((*targets, *ahead))
        self._dispatch()

    def shutdown(self) -> None:
        """切库或关窗：作废所有进行中的结果，并请求取消它们的任务。"""
        self._generation += 1
        for job_id in self._inflight.values():
            self._runner.cancel(job_id)
        self._inflight.clear()
        self._queue.clear()
        self._refs.clear()
        self._failed.clear()
        self._empty.clear()
        self._covers.clear()

    def forget(self, paths: Iterable[Path]) -> None:
        """这些路径上的文件变了：忘掉「读不出来 / 是空相册」的结论。

        那两个集合是记忆，为的是别对同一个坏文件反复重试；文件被外部替换之后记忆就过期了 ——
        不清掉那一格会永远停在占位图上（ADR 0012）。**一批一次过，不逐条重扫所有封面**：逐个
        遍历时云盘上大量读不出来的占位文件会让 ``_covers`` 变得很大，而监听器一次能报几十条。
        """
        targets = {Path(path) for path in paths}
        for target in targets:
            self._failed.pop(target, None)
            self._empty.pop(target, None)
        for cover in self._covers.values():
            cover.skipped.difference_update(targets)

    def _remember_window(self, entries: tuple[GridEntry, ...]) -> None:
        """把 ``_refs`` 换成当前窗口那一份。

        不能一直往后加：只有 :meth:`_dispatch` 查它，而它查的全是队列里的路径，队列同样每次
        都整份换掉 —— 于是窗外那些条目谁也够不着却一直留着（十万张的库滚一遍，实测约
        5.2 MB）。进行中的那几条必须留着：``_refs`` 同时是换封面那条路回头找条目的地方。
        """
        refs = {entry.path: entry for entry in entries}
        for path, _ in self._inflight:
            entry = self._refs.get(path)
            if entry is not None:
                refs.setdefault(path, entry)
        self._refs = refs

    def _dispatch(self) -> None:
        while len(self._inflight) < self._max_inflight:
            path = self._queue.pop_next()
            if path is None:
                return
            # guard: allow-long-comment（坏文件按档位记结论：同一档重试还是那个结果，还占线程池）
            # 「读不出来」「是空相册」这两个结论**按档位**记，而不是一律丢掉重试：128 px 读不
            # 出来不代表 256 px 也读不出来（换档要重试），而同一档位下坏文件重试再多次还是那个
            # 结果 —— 而那是最贵的一种失败，还要占住线程池把好照片挤到后面。
            edge = self._target_edge
            if self._failed.get(path) == edge or self._empty.get(path) == edge:
                continue
            if (path, edge) in self._inflight:
                continue
            ref = self._refs.get(path)
            if ref is None:
                continue
            self._start(ref)

    def _start(self, entry: GridEntry) -> None:
        library = self._library
        generation = self._generation
        edge = self._target_edge

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            if isinstance(entry, PhotoRef):
                ref = entry
            else:
                # 相册：拿里面找到的第一张照片当封面（读不出来的几张跳过），空相册为 None。
                ref = library.folder_cover(
                    entry.path,
                    should_cancel=context.is_cancelled,
                    skip=self._covers.get(entry.path, _Cover()).skipped,
                )
                if ref is None:
                    return None
                holder.cover = ref.path
            # 缩放在这里做（工作线程）。``should_cancel`` 必须一路传进解码：云盘上没下完的
            # 照片会让 ``open()`` 永远不返回，工作线程被永久占住，最后连 ⌘Q 都退不出去。
            return ScaledThumbnail(
                to_scaled_qimage(library.thumbnail(ref, should_cancel=context.is_cancelled), edge),
                edge,
            )

        holder = _Job()
        job_id = self._runner.submit(
            work,
            on_result=partial(self._on_result, entry, generation, holder),
            on_error=partial(self._on_error, entry, generation, holder),
        )
        holder.job_id = job_id
        holder.edge = edge
        self._inflight[(entry.path, edge)] = job_id

    def _release(self, entry: GridEntry, holder: _Job) -> None:
        """收尾时把这一格从「进行中」里摘出去，且只摘自己那一次。

        ``shutdown()`` 会清空 ``_inflight`` 并换代；旧一代的任务晚回来时若无条件
        ``pop(entry.path)``，摘掉的就是新一代登记的那一条 —— ``_inflight`` 少数了，
        ``cancel()`` 也够不着真正在跑的新任务。
        """
        key = (entry.path, holder.edge)
        if holder.job_id is not None and self._inflight.get(key) == holder.job_id:
            del self._inflight[key]

    def _on_result(self, entry: GridEntry, generation: int, holder: _Job, result: object) -> None:
        self._release(entry, holder)
        if generation != self._generation:
            self._dispatch()
            return
        if result is None:
            # 空相册没有封面：记下来别再反复问，界面会画文件夹图标。但「整个相册都读不出来」
            # 长得和空相册一模一样，区别只有一句话 —— 那句话得说出来。
            self._empty[entry.path] = holder.edge
            cover = self._covers.get(entry.path)
            if cover is not None and cover.message:
                self.failed.emit(entry.path, cover.message)
        elif isinstance(result, ScaledThumbnail):
            self.ready.emit(entry.path, result.image, result.edge)
        self._dispatch()

    def _on_error(self, entry: GridEntry, generation: int, holder: _Job, message: str) -> None:
        self._release(entry, holder)
        if generation == self._generation:
            if holder.cover is not None and not isinstance(entry, PhotoRef):
                cover = self._covers.setdefault(entry.path, _Cover())
                cover.skipped.add(holder.cover)
                cover.message = message
                # 重新排一次队（这一格不记进 ``_failed``：换个封面也许能成）；整个相册都试完
                # 之后 ``folder_cover`` 返回 ``None``（那一支见 ``_on_result``）。
                self._queue.add(entry.path, priority=VISIBLE)
                self._dispatch()
                return
            self._failed[entry.path] = holder.edge
            self.failed.emit(entry.path, message)
        self._dispatch()


def _thumbnail_target(entry: GridEntry) -> GridEntry | None:
    """这一格要显示谁的缩略图。

    分组卡片换成它组内第一张照片：封面不是现场去找的，于是它和普通照片走完全一样的解码与
    缓存路径。空分组返回 ``None``。
    """
    if isinstance(entry, GroupRef):
        return entry.cover
    return entry


__all__ = ["NO_EDGE", "ScaledThumbnail", "ThumbnailPipeline"]
