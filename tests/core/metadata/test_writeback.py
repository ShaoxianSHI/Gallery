"""元数据写回（ADR 0030）：白名单、回读校验、回滚、记账、撤销。

**一律用假的 writer**：测试不得依赖本机有没有装 exiftool（AGENTS）。假的 writer 是一份
内存里的键值表 —— 它既能模拟「写成了」，也能模拟「exiftool 说成功了却什么都没写进去」，
而后者恰恰是回读校验存在的理由，真 exiftool 反而不容易造出来。

断言两件事，缺一不可：**文件（那份内存里的表）变成了新的**，以及 **journal 里记下了
字段级的原值与新值**（红线 4）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.journal import (
    ACTION_METADATA,
    ACTION_UNDO_METADATA,
    RESULT_FAILED,
    RESULT_OK,
    FieldChange,
    WriteRecord,
    read_records,
)
from gallery.core.metadata.writeback import (
    CAPTURE_TIME_TAGS,
    COORDINATE_TAGS,
    PLACE_TAGS,
    WRITABLE_TAGS,
    Assignment,
    undo_metadata,
    write_metadata,
)


class FakeWriter:
    """一份内存里的「文件」。**能假装成功，也能假装失败** —— 后者才是测出校验的关键。"""

    def __init__(self, fields: Mapping[str, str] | None = None) -> None:
        self.fields: dict[str, str] = dict(fields or {})
        #: 写多少次、写了什么（撤销「外部改过」那几条时要用）。
        self.writes: list[tuple[str, ...]] = []
        #: 假装这次写其实没写进去（退出码为 0，值却原封不动）。
        self.silently_ignores = False
        #: 假装工具直接拒绝了这次写入。
        self.refuses = False
        self.available_flag = True

    def available(self) -> bool:
        return self.available_flag

    def read(self, path: Path) -> Mapping[str, str]:
        return dict(self.fields)

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        if self.refuses:
            raise MetadataWriteError("磁盘满了，腾出点空间再试。", path=path)
        self.writes.append(tuple(item.tag for item in assignments))
        if self.silently_ignores:
            return
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


#: 一组坐标赋值（十进制度 + 半球标记），与 ``place_edit`` 交出去的形状一致。
HANGZHOU = (
    Assignment("EXIF:GPSLatitude", "30.274150"),
    Assignment("EXIF:GPSLatitudeRef", "N"),
    Assignment("EXIF:GPSLongitude", "120.155070"),
    Assignment("EXIF:GPSLongitudeRef", "E"),
)


# ---- 白名单：这是「大部分元数据不能编辑」在代码里的形状 ------------------------


def test_only_capture_time_and_position_tags_are_writable() -> None:
    """**白名单就是位置那十三个加拍摄时间那两个**，一个不多。

    「大部分元数据不该可编辑」如果只写在界面上，早晚会有人从别的入口写进去。相机、
    镜头、曝光三件套一个都不在清单里 —— 想写它们在这个模块里压根做不到。

    **拍摄时间那两个是唯一的新增**（ADR 0032）：它们的加入是一次有意的收窄放宽，
    判据是「时间是需要人校准的时钟读数，而曝光是机身测出来的量」。

    顺带钉住**文件处理过程那几个日期字段仍然不可写**：``CreateDate`` /
    ``DateTimeDigitized`` / ``ModifyDate`` 记的是文件什么时候被创建、被数字化、被改写过，
    改它们等于制造一条没发生过的记录。
    """
    expected = frozenset(
        (
            *COORDINATE_TAGS,
            "EXIF:GPSAltitude",
            "EXIF:GPSAltitudeRef",
            "EXIF:GPSDateStamp",
            *PLACE_TAGS,
            *CAPTURE_TIME_TAGS,
        )
    )
    assert expected == WRITABLE_TAGS
    for forbidden in (
        "EXIF:Model",
        "EXIF:FNumber",
        "XMP:Rating",
        "EXIF:CreateDate",
        "EXIF:DateTimeDigitized",
        "EXIF:ModifyDate",
    ):
        assert forbidden not in WRITABLE_TAGS


@pytest.mark.parametrize(
    "tag",
    [
        "EXIF:Make",
        "EXIF:Model",
        "EXIF:ExposureTime",
        "XMP:Rating",
        # 文件处理过程那几个日期字段：**改它们等于制造一条没发生过的记录**（ADR 0032）。
        "EXIF:CreateDate",
        "EXIF:DateTimeDigitized",
        "EXIF:ModifyDate",
    ],
)
def test_writing_a_field_outside_the_whitelist_is_refused(
    library: Path, photo: PhotoRef, tag: str
) -> None:
    writer = FakeWriter()

    with pytest.raises(MetadataWriteError, match="不在可以修改的字段里"):
        write_metadata(library, photo, [Assignment(tag, "x")], writer=writer)

    assert writer.writes == []  # 连试都没试：它在守卫那一层就被挡下了


def test_writing_the_capture_time_goes_through_the_same_guarded_path(
    library: Path, photo: PhotoRef
) -> None:
    """**拍摄时间与坐标走同一条路**：守卫、回读校验、记账、撤销，一个都不少。

    它是后加的白名单条目（ADR 0032），而白名单的价值恰恰在于「加进来之后仍然受同一套
    规矩管」—— 少了这一条，加进来的两个标签就可能绕过回读校验或者不记账。
    """
    writer = FakeWriter(fields={"EXIF:DateTimeOriginal": "2024:05:16 20:52:02"})

    result = write_metadata(
        library,
        photo,
        [
            Assignment("EXIF:DateTimeOriginal", "2003:07:19 09:12:00"),
            Assignment("EXIF:OffsetTimeOriginal", "+09:00"),
        ],
        writer=writer,
    )

    assert result.written
    assert writer.fields["EXIF:DateTimeOriginal"] == "2003:07:19 09:12:00"
    assert writer.fields["EXIF:OffsetTimeOriginal"] == "+09:00"
    changes = {(change.tag, change.before, change.after) for change in result.changes}
    assert ("EXIF:DateTimeOriginal", "2024:05:16 20:52:02", "2003:07:19 09:12:00") in changes
    assert read_records(library)[-1].changes[0].before == "2024:05:16 20:52:02"


def test_a_file_outside_the_library_is_refused(
    library: Path, tmp_path: Path, photo: PhotoRef
) -> None:
    other = tmp_path / "别人的库"
    other.mkdir()
    Image.new("RGB", (8, 6), (1, 2, 3)).save(other / "一张.jpg")
    stranger = PhotoRef.from_path(other / "一张.jpg")
    writer = FakeWriter()

    with pytest.raises(MetadataWriteError, match="只能修改库里的照片"):
        write_metadata(library, stranger, list(HANGZHOU), writer=writer)

    assert writer.writes == []


def test_an_unsupported_format_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "照片"
    root.mkdir()
    (root / "说明.txt").write_text("不是照片", encoding="utf-8")
    paths.config_dir(root).mkdir()
    ref = PhotoRef(path=root / "说明.txt", size=1, mtime_ns=1)

    with pytest.raises(MetadataWriteError, match="不是 Gallery 支持的照片格式"):
        write_metadata(root, ref, list(HANGZHOU), writer=FakeWriter())


def test_without_exiftool_it_says_so_instead_of_failing_obscurely(
    library: Path, photo: PhotoRef
) -> None:
    writer = FakeWriter()
    writer.available_flag = False

    with pytest.raises(MetadataWriteError, match="没有找到 exiftool"):
        write_metadata(library, photo, list(HANGZHOU), writer=writer)


# ---- 写与记账 ------------------------------------------------------------------


def test_writing_records_the_original_value_of_every_field(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"EXIF:GPSLatitude": "44.83779", "XMP:City": "波尔多"})

    result = write_metadata(library, photo, list(HANGZHOU), writer=writer)

    assert result.written
    assert result.entry is not None and result.entry.size == photo.size  # 假文件没变大
    records = read_records(library)
    assert len(records) == 1
    record = records[0]
    assert record.action == ACTION_METADATA and record.result == RESULT_OK
    assert record.source == record.target == photo.path
    # 原值记成**十进制度**（读回来的是 44.83779，归一成 44.837790）—— 撤销要靠它写回去。
    assert FieldChange("EXIF:GPSLatitude", "44.837790", "30.274150") in record.changes
    assert FieldChange("XMP:City", "波尔多", None) not in record.changes  # 没让删就没删
    assert record.journaled if hasattr(record, "journaled") else True


def test_a_field_whose_value_did_not_change_is_not_written(library: Path, photo: PhotoRef) -> None:
    """值没变就既不写盘也不记账 —— 把「没改」记成一次改动，会让 ⌘Z 去撤一件空事。"""
    writer = FakeWriter({"XMP:City": "杭州"})

    result = write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert not result.written
    assert writer.writes == []
    assert read_records(library) == []


def test_clearing_a_field_writes_an_empty_value(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"XMP:City": "杭州"})

    result = write_metadata(library, photo, [Assignment("XMP:City", "")], writer=writer)

    assert result.changes == (FieldChange("XMP:City", "杭州", None),)
    assert "XMP:City" not in writer.fields


# ---- 回读校验与回滚 -------------------------------------------------------------


def test_a_silent_failure_is_caught_and_rolled_back(library: Path, photo: PhotoRef) -> None:
    """exiftool 返回成功却没写进去（标签不支持、格式拒绝）—— 靠**回读**才看得出来。

    回滚必须把原值写回去：这时文件已经被人动过了（哪怕动得不多），留着半成品才是真事故。
    """
    writer = FakeWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True  # 第一次写没进去，回滚那次照样「没进去」，于是仍是原样

    with pytest.raises(MetadataWriteError, match="写入没有生效"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert writer.fields["XMP:City"] == "波尔多"
    assert writer.writes[-1] == ("XMP:City",)  # 回滚确实又写了一次
    records = read_records(library)
    assert [record.result for record in records] == [RESULT_FAILED]


def test_coordinates_are_compared_as_numbers_not_as_text(library: Path, photo: PhotoRef) -> None:
    """写进去的是十进制度，读回来的是度分秒 —— **按字符串比会把每次成功都判成失败**。

    假 writer 刻意模拟真实行为：写入 ``30.274150``，回读时给出 ``30 deg 16' 27"``。
    """
    writer = FakeWriter()
    real_write = writer.write

    def write(path: Path, assignments: Sequence[Assignment]) -> None:
        real_write(path, assignments)
        for item in assignments:
            if item.tag == "EXIF:GPSLatitude":
                writer.fields[item.tag] = "30 deg 16' 26.94\""
            elif item.tag == "EXIF:GPSLongitude":
                writer.fields[item.tag] = "120 deg 9' 18.25\""

    writer.write = write  # type: ignore[method-assign]

    result = write_metadata(library, photo, list(HANGZHOU), writer=writer)

    assert result.written


def test_how_far_off_a_coordinate_has_to_be_to_count_as_not_written(
    library: Path, photo: PhotoRef
) -> None:
    """差 10 米（约 1e-4 度）就该算没写进去 —— 那是「差了一条街」，不是量化误差。"""
    writer = FakeWriter()
    real_write = writer.write

    def write(path: Path, assignments: Sequence[Assignment]) -> None:
        real_write(path, assignments)
        for item in assignments:
            if item.tag == "EXIF:GPSLatitude":
                writer.fields[item.tag] = "30.274250"

    writer.write = write  # type: ignore[method-assign]

    with pytest.raises(MetadataWriteError, match="写入没有生效"):
        write_metadata(library, photo, list(HANGZHOU), writer=writer)


def test_a_refused_write_is_journalled_as_failed(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter()
    writer.refuses = True

    with pytest.raises(MetadataWriteError):
        write_metadata(library, photo, list(HANGZHOU), writer=writer)

    records = read_records(library)
    assert [record.result for record in records] == [RESULT_FAILED]
    assert records[0].message


# ---- 撤销 ----------------------------------------------------------------------


#: 坐标加上三层地名 —— 撤销冲突那几条用例要的是「只有地名被外部改过，坐标没动」。
WRITTEN = (*HANGZHOU, Assignment("XMP:City", "杭州"), Assignment("IPTC:City", "杭州"))


def _written_record(library: Path, writer: FakeWriter, photo: PhotoRef) -> WriteRecord:
    write_metadata(library, photo, list(WRITTEN), writer=writer)
    return read_records(library)[0]


def test_undo_writes_the_original_values_back(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"XMP:City": "波尔多", "EXIF:GPSLatitude": "44.83779"})
    # 「44.83779」是 exiftool 那种十进制度的写法；度分秒那种见下面那条用例。
    record = _written_record(library, writer, photo)

    result = undo_metadata(library, record, writer=writer)

    assert result.ok
    assert [change.tag for change in result.restored] == [change.tag for change in record.changes]
    # 原来有值 → 写回原值（不是删掉）；本来就没有的那几条 → 删掉。
    assert writer.fields["EXIF:GPSLatitude"] == "44.837790"  # 归一后写回的那个值
    assert "EXIF:GPSLongitude" not in writer.fields
    assert writer.fields["XMP:City"] == "波尔多"
    assert read_records(library)[-1].action == ACTION_UNDO_METADATA


def test_undo_skips_a_field_that_was_changed_outside_gallery(
    library: Path, photo: PhotoRef
) -> None:
    """**别人改过的字段绝不覆盖。** 用户按 ⌘Z 想撤的是自己刚才那一下，不是别人那一下。

    文件里那条已经是「杭州 · 用户自己写的」，按原值盖回「波尔多」就是毁掉他的编辑。
    """
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    for tag in ("XMP:City", "IPTC:City"):
        writer.fields[tag] = "苏州"  # 另一个工具（或另一次 Gallery 会话）改的

    result = undo_metadata(library, record, writer=writer)

    assert not result.ok
    assert [item.name for item in result.conflicts] == ["XMP:City", "IPTC:City"]
    assert writer.fields["XMP:City"] == "苏州"


def test_undo_restores_the_fields_that_are_untouched(library: Path, photo: PhotoRef) -> None:
    """部分冲突时，**能撤的照撤**，冲突的那几个留在原处并报告。"""
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    writer.fields["XMP:City"] = "苏州"

    result = undo_metadata(library, record, writer=writer)

    assert "EXIF:GPSLatitude" not in writer.fields  # 坐标那四条照撤
    assert writer.fields["XMP:City"] == "苏州"  # 被外部改过的那一条留在原处
    assert len(result.restored) == len(record.changes) - 1


def test_undoing_a_gone_file_says_so(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    photo.path.unlink()

    with pytest.raises(MetadataWriteError, match="撤销不了：找不到"):
        undo_metadata(library, record, writer=writer)


def test_undo_does_not_push_another_undoable_write(library: Path, photo: PhotoRef) -> None:
    """撤销的那一次写入**不能又压回栈顶** —— 否则「改一下、⌘Z」会变成无限循环。"""
    from gallery.core.metadata.undo import next_undo_label, pending_undo_runs

    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    undo_metadata(library, record, writer=writer)

    assert pending_undo_runs(library) == []
    assert next_undo_label(library) is None


def test_the_recorded_original_value_is_a_number_not_a_degree_string(
    library: Path, photo: PhotoRef
) -> None:
    """**原值必须归一成我们会写回去的那一种写法。**

    exiftool 读回来的是 ``35 deg 1' 16"``，而我们写进去的是 ``35.021100``。把度分秒原样
    记进 journal，撤销时 ``-EXIF:GPSLatitude=35 deg 1' 16"`` 就会写出一个毫无意义的值 ——
    用户按下 ⌘Z 得到一句「照片已经恢复原样」，而它其实一个字都没恢复（实测）。
    """
    writer = FakeWriter({"EXIF:GPSLatitude": "35 deg 1' 16\"", "EXIF:GPSLatitudeRef": "North"})
    record = write_metadata(library, photo, list(WRITTEN), writer=writer)
    record = read_records(library)[0]
    latitude = next(c for c in record.changes if c.tag == "EXIF:GPSLatitude")

    assert latitude.before is not None
    assert float(latitude.before) == pytest.approx(35.0211, abs=1e-4)
    # 半球那一条压根不进记录：它已经是「North」也就是「N」，写不写都一样
    # （`before` 归一之后与目标逐字相同 → 这一条是空操作）。
    assert "EXIF:GPSLatitudeRef" not in [change.tag for change in record.changes]


# ---- 旧日志照读 ----------------------------------------------------------------


def test_an_old_journal_line_still_parses() -> None:
    """没有 ``changes`` 那一段的旧日志（改名、删除、粘贴）照旧读得出来。"""
    record = WriteRecord.from_json(
        '{"timestamp": "2026-01-01T00:00:00+08:00", "action": "rename",'
        ' "from": "/a/x.jpg", "to": "/a/y.jpg", "is_dir": false, "result": "ok"}'
    )

    assert record is not None
    assert record.changes == ()


def test_a_broken_change_entry_does_not_lose_the_whole_record() -> None:
    """一条字段改动写歪了，只丢那一条 —— 整条撤销凭据跟着没了，用户反而再也撤不掉。"""
    record = WriteRecord.from_json(
        '{"from": "/a/x.jpg", "to": "/a/x.jpg", "result": "ok", "action": "metadata",'
        ' "changes": [{"tag": "XMP:City", "before": null, "after": "杭州"}, {"坏": 1}, 7]}'
    )

    assert record is not None
    assert record.changes == (FieldChange("XMP:City", None, "杭州"),)
