"""元数据写回（ADR 0030）：白名单、回读校验、回滚、记账、撤销。

**一律用假的 writer**：它能模拟「exiftool 说成功了却什么都没写进去」，而那正是回读校验的由来。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import SUPPORTED_SUFFIXES, PhotoRef
from gallery.core.metadata.journal import (
    ACTION_METADATA,
    ACTION_UNDO_METADATA,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PENDING,
    FieldChange,
    WriteRecord,
    read_records,
)
from gallery.core.metadata.place_edit import PlaceEdit, PlaceState, plan_place_edit
from gallery.core.metadata.undo import pending_undo_runs  # noqa: E402
from gallery.core.metadata.writeback import (
    CAPTURE_TIME_TAGS,
    COORDINATE_TAGS,
    EXIFTOOL_TIMEOUT_SECONDS,
    IPTC_PLACE_TAGS,
    PLACE_TAGS,
    ROLLBACK_TIMEOUT_SECONDS,
    UNSTORABLE_TAGS,
    WRITABLE_TAGS,
    XMP_PLACE_TAGS,
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
        #: **这个「容器」存不下的标签**：exiftool 往 HEIC 写 IPTC 时返回 0、不警告、把这几个
        #: 字段默默跳过 —— 少了它，「HEIC 地点写不进去」那个 bug 就复现不出来。
        self.unstorable: frozenset[str] = frozenset()
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
            if item.tag in self.unstorable:
                continue  # 静默丢弃：**不报错，也不影响同一批里别的字段**
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


def test_only_capture_time_and_position_tags_are_writable() -> None:
    """**白名单就是位置那十三个加拍摄时间那两个**，一个不多。

    「大部分元数据不该可编辑」只写在界面上，早晚有人从别的入口写进去（ADR 0032）。"""
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
    """**拍摄时间与坐标走同一条路**：守卫、回读校验、记账、撤销，一个都不少 —— 白名单的价值
    在于「加进来之后仍受同一套规矩管」，少了这条，新标签就可能绕过校验或不记账（ADR 0032）。
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


def _photo_with_suffix(library: Path, suffix: str) -> PhotoRef:
    """造一张**指定后缀**的照片：这一层只 ``stat`` 文件、只看后缀（ADR 0036 决策 2），
    不碰像素 —— 所以不需要造一张真的 HEIC，也不需要 pillow-heif。
    """
    path = library / f"一张{suffix}"
    path.write_bytes(b"not really a photo, just takes up a spot")
    return PhotoRef.from_path(path)


#: 三个地名层在计划里的形状：XMP 与 IPTC 各一份（ADR 0030 决策 1）。
PLACE_EDIT = (
    Assignment("XMP:City", "杭州"),
    Assignment("XMP:State", "浙江省"),
    Assignment("XMP:Country", "中国"),
    Assignment("IPTC:City", "杭州"),
    Assignment("IPTC:Province-State", "浙江省"),
    Assignment("IPTC:Country-PrimaryLocationName", "中国"),
)


@pytest.mark.parametrize("suffix", sorted(UNSTORABLE_TAGS))
def test_a_tag_the_container_cannot_store_is_never_sent_to_the_writer(
    library: Path, suffix: str
) -> None:
    """**表里说存不下的，一个都不许发出去。**

    发出去等于「exiftool 返回 0、什么都不写」，回读校验必定判它没写进去并整体回滚（ADR 0036）。"""
    writer = FakeWriter()
    ref = _photo_with_suffix(library, suffix)

    write_metadata(library, ref, (*PLACE_EDIT, *HANGZHOU), writer=writer)

    dropped = UNSTORABLE_TAGS[suffix]
    sent = {tag for batch in writer.writes for tag in batch}
    assert sent, "整批都被过滤掉了 —— 那不该走到写盘这一步"
    assert sent & dropped == set()


@pytest.mark.parametrize("suffix", sorted(UNSTORABLE_TAGS))
def test_the_place_still_lands_on_a_container_that_cannot_store_iptc(
    library: Path, suffix: str
) -> None:
    """**那个 bug 的完整复现**（ADR 0036）：假 writer 这一条真的会丢 IPTC，于是旧行为原样重演 ——
    IPTC 发出去被默默跳过、回读对不上、整体回滚把写成功的 XMP 也擦掉。"""
    writer = FakeWriter()
    writer.unstorable = UNSTORABLE_TAGS[suffix]
    ref = _photo_with_suffix(library, suffix)

    result = write_metadata(library, ref, PLACE_EDIT, writer=writer)

    assert result.written
    assert writer.fields["XMP:City"] == "杭州"
    assert writer.fields["XMP:State"] == "浙江省"
    assert writer.fields["XMP:Country"] == "中国"
    # 读回校验没被放松：回滚一次都没发生（真发生过一次的话 XMP 已经被擦掉了）
    assert len(writer.writes) == 1


@pytest.mark.parametrize("suffix", sorted(UNSTORABLE_TAGS))
def test_a_filtered_tag_never_reaches_the_journal(library: Path, suffix: str) -> None:
    """**没写过的字段不该出现在回滚记录里**：撤销时去「恢复」一个从未存在的 IPTC，
    反而会造出一条不存在的记录。
    """
    writer = FakeWriter()
    ref = _photo_with_suffix(library, suffix)

    result = write_metadata(library, ref, PLACE_EDIT, writer=writer)

    recorded = {change.tag for record in read_records(library) for change in record.changes}
    assert recorded == {change.tag for change in result.changes}
    assert recorded & UNSTORABLE_TAGS[suffix] == set()


def test_a_container_that_can_store_both_is_still_given_both(library: Path) -> None:
    """**别把另一半也砍掉**：JPEG 存得下 IPTC，两份照写（ADR 0030 决策 1）。"""
    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".jpg")

    write_metadata(library, ref, PLACE_EDIT, writer=writer)

    assert writer.fields["XMP:City"] == "杭州"
    assert writer.fields["IPTC:City"] == "杭州"
    assert writer.fields["IPTC:Province-State"] == "浙江省"
    assert writer.fields["IPTC:Country-PrimaryLocationName"] == "中国"


def test_the_coordinates_still_go_to_a_container_without_iptc(library: Path) -> None:
    """**过滤不许伤及无辜**：坐标在 HEIC 上一直是好的，别顺手把它也砍了。"""
    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".heic")

    write_metadata(library, ref, HANGZHOU, writer=writer)

    assert writer.fields["EXIF:GPSLatitude"] == "30.274150"
    assert writer.fields["EXIF:GPSLongitude"] == "120.155070"


def test_when_every_field_is_filtered_out_it_says_the_format_cannot_store_them(
    library: Path,
) -> None:
    """**「本来就没改」与「这个格式存不下」必须分开说**（ADR 0036 决策 4）：共用一句「没有
    要写入的改动」，用户会以为没保存成功、再点一次，而结果还是一样。
    """
    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".gif")
    capture = [Assignment("EXIF:DateTimeOriginal", "2024:01:02 03:04:05")]

    with pytest.raises(MetadataWriteError, match="存不下要写的字段") as raised:
        write_metadata(library, ref, capture, writer=writer)

    assert "没有要写入的改动" not in raised.value.message
    assert writer.writes == []  # 一次都没发出去


def test_the_two_kinds_of_nothing_to_write_are_told_apart(library: Path) -> None:
    """**「压根没让写」与「让写了但值一样」是两回事**（ADR 0036 决策 4）：前者本来就报错，
    后者是常态 —— 把「没改」记成一次改动会让 ⌘Z 去撤空事，所以它连日志都不该留。
    """
    writer = FakeWriter(fields={"XMP:City": "杭州"})
    ref = _photo_with_suffix(library, ".jpg")

    with pytest.raises(MetadataWriteError, match="没有要写入的改动"):
        write_metadata(library, ref, [], writer=writer)

    result = write_metadata(library, ref, [Assignment("XMP:City", "杭州")], writer=writer)

    assert not result.written
    assert result.changes == ()
    assert writer.writes == []
    assert read_records(library) == []


def test_the_storage_gap_table_only_names_tags_gallery_can_write() -> None:
    """**表里的标签必须都在白名单里** —— 写错一个字母，整条修复静悄悄地失效：HEIC 的地点
    继续写不进去，而没有一处报错。
    """
    for suffix, tags in UNSTORABLE_TAGS.items():
        for tag in tags:
            assert tag in WRITABLE_TAGS, f"{suffix} 那条写错了标签：{tag}"


def test_the_storage_gap_table_never_drops_the_xmp_place_names() -> None:
    """**XMP 一个都不许进表**（进表的那些容器都存得下它）—— 砍掉它就等于让地点
    彻底写不进去。
    """
    for suffix, tags in UNSTORABLE_TAGS.items():
        assert set(XMP_PLACE_TAGS) & tags == set(), f"{suffix} 那条把 XMP 也砍掉了"


@pytest.mark.parametrize("suffix", [".heic", ".heif", ".webp"])
def test_the_containers_without_iptc_drop_exactly_the_iptc_place_names(suffix: str) -> None:
    """**这三个容器丢掉的正好是 IPTC 那三个地名标签**，一个不多一个不少：坐标与拍摄时间在
    那几个容器里都是好的，多丢一个就是让地点又写不进去一次。
    """
    assert UNSTORABLE_TAGS[suffix] == frozenset(IPTC_PLACE_TAGS)


def test_the_storage_gap_table_only_covers_formats_the_library_shows() -> None:
    """**表里的每个后缀都得是 Gallery 认得的照片格式** —— 否则那是给不存在的东西开的口子。"""
    assert set(UNSTORABLE_TAGS) <= SUPPORTED_SUFFIXES


def test_bmp_is_left_to_the_message_exiftool_already_gives() -> None:
    """**BMP 不进这张表**：exiftool 对它明确报错并返回非 0，那条路本来就是通的 —— 这张表
    只装**不说话的**，把它塞进来等于把一句清楚的错误换成一次沉默的失败。
    """
    assert ".bmp" not in UNSTORABLE_TAGS


def test_the_place_edit_a_heic_gets_is_the_one_that_actually_lands(library: Path) -> None:
    """**从计划到写盘的整条路**（跨 :mod:`place_edit` 与 :mod:`writeback` 两层）。

    ``plan_place_edit`` 同时生成 XMP 与 IPTC 是对的；由写回这一层按容器去掉存不下的那一份。"""
    plan = plan_place_edit(PlaceState(), PlaceEdit(city="杭州", region="浙江省", country="中国"))
    assert {"IPTC:City", "XMP:City"} <= {item.tag for item in plan.assignments}

    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".heic")

    result = write_metadata(library, ref, plan.assignments, writer=writer)

    assert result.written
    assert writer.fields["XMP:City"] == "杭州"
    assert "IPTC:City" not in writer.fields
    assert {c.tag for c in result.changes} == {"XMP:City", "XMP:State", "XMP:Country"}


def test_writing_records_the_original_value_of_every_field(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"EXIF:GPSLatitude": "44.83779", "XMP:City": "波尔多"})

    result = write_metadata(library, photo, list(HANGZHOU), writer=writer)

    assert result.written
    assert result.entry is not None and result.entry.size == photo.size  # 假文件没变大
    records = read_records(library)
    # **两条**：先记「打算改」（原值与新值那时已齐），动完文件再记终态；少一条则崩溃窗口留不下东西。
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_OK]
    pending, record = records
    assert pending.action == record.action == ACTION_METADATA
    assert pending.changes == record.changes  # 两记的是同一件事
    assert record.source == record.target == photo.path
    # 原值记成**十进制度**（读回来的是 44.83779，归一成 44.837790）—— 撤销要靠它写回去。
    assert FieldChange("EXIF:GPSLatitude", "44.837790", "30.274150") in record.changes
    assert FieldChange("XMP:City", "波尔多", None) not in record.changes  # 没让删就没删


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


def test_a_silent_failure_is_caught_and_rolled_back(library: Path, photo: PhotoRef) -> None:
    """exiftool 返回成功却没写进去（标签不支持、格式拒绝）—— 靠**回读**才看得出来。回滚必须
    把原值写回去：文件已经被人动过（哪怕动得不多），留着半成品才是真事故。
    """
    writer = FakeWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True  # 第一次写没进去，回滚那次照样「没进去」，于是仍是原样

    with pytest.raises(MetadataWriteError, match="写入没有生效"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert writer.fields["XMP:City"] == "波尔多"
    assert writer.writes[-1] == ("XMP:City",)  # 回滚确实又写了一次
    records = read_records(library)
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_FAILED]


def test_coordinates_are_compared_as_numbers_not_as_text(library: Path, photo: PhotoRef) -> None:
    """写进去的是十进制度，读回来的是度分秒 —— **按字符串比会把每次成功都判成失败**：
    假 writer 刻意模拟真实行为，写入 ``30.274150`` 而回读给出 ``30 deg 16' 27"``。
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
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_FAILED]
    assert records[-1].message


#: 坐标加上三层地名 —— 撤销冲突那几条用例要的是「只有地名被外部改过，坐标没动」。
WRITTEN = (*HANGZHOU, Assignment("XMP:City", "杭州"), Assignment("IPTC:City", "杭州"))


def _written_record(library: Path, writer: FakeWriter, photo: PhotoRef) -> WriteRecord:
    write_metadata(library, photo, list(WRITTEN), writer=writer)
    return read_records(library)[0]


def test_undo_writes_the_original_values_back(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"XMP:City": "波尔多", "EXIF:GPSLatitude": "44.83779"})
    record = _written_record(library, writer, photo)

    result = undo_metadata(library, record, writer=writer)

    assert result.ok
    assert [change.tag for change in result.restored] == [change.tag for change in record.changes]
    assert writer.fields["EXIF:GPSLatitude"] == "44.837790"  # 归一后写回的那个值
    assert "EXIF:GPSLongitude" not in writer.fields
    assert writer.fields["XMP:City"] == "波尔多"
    assert read_records(library)[-1].action == ACTION_UNDO_METADATA


def test_undo_skips_a_field_that_was_changed_outside_gallery(
    library: Path, photo: PhotoRef
) -> None:
    """**别人改过的字段绝不覆盖**：用户按 ⌘Z 想撤的是自己刚才那一下，不是别人那一下 ——
    按原值盖回去就是毁掉他的编辑。
    """
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    for tag in ("XMP:City", "IPTC:City"):
        writer.fields[tag] = "苏州"  # 另一个工具（或另一次 Gallery 会话）改的

    result = undo_metadata(library, record, writer=writer)

    assert not result.ok
    assert [item.name for item in result.conflicts] == ["XMP:City", "IPTC:City"]
    assert writer.fields["XMP:City"] == "苏州"
    # 这里坐标那几条**没被外部改过**，所以是「部分冲突」：能撤的照撤 → 记 ok → 弹栈。
    # 「一项都没撤成」（那才是 failed、不弹栈、再按一次会重试）由下面那条用例负责。
    assert read_records(library)[-1].result == RESULT_OK


def test_undo_restores_the_fields_that_are_untouched(library: Path, photo: PhotoRef) -> None:
    """部分冲突时，**能撤的照撤**，冲突的那几个留在原处并报告。"""
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    writer.fields["XMP:City"] = "苏州"

    result = undo_metadata(library, record, writer=writer)

    assert "EXIF:GPSLatitude" not in writer.fields  # 坐标那四条照撤
    assert writer.fields["XMP:City"] == "苏州"  # 被外部改过的那一条留在原处
    assert len(result.restored) == len(record.changes) - 1


def test_an_undo_that_could_restore_nothing_stays_on_the_stack(
    library: Path, photo: PhotoRef
) -> None:
    """**一项都没撤成时，那条写回留在栈上** —— 再按一次 ⌘Z 会重试。

    部分冲突与全冲突是两种情形，必须分开：

    - **部分冲突**（有能撤的字段）：撤成功的那几条记 ``ok`` → 弹栈。它们确实回到原值了，
      再让用户去重撤一遍没有意义。
    - **全冲突**（一个都撤不了）：记 ``failed`` → :func:`pending_undo_runs` 的
      ``if not record.succeeded: continue`` 跳过它 → **栈顶仍是当初那条写回**。
      留着是因为外部那一方可能改回去：那样这一次重试就撤得掉。
    """
    writer = FakeWriter()
    record = _written_record(library, writer, photo)
    for change in record.changes:  # 每一项都在 Gallery 之外被改过
        writer.fields[change.tag] = "别人写的"

    result = undo_metadata(library, record, writer=writer)

    assert result.restored == ()
    assert read_records(library)[-1].result == RESULT_FAILED
    # 记的是 failed，于是下一次读栈时会跳过它 —— **栈上还有那条写回**。
    assert [run[0].result for run in pending_undo_runs(library)] == [RESULT_OK]


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

    exiftool 读回来是度分秒，我们写进去是十进制度：原样记进 journal，撤销会写出一个毫无意义的值。"""
    writer = FakeWriter({"EXIF:GPSLatitude": "35 deg 1' 16\"", "EXIF:GPSLatitudeRef": "North"})
    record = write_metadata(library, photo, list(WRITTEN), writer=writer)
    record = read_records(library)[0]
    latitude = next(c for c in record.changes if c.tag == "EXIF:GPSLatitude")

    assert latitude.before is not None
    assert float(latitude.before) == pytest.approx(35.0211, abs=1e-4)
    # 半球那一条压根不进记录：``before`` 归一之后与目标逐字相同，是空操作。
    assert "EXIF:GPSLatitudeRef" not in [change.tag for change in record.changes]


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


class _RollbackCorruptingWriter(FakeWriter):
    """写进去的值**总是错的**，但退出码为 0 —— exiftool 在这一支要处理的情形里正是如此：
    正向写入把经度写成别的值，回滚那次又写成另一个错值，于是原来的坐标被冲掉。"""

    def __init__(self, fields: Mapping[str, str]) -> None:
        super().__init__(fields)
        self.corrupted: set[str] = set()

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        super().write(path, assignments)
        for item in assignments:
            if item.tag in self.corrupted:
                self.fields[item.tag] = "0.000000"


def test_a_rollback_that_does_not_take_is_not_reported_as_a_success(
    library: Path, photo: PhotoRef
) -> None:
    """**回滚本身要被回读校验** —— 「按原值写回去」是一次请求，不是一次保证
    （exiftool 在这一支照样返回 0，见 :class:`_RollbackCorruptingWriter`）。"""
    writer = _RollbackCorruptingWriter(
        {
            "EXIF:GPSLatitude": "39.904200",
            "EXIF:GPSLatitudeRef": "N",
            "EXIF:GPSLongitude": "116.407400",
            "EXIF:GPSLongitudeRef": "E",
        }
    )
    writer.corrupted = {"EXIF:GPSLongitude"}

    with pytest.raises(MetadataWriteError) as raised:
        write_metadata(library, photo, list(HANGZHOU), writer=writer)

    message = str(raised.value)
    assert "EXIF:GPSLongitude" in message, message
    assert "没能恢复" in message, message
    # **关键**：不许出现「已经恢复原样」那句 —— 它此刻是假的。
    assert "已经恢复原样" not in message, message


def test_a_rollback_that_really_restores_still_says_so(library: Path, photo: PhotoRef) -> None:
    """反过来那条：回滚确实把原值写回去了，就照旧给「已经恢复原样」—— 校验是为了不再说
    假话，不是为了让每次失败都吓用户一跳。
    """
    writer = FakeWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True  # 两次写都没进去，于是文件一直是原样

    with pytest.raises(MetadataWriteError, match="已经恢复原样"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)


def test_the_intent_is_recorded_before_the_file_is_touched(library: Path, photo: PhotoRef) -> None:
    """**先记账，再动文件。**

    落一条 pending 只要几十微秒，写入却要起一到两次 exiftool 子进程，断电窗口里什么都留不下。"""
    seen: list[int] = []

    class _WatchingWriter(FakeWriter):
        def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
            # **写的那一刻**：日志里必须已经有那条「打算改」。
            seen.append(len(read_records(library)))
            super().write(path, assignments)

    writer = _WatchingWriter({})
    write_metadata(library, photo, list(HANGZHOU), writer=writer)

    assert seen, "根本没有调过写入"
    assert seen[0] >= 1, "动文件的那一刻，日志里一条记录都没有"


class _TimeoutRecordingWriter(FakeWriter):
    """记下每次 ``write`` 收到的 ``timeout``，其余行为与 :class:`FakeWriter` 一样。

    它**接受** ``timeout``，于是 :func:`_write_for_rollback` 的能力探测会命中 ——
    那正是这条用例要验的那条路。所以探测不能写成 ``isinstance(tool, ExifToolWriter)``：
    测试注入的都是假实现，那样**永远验不到**，而写出来永远为绿的回归测试比没有更坏。
    """

    def __init__(self, fields: dict[str, str] | None = None) -> None:
        super().__init__(fields)
        self.timeouts: list[float | None] = []

    def write(  # type: ignore[override]
        self,
        path: Path,
        assignments: Sequence[Assignment],
        *,
        timeout: float | None = None,
    ) -> None:
        self.timeouts.append(timeout)
        super().write(path, assignments)


def test_the_rollback_write_gets_a_shorter_timeout_than_the_first_one(
    library: Path, photo: PhotoRef
) -> None:
    """**回滚那次不等满两轮超时** —— 用户此刻正盯着一个不动的界面。

    ``_apply`` 这一段最坏要起 6 个 exiftool（两次 ``write`` 各两个 + 两次 ``read``），
    每个能卡满 :data:`EXIFTOOL_TIMEOUT_SECONDS`（15 秒）—— 最坏一分半。而「回滚没有
    成功」正是最需要尽快看到结果的一条路径：他刚按的 ⌘Z 现在处于说不清的状态里。
    """
    writer = _TimeoutRecordingWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True  # 第一次写没进去，于是走回滚

    with pytest.raises(MetadataWriteError, match="写入没有生效"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert writer.timeouts, "回滚那次压根没写"
    assert writer.timeouts[0] is None, "第一次写该用默认超时"
    assert writer.timeouts[-1] == ROLLBACK_TIMEOUT_SECONDS, (
        f"回滚那次用的超时是 {writer.timeouts[-1]}，应当是 {ROLLBACK_TIMEOUT_SECONDS}"
    )
    assert ROLLBACK_TIMEOUT_SECONDS < EXIFTOOL_TIMEOUT_SECONDS


def test_a_plain_writer_is_not_poked_with_a_timeout_it_does_not_take(
    library: Path, photo: PhotoRef
) -> None:
    """**协议里没有 ``timeout``**，所以假实现绝不能被塞一个它不认的参数。

    :class:`~gallery.core.metadata.writeback.MetadataWriter` 是 ``Protocol``，
    加上那个参数就得改所有假实现 —— 而它们根本不起进程。所以 ``_apply`` 用
    ``isinstance`` 判断：这条锁定那条分支走的正是「不传」。
    """
    writer = FakeWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True

    with pytest.raises(MetadataWriteError, match="写入没有生效"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)

    assert writer.writes[-1] == ("XMP:City",), "回滚确实发生了（假实现没被 timeout 弄坏）"


def test_normalize_turns_every_coordinate_tag_into_the_form_we_write() -> None:
    """**归一化按轴表分派**：四个坐标标签各归一成「我们会写回去的那一种写法」。

    它是撤销的立足点 —— 原值照抄 exiftool 的度分秒会让 ⌘Z 写回一个毫无意义的值，校验
    随即判它没写进去（ADR 0030 决策 2）。四个一起量是因为它们现在共用同一张轴表：
    漏一个的症状是**那一轴撤不回来**，而另外三轴正常。
    """
    from gallery.core.metadata.writeback import _normalize

    assert _normalize("EXIF:GPSLatitude", "30 deg 16' 26.94\"") == "30.274150"
    assert _normalize("EXIF:GPSLongitude", "120 deg 9' 18.25\"") == "120.155069"
    # 半球标记：exiftool 回 ``North``、Pillow 回 ``N``，都要归一成 ``N``。
    assert _normalize("EXIF:GPSLatitudeRef", "North") == "N"
    assert _normalize("EXIF:GPSLatitudeRef", "S") == "S"
    assert _normalize("EXIF:GPSLongitudeRef", "East") == "E"
    assert _normalize("EXIF:GPSLongitudeRef", "W") == "W"
    # 不是半球字母的一律当「没有」，别把一段乱文本当成方向。
    assert _normalize("EXIF:GPSLatitudeRef", "乱写") is None
    assert _normalize("EXIF:GPSLatitudeRef", None) is None
    # 非坐标标签照旧走文本那一支。
    assert _normalize("XMP:City", " 杭州 ") == "杭州"


def test_holds_compares_the_hemisphere_by_its_initial() -> None:
    """半球标记也**取首字母**比 —— 我们写 ``N``，exiftool 回读给的是 ``North``。"""
    from gallery.core.metadata.writeback import _holds

    assert _holds("N", "EXIF:GPSLatitudeRef", {"EXIF:GPSLatitudeRef": "North"})
    assert _holds("S", "EXIF:GPSLatitudeRef", {"EXIF:GPSLatitudeRef": "South"})
    assert _holds("E", "EXIF:GPSLongitudeRef", {"EXIF:GPSLongitudeRef": "East"})
    assert not _holds("N", "EXIF:GPSLatitudeRef", {"EXIF:GPSLatitudeRef": "South"})
    assert not _holds("N", "EXIF:GPSLatitudeRef", {}), "文件里没记方向，就不算写成"


def test_the_coordinate_tags_are_two_axes_of_two_tags() -> None:
    """``COORDINATE_TAGS`` 就是那四个标签、顺序固定（先值后半球）。

    顺序是给写回用的；「四个必须一起写」这件事也靠它盯着 —— 少一个的症状不是「少个字段」，
    是另一半留着**上一个位置**的值。
    """
    assert COORDINATE_TAGS == (
        "EXIF:GPSLatitude",
        "EXIF:GPSLatitudeRef",
        "EXIF:GPSLongitude",
        "EXIF:GPSLongitudeRef",
    )
