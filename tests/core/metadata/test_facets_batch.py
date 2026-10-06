"""``gallery.core.metadata.batch`` 的测试。

分组与排序的性能全押在这条路上：**一批一个子进程**、坏文件不拖垮整批、取消要真的停。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from gallery.core.errors import MetadataError
from gallery.core.metadata.batch import (
    PROGRESS_STEP,
    ExifToolBatchSource,
    PillowBatchSource,
    read_facets,
)
from gallery.core.metadata.facets import PhotoFacets


class FakeSource:
    """假的批量来源：按预置字典发数据，并记录自己被喂了多大一批。"""

    def __init__(self, data: Mapping[str, Mapping[str, object]], *, available: bool = True) -> None:
        self._data = data
        self._available = available
        self.batch_sizes: list[int] = []

    def available(self) -> bool:
        return self._available

    def read_many(
        self, paths: Sequence[Path], *, should_cancel: Callable[[], bool] | None = None
    ) -> Iterator[tuple[Path, Mapping[str, object]]]:
        self.batch_sizes.append(len(paths))
        for path in paths:
            if should_cancel is not None and should_cancel():
                return
            raw = self._data.get(path.name)
            if raw is not None:
                yield Path(path), raw


class FlakySource(FakeSource):
    """某一类文件读不出来，其余照常 —— 批量读必须能跳过坏文件。"""

    def read_many(
        self, paths: Sequence[Path], *, should_cancel: Callable[[], bool] | None = None
    ) -> Iterator[tuple[Path, Mapping[str, object]]]:
        for path, raw in super().read_many(paths, should_cancel=should_cancel):
            if path.suffix == ".png":
                raise MetadataError(f"读不了：{path.name}")
            yield path, raw


def _paths(tmp_path: Path, count: int) -> list[Path]:
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"{index:03}.jpg"
        path.write_bytes(b"x")
        paths.append(path)
    return paths


def test_read_facets_extracts_values(tmp_path: Path) -> None:
    paths = _paths(tmp_path, 3)
    source = FakeSource(
        {
            "000.jpg": {"EXIF:DateTimeOriginal": "2024:05:16 10:00:00"},
            "002.jpg": {"EXIF:DateTimeOriginal": "2023:01:02 08:00:00"},
        }
    )

    result = read_facets(paths, source=source)

    assert set(result) == {paths[0], paths[2]}
    assert result[paths[0]] == PhotoFacets(
        captured_at=result[paths[0]].captured_at,
        year=2024,
        month=5,
        day=16,
        hour=10,
        minute=0,
    )
    assert result[paths[2]].year == 2023


@pytest.mark.parametrize("total", [0, 1, 7, 400, 401, 800, 5000])
def test_progress_always_lands_on_the_total(tmp_path: Path, total: int) -> None:
    """**收尾一定落在「读完了」上**，与总数能不能被档位整除无关。

    进度按 :data:`PROGRESS_STEP` 报，总数 5000 时最后一次整档报的是 4800，不补一次就像卡住。"""
    paths = _paths(tmp_path, total)
    source = FakeSource({f"{index:03d}.jpg": {"EXIF:ISO": "100"} for index in range(total)})
    seen: list[tuple[int, int]] = []

    read_facets(paths, source=source, on_progress=lambda done, all_: seen.append((done, all_)))

    if total == 0:
        assert seen == [], "没有照片就不该报进度"
        return
    assert seen[0] == (1, total), "第一张立刻报一次，横幅要马上活过来"
    assert seen[-1] == (total, total)
    assert all(done <= all_ for done, all_ in seen), "进度不能越过总数"
    assert seen == sorted(seen), "进度必须单调"


def test_progress_is_not_reported_once_per_photo(tmp_path: Path) -> None:
    """**不按张报**：每一次都是跨线程的 Qt 信号，五千张就是五千次投递。

    粒度见 :data:`PROGRESS_STEP`（与一次 exiftool 子进程对齐）：横幅本来就一跳一跳的。"""
    paths = _paths(tmp_path, 2000)
    source = FakeSource({f"{index:03d}.jpg": {"EXIF:ISO": "100"} for index in range(2000)})
    seen: list[tuple[int, int]] = []

    read_facets(paths, source=source, on_progress=lambda done, all_: seen.append((done, all_)))

    assert len(seen) <= 2000 // PROGRESS_STEP + 2, f"2000 张报了 {len(seen)} 次，太细了"
    assert seen[-1] == (2000, 2000)


def test_read_facets_reports_progress_to_the_end(tmp_path: Path) -> None:
    """读不出来的那几个不再回调；不补一次的话进度条永远停在中间。"""
    paths = _paths(tmp_path, 3)
    source = FakeSource({"000.jpg": {"EXIF:ISO": "100"}, "001.jpg": {"EXIF:ISO": "200"}})
    seen: list[tuple[int, int]] = []

    read_facets(paths, source=source, on_progress=lambda done, total: seen.append((done, total)))

    assert seen[-1] == (3, 3)


def test_read_facets_skips_unreadable_files(tmp_path: Path) -> None:
    paths = _paths(tmp_path, 2)
    paths.append(tmp_path / "坏的.png")
    paths[-1].write_bytes(b"x")
    source = FlakySource({"000.jpg": {"EXIF:ISO": "100"}})

    result = read_facets(paths, source=source)

    assert set(result) == {paths[0]}


def test_read_facets_falls_back_when_source_is_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path, 1)
    fallback = FakeSource({"000.jpg": {"EXIF:ISO": "100"}})

    result = read_facets(paths, source=FakeSource({}, available=False), fallback=fallback)

    assert result[paths[0]].iso == 100
    assert fallback.batch_sizes == [1]


def test_read_facets_respects_cancellation(tmp_path: Path) -> None:
    paths = _paths(tmp_path, 5)
    source = FakeSource({path.name: {"EXIF:ISO": "100"} for path in paths})
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    result = read_facets(paths, source=source, should_cancel=should_cancel)

    assert 0 < len(result) < len(paths)


#: 假 exiftool：按收到的路径逐个回一份 JSON，字段由环境变量给。
_FAKE_EXIFTOOL = """
import json, os, sys
paths = [a for a in sys.argv[1:] if not a.startswith("-") and a.endswith(".jpg")]
payload = [
    {"SourceFile": p, "EXIF:DateTimeOriginal": os.environ.get("FAKE_DATE", "2024:05:16 10:00:00")}
    for p in paths
]
sys.stdout.write(json.dumps(payload))
"""


def _fake_exiftool(tmp_path: Path) -> Path:
    """造一个能当 ``exiftool`` 用的可执行文件（转调当前解释器跑那段假实现）。"""
    script = tmp_path / "假 exiftool.py"
    script.write_text(_FAKE_EXIFTOOL, encoding="utf-8")
    launcher = tmp_path / "假 exiftool"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def test_exiftool_source_parses_output_and_matches_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """输出里的 ``SourceFile`` 必须能和请求的路径对上，否则整批都算读失败。"""
    paths = _paths(tmp_path, 2)
    source = ExifToolBatchSource(str(_fake_exiftool(tmp_path)))

    got = dict(source.read_many(paths))

    assert set(got) == set(paths)
    assert got[paths[0]]["EXIF:DateTimeOriginal"] == "2024:05:16 10:00:00"


def test_exiftool_source_splits_into_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """argv 长度有上限（macOS 约 1MB），所以大批必须切块。"""
    paths = _paths(tmp_path, 7)
    calls: list[list[str]] = []
    real_run = ExifToolBatchSource._run

    def counting_run(
        self: ExifToolBatchSource, argv: Sequence[str], *, should_cancel: object
    ) -> str:
        calls.append(list(argv))
        return real_run(self, argv, should_cancel=lambda: False) or "[]"

    monkeypatch.setattr(ExifToolBatchSource, "_run", counting_run)
    source = ExifToolBatchSource("exiftool", batch_size=3)

    list(source.read_many(paths))

    assert [len([a for a in call if a.endswith(".jpg")]) for call in calls] == [3, 3, 1]


def test_exiftool_source_stops_between_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消必须能真的掐断，而不是把整库读完再丢弃。"""
    paths = _paths(tmp_path, 6)
    monkeypatch.setattr(
        ExifToolBatchSource, "_run", lambda self, argv, should_cancel: json.dumps([])
    )
    source = ExifToolBatchSource("exiftool", batch_size=2)

    assert list(source.read_many(paths, should_cancel=lambda: True)) == []


def test_exiftool_source_reports_unrunnable_tool(tmp_path: Path) -> None:
    """exiftool 跑不起来要抛错并说清楚，由界面决定怎么告诉用户。"""
    source = ExifToolBatchSource(str(tmp_path / "并不存在的工具"))
    paths = _paths(tmp_path, 1)

    with pytest.raises(MetadataError) as info:
        list(source.read_many(paths))

    assert "exiftool" in info.value.message


def test_pillow_fallback_reads_basic_exif(make_photo) -> None:  # type: ignore[no-untyped-def]
    """没装 exiftool 时走 Pillow：基础 EXIF 仍然拿得到（地点那几项拿不到）。"""
    path = make_photo(
        "a.jpg",
        exif={0x010F: "Canon", 0x0110: "Canon EOS R6"},
        exif_ifd={0x9003: "2024:05:16 10:00:00"},
    )

    result = read_facets([path], source=PillowBatchSource())

    facets = result[path]
    assert facets.camera == "Canon EOS R6"
    assert (facets.year, facets.month, facets.day) == (2024, 5, 16)
    assert facets.country is None


def test_the_exiftool_source_resolves_each_path_through_the_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**批量读走 :func:`gallery.core.paths.resolved` 那份带记忆的**。

    ``realpath`` 要 ``lstat`` 路径上的每一段，而同一张照片在**下一批、下一次打开
    同一个库、换一个分组字段重读**时都要重新付一遍，答案却逐字相同 —— 正是
    AGENTS.md 点名的「同一个问题反复问」。

    钉的是「第二次不再解析」，不是速度：第一次必然要真算一次（缓存未命中），
    而测试里量时间本来就不稳。
    """
    photo = tmp_path / "一张.jpg"
    photo.write_bytes(b"x")
    source = ExifToolBatchSource(executable=str(tmp_path / "没有这个命令"))

    calls: list[str] = []
    real = Path.resolve

    def counting(self: Path, *args: object, **kwargs: object) -> Path:
        calls.append(str(self))
        return real(self, *args, **kwargs)

    # 先真跑一次，把这个路径填进记忆里（第一次必然要算一次，那不是浪费）。
    with pytest.raises(MetadataError):
        list(source.read_many([photo]))

    monkeypatch.setattr(Path, "resolve", counting)
    with pytest.raises(MetadataError):
        list(source.read_many([photo]))  # 这一遍才是「重复劳动」

    assert calls == [], f"同一个路径被重复解析 {len(calls)} 次"
