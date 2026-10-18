"""一次性读取一组照片的元数据。

逐张起 exiftool（约 50ms/张）只适合「看当前这一张」，5000 张就是 4 分钟；这里把一批路径交给
**同一个** exiftool 进程，并只点名要标签（``facets.WANTED_TAGS``）、分批（argv 有 ``ARG_MAX``
上限，顺带给取消点）、用 ``Popen`` 轮询而不是一次 ``run``（切库时能立刻 ``terminate``）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol

from gallery.core import paths
from gallery.core.cancellation import never_cancel
from gallery.core.errors import MetadataError
from gallery.core.metadata.exif import (
    EXIFTOOL_CHARSET_ARGS,
    EXIFTOOL_EXECUTABLE,
    EXIFTOOL_TIMEOUT_SECONDS,
)
from gallery.core.metadata.facets import WANTED_TAGS, PhotoFacets, extract

#: 一次交给 exiftool 的文件数。越大越省进程启动开销（约 60ms），但一次失败要重读的也多；
#: 400 个一批时 5000 张约 2~3 秒，取消延迟在一秒多以内。
BATCH_SIZE: Final = 400

#: 轮询间隔：够细才叫得动取消，粗了又白费 CPU。
POLL_SECONDS: Final = 0.05

#: 报进度的粒度（张），与 :data:`BATCH_SIZE` 对齐 —— 一跳正好对应一次 exiftool 子进程。
#: **每张照片报一次是错的**：界面那一次回调是跨线程的 Qt 信号，五千张就是五千次投递。
PROGRESS_STEP: Final = BATCH_SIZE


class BatchMetadataSource(Protocol):
    """批量来源：默认是 exiftool，测试注入假实现。"""

    def available(self) -> bool:
        """现在能不能用。"""
        ...

    def read_many(
        self, paths: Sequence[Path], *, should_cancel: Callable[[], bool] | None = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        """依次产出 ``(路径, 原始键值)``。

        **「读到了但里面什么都没有」与「压根没读出来」是两件事**，用 ``None`` 区分：
        前者要记进索引（那张照片就是没有拍摄时间），后者不记（权限问题、文件被占用，下次
        还得再试）。少了这个区分，换一次分组字段就得把整库没写元数据的照片重新问一遍。
        """
        ...


class ExifToolBatchSource:
    """真正去起 exiftool 子进程的实现（一次读一批）。"""

    def __init__(
        self,
        executable: str = EXIFTOOL_EXECUTABLE,
        *,
        timeout: float = EXIFTOOL_TIMEOUT_SECONDS,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._executable = executable
        self._timeout = timeout
        self._batch_size = max(1, batch_size)

    @property
    def executable(self) -> str:
        return self._executable

    def available(self) -> bool:
        return shutil.which(self._executable) is not None

    def read_many(
        self, paths: Sequence[Path], *, should_cancel: Callable[[], bool] | None = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        cancelled = should_cancel or never_cancel
        for start in range(0, len(paths), self._batch_size):
            if cancelled():
                return
            batch = paths[start : start + self._batch_size]
            yield from self._read_batch(batch, should_cancel=cancelled)

    def _read_batch(
        self, batch: Sequence[Path], *, should_cancel: Callable[[], bool]
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        # guard: allow-long-comment（绝对路径与记忆版 resolved() 少一条就认不出自己或慢十倍）
        # 路径先转绝对：exiftool 回填的 SourceFile 就是我们给它的字面量，相对路径在不同工作
        # 目录下会认不出自己。**每张照片只解析一次，argv 与归位共用** —— ``resolve()`` 走的是
        # 一整条 ``realpath`` 系统调用链，而 :func:`gallery.core.paths.resolved` 那份带记忆，
        # 下一批、下一次打开同一个库、换一个分组字段重读时答案都逐字相同。
        resolved = [paths.resolved(path) for path in batch]
        argv = [self._executable, "-j", "-G", "-sort", *EXIFTOOL_CHARSET_ARGS, "-q"]
        for tag in WANTED_TAGS:
            argv.append(f"-{tag}")
        argv.extend(str(path) for path in resolved)
        payload = self._run(argv, should_cancel=should_cancel)
        if payload is None:
            return
        by_source = _BySourceFile(payload)
        for original, absolute in zip(batch, resolved, strict=True):
            # exiftool 对能读的文件一定会给一条记录（哪怕里面只有 SourceFile）；
            # 完全没提到的那些（权限不足、坏文件）才是「压根没读出来」。
            yield Path(original), by_source.get(absolute)

    def _run(self, argv: Sequence[str], *, should_cancel: Callable[[], bool]) -> str | None:
        """跑一次 exiftool，返回它的 stdout；取消或失败返回 ``None``。"""
        try:
            process = subprocess.Popen(  # noqa: S603 - argv 是自己拼的，没有 shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MetadataError(
                f"无法运行 exiftool：{exc.strerror or exc}。",
                detail=str(exc),
            ) from exc

        deadline = time.monotonic() + self._timeout
        try:
            while True:
                try:
                    stdout, _stderr = process.communicate(timeout=POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if should_cancel():
                    _terminate(process)
                    return None
                if time.monotonic() > deadline:
                    _terminate(process)
                    raise MetadataError(
                        f"读取元数据超时，超过 {self._timeout:g} 秒：这一批照片。",
                        detail=f"命令：{' '.join(argv[:3])} …",
                    )
        finally:
            if process.poll() is None:  # pragma: no cover - 回退分支，正常路径走不到
                _terminate(process)

        if process.returncode not in (0, None) and not stdout:
            raise MetadataError(
                "exiftool 没有返回可用信息。",
                detail=f"退出码 {process.returncode}",
            )
        return stdout.decode("utf-8", "replace") if stdout else None


class PillowBatchSource:
    """降级来源：没有 exiftool 时逐张用 Pillow 读基础 EXIF（不起子进程，比逐张起 exiftool 快得多；
    拿不到 IPTC / XMP，界面据此把国家、省州、城市、关键词几项置灰）。"""

    def __init__(self) -> None:
        from gallery.core.metadata.pillow_fallback import PillowSource

        self._source = PillowSource()

    def available(self) -> bool:
        return True

    def read_many(
        self, paths: Sequence[Path], *, should_cancel: Callable[[], bool] | None = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        cancelled = should_cancel or never_cancel
        for path in paths:
            if cancelled():
                return
            try:
                yield Path(path), self._source.read(path)
            except MetadataError:
                yield Path(path), None  # 读不出来：下次还要再试，不进索引


class _BySourceFile:
    """按 ``SourceFile`` 把 exiftool 的 JSON 输出归位。
    **先用字面量查，查不到才建一份按 ``realpath`` 的慢表。** exiftool 把 ``SourceFile`` 原样
    回填成我们拼进 argv 的那个字符串，而那个字符串已经 ``resolve()`` 过 —— 绝大多数记录一次
    ``normcase``（零系统调用）就能对上。``realpath`` 要 ``lstat`` 路径上每一段，五层的库就是五次
    系统调用；原实现对每张照片做三次，五千张就是一万五千次，比 exiftool 跑完那一批还贵。
    """

    __slots__ = ("_literal", "_resolved")

    def __init__(self, payload: str) -> None:
        self._resolved: dict[str, Mapping[str, object]] | None = None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = []
        self._literal: dict[str, Mapping[str, object]] = {}
        for item in parsed if isinstance(parsed, list) else []:
            if not isinstance(item, dict):
                continue
            source = item.get("SourceFile")
            if isinstance(source, str):
                self._literal[os.path.normcase(source)] = item

    def get(self, path: Path) -> Mapping[str, object] | None:
        key = os.path.normcase(str(path))
        record = self._literal.get(key)
        if record is not None:
            return record
        return self._slow().get(key)

    def _slow(self) -> dict[str, Mapping[str, object]]:
        """第一次真正查不中时才建的那份慢表（建一次就留着）。"""
        if self._resolved is None:
            slow: dict[str, Mapping[str, object]] = {}
            for key, item in self._literal.items():
                slow[_normalized(key)] = item
            self._resolved = slow
        return self._resolved


def _normalized(path: str | Path) -> str:
    """比路径用的标准写法：解析符号链接、统一分隔符、去掉 ``./``。"""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:  # pragma: no cover - realpath 基本不会失败
        return os.path.normcase(os.fspath(path))


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """收掉子进程：先礼后兵，实在不停才杀。"""
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:  # pragma: no cover - exiftool 通常很配合
        process.kill()
        process.wait(timeout=2)


def read_facets(
    paths: Sequence[Path],
    *,
    source: BatchMetadataSource | None = None,
    fallback: BatchMetadataSource | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, PhotoFacets]:
    """读取这批照片的分组 / 排序字段。
    :param source: 替换默认的 exiftool 批量来源（测试用）。
    :param fallback: 主来源不可用时用谁（默认 Pillow）。
    :param on_progress: ``(已读, 总数)``，每批之后回调一次，供界面报进度。
    :raises MetadataError: 主来源存在但整体失败（例如 exiftool 跑不起来）。
    """
    chosen = source if source is not None else ExifToolBatchSource()
    if not chosen.available():
        chosen = fallback if fallback is not None else PillowBatchSource()
    result: dict[Path, PhotoFacets] = {}
    total = len(paths)
    done = 0
    reported = 0
    for done, (path, raw) in enumerate(
        chosen.read_many(paths, should_cancel=should_cancel), start=1
    ):
        if raw is not None:
            # 读到但字段全空也要给出一份空快照：它就是「这张照片没有拍摄时间」。
            result[Path(path)] = extract(raw)
        # **按 :data:`PROGRESS_STEP` 报，不按张报**（理由见那个常量的注释）：
        # 第一张立刻报一次（横幅要马上活过来），之后每满一档报一次。
        if on_progress is not None and (done == 1 or done // PROGRESS_STEP != reported):
            on_progress(done, total)
            reported = done // PROGRESS_STEP
    if on_progress is not None and reported * PROGRESS_STEP != total:
        # 收尾一定落在「读完了」上，不管上一次报的是第几张：进度条停在 4800/5000
        # 就像卡住了。读不出来的那些不会再有回调，所以这一次是必需的，不是锦上添花。
        on_progress(total, total)
    return result


__all__ = [
    "BATCH_SIZE",
    "BatchMetadataSource",
    "ExifToolBatchSource",
    "PillowBatchSource",
    "read_facets",
]
