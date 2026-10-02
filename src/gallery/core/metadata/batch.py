"""一次性读取一组照片的元数据。

界面选中一张就起一次 exiftool（约 50ms）这件事只适合「看当前这一张」。分组与排序
要对**当前层的每一张**做同样的事，一次一个子进程就是 5000 张 × 50ms ≈ 4 分钟 ——
功能等于不存在。所以这里把一批路径交给**同一个** exiftool 进程。

三个要点：

- **点名要标签**而不是拉全量（见 ``facets.WANTED_TAGS``）：5000 张的全量 JSON 有几十兆，
  解码它比排序本身还贵。
- **分批**：argv 长度有硬上限（macOS 的 ``ARG_MAX`` 约 1MB），一万个路径会直接 ``E2BIG``；
  分批顺便给了取消点。
- **可取消**：用 ``Popen`` 轮询而不是一次 ``run``，切库时能立刻 ``terminate``，
  不给野进程留在后台跑。
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

from gallery.core.cancellation import never_cancel
from gallery.core.errors import MetadataError
from gallery.core.metadata.exif import EXIFTOOL_EXECUTABLE, EXIFTOOL_TIMEOUT_SECONDS
from gallery.core.metadata.facets import WANTED_TAGS, PhotoFacets, extract

#: 一次交给 exiftool 的文件数。
#:
#: 越大越省进程启动开销（一次约 60ms），但一次失败要重读的也多。400 个一批时，
#: 5000 张照片的读取约 2~3 秒，取消延迟也在一秒多以内 —— 这个平衡点够用。
BATCH_SIZE: Final = 400

#: 轮询间隔：够细才叫得动取消，粗了又白费 CPU。
POLL_SECONDS: Final = 0.05


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
        前者要记进索引（那张照片就是没有拍摄时间，下次不必再问一遍），后者不记
        （权限问题、文件正被占用，下次还得再试）。少了这个区分，换一次分组字段
        就得把整库没写元数据的照片重新问一遍 exiftool。
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
        # 路径先转绝对：exiftool 回填的 SourceFile 就是我们给它的字面量，
        # 相对路径在不同工作目录下会认不出自己。
        argv = [self._executable, "-j", "-G", "-sort", "-charset", "utf8", "-q"]
        for tag in WANTED_TAGS:
            argv.append(f"-{tag}")
        argv.extend(str(Path(path).resolve()) for path in batch)
        payload = self._run(argv, should_cancel=should_cancel)
        if payload is None:
            return
        by_source = _by_source_file(payload)
        for path in batch:
            # exiftool 对能读的文件一定会给一条记录（哪怕里面只有 SourceFile）；
            # 完全没提到的那些（权限不足、坏文件）才是「压根没读出来」。
            yield Path(path), by_source.get(_normalized(Path(path).resolve()))

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
                        f"读取元数据超时（超过 {self._timeout:g} 秒）：这一批照片。",
                        detail=f"命令：{' '.join(argv[:3])} …",
                    )
        finally:
            if process.poll() is None:  # pragma: no cover - 兜底，正常路径走不到
                _terminate(process)

        if process.returncode not in (0, None) and not stdout:
            raise MetadataError(
                "exiftool 没有返回可用信息。",
                detail=f"退出码 {process.returncode}",
            )
        return stdout.decode("utf-8", "replace") if stdout else None


class PillowBatchSource:
    """降级来源：没有 exiftool 时逐张用 Pillow 读基础 EXIF。

    不起子进程，因此比「每张一个 exiftool」快得多；拿不到 IPTC / XMP，
    也就是国家、省州、城市、关键词那几项（界面据此把它们置灰并说明原因）。
    """

    def __init__(self) -> None:
        from gallery.core.metadata.exif import PillowSource

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


def _by_source_file(payload: str) -> dict[str, Mapping[str, object]]:
    """把 exiftool 的 JSON 输出按 ``SourceFile`` 归位。"""
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, list):
        return {}
    result: dict[str, Mapping[str, object]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        source = item.get("SourceFile")
        if isinstance(source, str):
            result[_normalized(Path(source))] = item
    return result


def _normalized(path: Path) -> str:
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
    for path, raw in chosen.read_many(paths, should_cancel=should_cancel):
        if raw is not None:
            # 读到但字段全空也要给出一份空快照：它就是「这张照片没有拍摄时间」。
            result[Path(path)] = extract(raw)
        done += 1
        if on_progress is not None:
            on_progress(done, total)
    if on_progress is not None and done < total:
        # 读不出来的那些不会再有回调，不补一次的话进度条会永远停在中间。
        on_progress(total, total)
    return result


__all__ = [
    "BATCH_SIZE",
    "BatchMetadataSource",
    "ExifToolBatchSource",
    "PillowBatchSource",
    "read_facets",
]
