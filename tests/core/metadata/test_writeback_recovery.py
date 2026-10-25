"""写回元数据出岔子之后，用户还捞得回来吗（写回必须先记账再动文件那条）。

**先记账再动文件**与**回滚也要回读校验**各钉在自己的文件里；这里钉的是它们在撤销栈上合起来的样子。"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.journal import (
    ACTION_METADATA,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PENDING,
    FieldChange,
    WriteRecord,
    append_record,
    read_records,
)
from gallery.core.metadata.undo import next_undo_label, pending_undo_runs, undo_last_write
from gallery.core.metadata.writeback import Assignment, write_metadata

CHANGE = FieldChange("XMP:City", "波尔多", "杭州")


class _RecordingWriter:
    """一个听话的写回工具：写进去就真的改了。"""

    def __init__(self, fields: Mapping[str, str] | None = None) -> None:
        self.fields: dict[str, str] = dict(fields or {})

    def available(self) -> bool:
        return True

    def read(self, path: Path) -> Mapping[str, str]:
        return dict(self.fields)

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        for item in assignments:
            if item.value:
                self.fields[item.tag] = item.value
            else:
                self.fields.pop(item.tag, None)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(root / "一张.jpg")
    paths.config_dir(root).mkdir()
    return root


@pytest.fixture
def photo(library: Path) -> PhotoRef:
    return PhotoRef.from_path(library / "一张.jpg")


def _record(library: Path, photo: PhotoRef, result: str) -> WriteRecord:
    return WriteRecord(
        action=ACTION_METADATA,
        source=photo.path,
        target=photo.path,
        result=result,
        timestamp=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        changes=(CHANGE,),
    )


def test_a_normal_write_is_one_undo_step_not_two(library: Path, photo: PhotoRef) -> None:
    """成功的一次写回在日志里是**成对**的（pending + 终态），而它只该值**一次** ⌘Z ——
    撤销栈按「终态顶掉 pending」处理，少了这步，用户改一次元数据要按两次 ⌘Z 才看到原值。
    """
    writer = _RecordingWriter({"XMP:City": "波尔多"})
    write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert [record.result for record in read_records(library)] == [RESULT_PENDING, RESULT_OK]
    runs = pending_undo_runs(library)
    assert len(runs) == 1, f"一次写回占了 {len(runs)} 摞"
    assert next_undo_label(library) == "编辑元数据"


def test_a_crash_between_journalling_and_writing_stays_undoable(
    library: Path, photo: PhotoRef
) -> None:
    """**进程死在「记完账、还没写完」那一刻** —— 那正是最需要 ⌘Z 的时候：这条 pending 没有
    终态（文件改没改过都不知道），撤销栈仍要把它压在栈顶，让用户自己决定撤不撤。
    """
    assert append_record(library, _record(library, photo, RESULT_PENDING))

    runs = pending_undo_runs(library)
    assert len(runs) == 1
    assert runs[0][0].changes == (CHANGE,), "原值没了就再也写不回去"

    result = undo_last_write(library, writer=_RecordingWriter({"XMP:City": "杭州"}))
    assert result is not None and result.action == ACTION_METADATA


def test_a_pending_record_loses_to_its_own_outcome(library: Path, photo: PhotoRef) -> None:
    """终态一到，那条 pending 就不再单独占一摞 —— 它已经说完了；关键是仍然只有一摞。"""
    assert append_record(library, _record(library, photo, RESULT_PENDING))
    assert append_record(library, _record(library, photo, RESULT_OK))

    runs = pending_undo_runs(library)
    assert len(runs) == 1, f"一记写回占了 {len(runs)} 摞"
    assert all(record.result != RESULT_PENDING for run in runs for record in run)


def test_a_failed_write_back_is_not_offered_as_undoable(library: Path, photo: PhotoRef) -> None:
    """**失败的写回不压栈**：文件压根没被改（回滚已经还原了），没什么可撤。

    pending 是「不知道改没改成」，failed 是「确认没改成」—— 压上去，⌘Z 会去重写没动过的字段。"""
    assert append_record(library, _record(library, photo, RESULT_PENDING))
    assert append_record(library, _record(library, photo, RESULT_FAILED))

    assert pending_undo_runs(library) == []


def test_the_intent_survives_when_the_write_itself_is_refused(
    library: Path, photo: PhotoRef
) -> None:
    """工具直接拒绝时：pending 与 failed 都在，且原值仍然齐着 —— 原值在日志里人才有得救。"""

    class _Refusing(_RecordingWriter):
        def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
            raise MetadataWriteError("磁盘已满，请释放空间后重试。", path=path)

    with pytest.raises(MetadataWriteError):
        write_metadata(
            library,
            photo,
            [Assignment("XMP:City", "杭州")],
            writer=_Refusing({"XMP:City": "波尔多"}),
        )

    records = read_records(library)
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_FAILED]
    assert records[0].changes == (CHANGE,)
