"""缩略图流水线：把「当前窗口需要的图」变成主线程上可用的 ``QImage``。

界面线程只调用 :meth:`ThumbnailPipeline.set_window`，剩下的解码、缩放、编码落盘
全在线程池里。结果用**代次号**过滤：切库之后，旧库的任务即使跑完也不会画到新界面上。

**缩放也在工作线程做**（ADR 0015）：``QImage.scaled`` 线程安全，于是界面线程只剩
一次 ``QPixmap.fromImage``，不再和 ``QListView`` 的排版绘制抢那一次平滑缩放。为此
结果连同「这张图是缩到几才发出来的」一起交回（:attr:`ThumbnailPipeline.ready` 的
第三个参数）—— 尺寸对不上的结果会由 :meth:`PhotoListModel.needs_thumbnail` 认出来
并重取，所以切档瞬间在途的任务不会把格子填歪。

解码失败的照片会被记住并跳过，避免每次滚动都对同一个坏文件反复重试。

## 哪些账本跟着窗口走，哪些一路留着

只有 ``_refs`` 跟着**当前窗口**走（见 :meth:`ThumbnailPipeline._remember_window`）：
它唯一的作用是给队列里的路径找条目，而队列每次都整份换掉。

``_failed`` / ``_empty`` / ``_covers`` **一律不清，只在切库时清**（``shutdown``）。
它们是**「这个文件读不出来」这种结论的记忆**，不是索引：坏文件是**永久**坏的
（除非用户拿外部工具把它换掉，那时 :meth:`forget` 会把那条结论作废）。
把它们也按窗口清掉，用户每滚回去一次就要把同一个坏文件重新解码一遍 ——
而解码一个截断 / 损坏的 JPEG 恰恰是最贵的那一种失败，几百个坏文件的库
滚一趟就是几百次白费的解码，还要占住线程池把好照片的图挤到后面去。
几百条路径本身才几十 KB，与它们省下的那些次解码完全不成比例，所以留着。

``_covers`` 多一层理由：它是**相册**的份数，不是照片的 —— 相册数比照片数小几个
数量级，压根撑不到需要清的分量。
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

#: ``target_edge`` 的「不缩放」取值，同时也是 :attr:`ThumbnailPipeline.ready`
#: 第三个参数里表示「这张图没有按某个尺寸缩放过」的值。
NO_EDGE = 0


@dataclass(frozen=True, slots=True)
class ScaledThumbnail:
    """一张在工作线程里就缩好的缩略图。"""

    image: object
    #: 它是按这个长边缩的（``0`` = 原样，按当前尺寸直接用）。
    edge: int


@dataclass(slots=True)
class _Job:
    """装「一次投递」的盒子：它的 job_id，以及**这一格最终用了谁的像素**。

    ``TaskRunner.submit`` 要先拿到回调，回调又要知道自己是谁 ——
    那时 ``job_id`` 还不存在。用一个只装一次的盒子把 id 递进去，
    提交返回后立刻填上。回调据此确认「回来的这个结果确实是我那一次」。

    ``cover`` 只对相册有意义：封面是现去找的，失败时要记住「是这一张不行」，
    下一次才好换下一张当封面（见 :meth:`ThumbnailPipeline._on_error`）。
    """

    job_id: int | None = None
    cover: Path | None = None


@dataclass(slots=True)
class _Cover:
    """一个相册「封面挑到谁」的那份记忆。

    ``skipped`` 是当过封面却读不出来的照片：换封面时跳过它们。少了这份记忆，
    一张云盘上没下完的照片会让整张相册卡片永远挂着它（用户看到的是一张出不来、
    也换不掉的黑封面）。``message`` 是最近一次失败的中文说法 —— 整个相册的照片
    都试完之后正是拿它把原因说出来，否则界面上只剩一个文件夹图形。
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
        #: 每条路径**最新**那一次的 job_id（用来判断「是不是正在解」）。
        self._inflight: dict[Path, int] = {}
        #: 所有还活着的任务：``job_id → (路径, 它是按哪个档位解的)``。
        #: 一条路径可以同时有两个任务（见 :meth:`_dispatch`），所以计数与作废都问这一份。
        self._live: dict[int, tuple[Path, int]] = {}
        self._failed: set[Path] = set()
        self._empty: set[Path] = set()
        #: 相册 → 挑封面挑到哪儿了（见 :class:`_Cover`）。
        self._covers: dict[Path, _Cover] = {}
        self._generation = 0
        #: 当前要缩到多长。在**提交任务时**定格进闭包（见 :meth:`_start`）。
        self._target_edge = NO_EDGE
        # 在途任务不超过线程数，保证线程池永远满载，同时让「可见」优先级真正生效。
        self._max_inflight = max(1, runner.thread_count)

    def set_window(
        self,
        visible: Sequence[GridEntry],
        prefetch: Sequence[GridEntry] = (),
        *,
        target_edge: int = NO_EDGE,
    ) -> None:
        """声明当前窗口：``visible`` 先出图，``prefetch`` 次之，其余请求全部丢弃。

        条目可能是照片、相册或分组卡片：相册的封面要现去找，分组卡片的封面早就知道
        （组内第一张）。三者在这里都被归一成「一条路径 + 一个能出图的引用」。

        :param target_edge: 缩略图要缩到多长。在工作线程里做，``0`` 表示不缩放。
        """
        self._target_edge = max(NO_EDGE, int(target_edge))  # noqa: E501
        targets = [target for entry in visible if (target := _thumbnail_target(entry))]
        ahead = [target for entry in prefetch if (target := _thumbnail_target(entry))]
        self._queue.set_window(
            [target.path for target in targets],
            prefetch=[target.path for target in ahead],
        )
        # **``_refs`` 整份换成当前窗口那一份**（理由见 ``_remember_window``）。
        self._remember_window((*targets, *ahead))
        self._dispatch()

    def shutdown(self) -> None:
        """切库或关窗：作废所有在途结果，并请求取消它们的任务。"""
        self._generation += 1
        for job_id in self._live:
            self._runner.cancel(job_id)
        self._inflight.clear()
        self._live.clear()
        self._queue.clear()
        self._refs.clear()
        self._failed.clear()
        self._empty.clear()
        self._covers.clear()

    def forget(self, paths: Iterable[Path]) -> None:
        """这些路径上的文件变了：忘掉「读不出来 / 是空相册」的结论。

        那两个集合是**记忆**，为的是别对同一个坏文件反复重试；但文件被外部工具替换
        之后记忆就过期了 —— 不清掉，那一格会永远停在占位图上（ADR 0012）。
        """
        for path in paths:
            target = Path(path)
            self._failed.discard(target)
            self._empty.discard(target)
            # 「这一张不能当封面」同样会过期：云盘把半截的照片补完之后，
            # 它又是一个好封面了（见 :class:`_Cover`）。
            for cover in self._covers.values():
                cover.skipped.discard(target)

    def _remember_window(self, entries: tuple[GridEntry, ...]) -> None:
        """把 ``_refs`` 换成**当前窗口**那一份。

        **为什么不是一直往后加**：``_refs`` 是「路径 → 能出图的那个引用」的对照表，
        只有 :meth:`_dispatch` 会查它，而 :meth:`_dispatch` 查的全是**队列里**的路径 ——
        队列同样每次都整份换成可见 + 预读（``ThumbnailQueue.set_window``）。
        于是窗外那些条目谁也够不着，却一个都没走：把一个 10 万张的平铺库从头滚到尾，
        这份表就攒下 10 万条（实测约 5.2 MB）。换层 / 换分组 / 换相册同样不清 ——
        而那些正是「离开了一会儿又回来」的场景，清掉它们没有任何好处。

        **在途的那几条必须留着**：``_refs`` 同时是 :meth:`_on_error` 换封面那条路
        （重新排队）回头找条目的地方，``_inflight`` 里的路径正是它下一次要排队的 ——
        摘掉的话，一张云盘上没下完的照片就会让那张相册卡片永远挂着上一张黑封面，
        而且 ``cancel()`` 之后再也够不着那个真正在跑的任务。
        """
        refs = {entry.path: entry for entry in entries}
        for path in self._inflight:
            entry = self._refs.get(path)
            if entry is not None:
                refs.setdefault(path, entry)
        self._refs = refs

    def _dispatch(self) -> None:
        while len(self._live) < self._max_inflight:
            path = self._queue.pop_next()
            if path is None:
                return
            if path in self._failed or path in self._empty:
                continue
            # 同一张照片**同一个档位**不能重复投递：窗口会随滚动反复刷新，而已在解码的
            # 路径仍然会出现在新的可见窗口里。
            #
            # **档位不同就要再解一次**：日历三层与网格要的档位互不相同（年 48 / 月 288 /
            # 周 192 / 网格 176），而切过去的那一刻，上一档位的那一次通常还在飞。按路径
            # 一刀切地挡掉，于是新档位那一张**从此没人再解** —— 在途的那一次回来时带着
            # 旧边长，谁都用不上（尺寸对不上就是没出图）。要等碰巧又滚动一次 / 又重排
            # 一次才会重新报上去，于是「刚切过来是灰的，切一下再切回来就好了」。
            busy = self._inflight.get(path)
            if busy is not None and self._live.get(busy, (path, NO_EDGE))[1] == self._target_edge:
                continue
            ref = self._refs.get(path)
            if ref is None:
                continue
            self._start(ref)

    def _start(self, entry: GridEntry) -> None:
        library = self._library
        generation = self._generation
        # 在提交时就定格，而不是等任务跑完再读：切档瞬间在途的任务要带着它开始
        # 时的尺寸走完，交回来的边长才对得上它自己的像素。
        edge = self._target_edge

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            if isinstance(entry, PhotoRef):
                ref = entry
            else:
                # 相册：拿里面找到的第一张照片当封面；空相册返回 None。
                # 当过封面却读不出来的那几张跳过 —— 否则一张云盘上没下完的照片
                # 会让整张相册卡片一直挂着那张（用户看到的是一张黑的封面）。
                ref = library.folder_cover(
                    entry.path,
                    should_cancel=context.is_cancelled,
                    skip=self._covers.get(entry.path, _Cover()).skipped,
                )
                if ref is None:
                    return None
                holder.cover = ref.path
            # 缩放在这里做（工作线程），界面线程只留一次 QPixmap.fromImage。
            #
            # ``should_cancel`` 必须一路传进解码里：一张云盘上还没下完的照片会让
            # ``open()`` 永远不返回，没有它这个工作线程就被永久占住 —— 占满之后整条
            # 流水线停摆，而关窗时 Qt 还要 join 它们，于是连 ⌘Q 都退不出去
            # （理由与做法见 ``core.media.decode._within_deadline``）。
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
        # ``submit`` 要先拿得到回调，回调又要知道自己是谁 —— 用一个只装一次的
        # 小盒子把 job_id 递进去（:class:`_Job`）。拿到之后立刻写进 ``_inflight``。
        holder.job_id = job_id
        self._inflight[entry.path] = job_id
        self._live[job_id] = (entry.path, edge)
        self._live[job_id] = (entry.path, edge)

    def _release(self, entry: GridEntry, holder: _Job) -> None:
        """收尾时把这一格从「在途」里摘出去 —— **只摘自己那一次**。

        ``shutdown()`` 会清空 ``_inflight`` 并换代。旧一代的任务若晚回来还照着
        老写法无条件 ``pop(entry.path)``，摘掉的就是**新一代**为同一格登记的那一条：
        ``_inflight`` 于是少数了（超过 ``_max_inflight`` 也不会再补），
        而 ``cancel()`` 更是再也够不着那个真正在跑的新任务。
        """
        if holder.job_id is not None:
            self._live.pop(holder.job_id, None)
        if holder.job_id is not None:
            self._live.pop(holder.job_id, None)
        if holder.job_id is not None and self._inflight.get(entry.path) == holder.job_id:
            del self._inflight[entry.path]

    def _on_result(self, entry: GridEntry, generation: int, holder: _Job, result: object) -> None:
        self._release(entry, holder)
        if generation != self._generation:
            self._dispatch()
            return
        if result is None:
            # 空相册没有封面：记下来别再反复问，界面会画文件夹图标。
            self._empty.add(entry.path)
            # 但「整个相册都读不出来」长得和空相册一模一样（一个文件夹图形），
            # 区别只有一句话 —— 那句话得说出来，否则用户只看到一张没有照片的卡片。
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
                # 相册的封面读不出来：记下「这一张不行」，**换下一张再试**。
                # 不这么做，一张云盘上没下完的照片就会让整张相册卡片一直挂着它 ——
                # 用户看到的是一张永远出不来、也永远换不掉的黑封面。
                cover = self._covers.setdefault(entry.path, _Cover())
                cover.skipped.add(holder.cover)
                cover.message = message
                # 重新排一次队（这一格本身不记进 ``_failed``：换个封面也许能成）。
                # 整个相册都试完之后 ``folder_cover`` 会返回 ``None``，那一支见
                # :meth:`_on_result` —— 于是「都试过了」不会变成无休止的重试。
                self._queue.add(entry.path, priority=VISIBLE)
                self._dispatch()
                return
            self._failed.add(entry.path)
            self.failed.emit(entry.path, message)
        self._dispatch()


def _thumbnail_target(entry: GridEntry) -> GridEntry | None:
    """这一格要显示谁的缩略图。

    分组卡片在这里就换成它组内第一张照片：封面不是现场去找的，于是它和普通照片
    走完全一样的解码与缓存路径（同一张照片当好几个组的封面时也只解码一次）。
    空分组返回 ``None`` —— 没有封面可画，界面会画文件夹图形。
    """
    if isinstance(entry, GroupRef):
        return entry.cover
    return entry


__all__ = ["NO_EDGE", "ScaledThumbnail", "ThumbnailPipeline"]
