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
from gallery.core.metadata.writeback import (
    CAPTURE_TIME_TAGS,
    COORDINATE_TAGS,
    IPTC_PLACE_TAGS,
    PLACE_TAGS,
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
        #: **这个「容器」存不下的标签** —— 模拟 exiftool 往 HEIC 写 IPTC 时的真实行为：
        #: 返回 0、不警告、**只把这几个字段默默跳过**，其余照写。少了这一项，「HEIC 地点
        #: 写不进去」那个 bug 在测试里就复现不出来（假的 writer 什么都存得下）。
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


# ---- 容器存不下的标签压根不发（ADR 0036）----------------------------------------


def _photo_with_suffix(library: Path, suffix: str) -> PhotoRef:
    """造一张**指定后缀**的照片。

    这一层只 ``stat`` 文件、只看后缀，压根不碰像素 ——``is_supported_photo`` 的文档原话
    就是「不检查文件是否真的能解码」，而 ADR 0036 决策 2 定的判据正是**后缀**。
    所以这里不需要造一张真的 HEIC，也不需要 pillow-heif。
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

    这条钉住的是 ADR 0036 的整件事：发出去等于「exiftool 返回 0、什么都不写」，回读校验
    必定判它没写进去，紧接着的**整体回滚会把已经写成功的 XMP 一起擦掉** —— HEIC 的地点
    写不进去就是从这儿来的。逐个后缀过一遍，表里每一条都要被真的拦住。
    """
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
    """**这就是用户遇到的那个 bug 的完整复现**（ADR 0036）。

    假的 writer 这一条**真的会丢 IPTC**（``unstorable``），于是今天的行为会原样重演：
    IPTC 发出去 → 被默默跳过 → 回读对不上 → **整体回滚把已经写成功的 XMP 一起擦掉** →
    地点一个字都没写进去。

    修好之后：存不下的压根不发，XMP 落地，回滚一次都没发生。
    """
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
    """**没写过的字段不该出现在回滚记录里。**

    撤销时去「恢复」一个从未存在过的 IPTC，反而会造出一条不存在的记录。
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
    """**「本来就没改」与「这个格式存不下」必须分开说**（ADR 0036 决策 4）。

    共用一句「没有要写入的改动」的话，用户会以为自己没保存成功、再点一次保存 ——
    那一次还是同样的结果。
    """
    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".gif")
    capture = [Assignment("EXIF:DateTimeOriginal", "2024:01:02 03:04:05")]

    with pytest.raises(MetadataWriteError, match="存不下要写的字段") as raised:
        write_metadata(library, ref, capture, writer=writer)

    assert "没有要写入的改动" not in raised.value.message
    assert writer.writes == []  # 一次都没发出去


def test_the_two_kinds_of_nothing_to_write_are_told_apart(library: Path) -> None:
    """**「压根没让写」与「让写了但值一样」是两回事**，只有后者该安静（ADR 0036 决策 4）。

    前者本来就是一句报错；后者是常态 —— 把「没改」记成一次改动，会让 ⌘Z 去撤一件空事，
    所以它连日志都不该留。
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


# ---- 那张表本身的守护 ---------------------------------------------------------


def test_the_storage_gap_table_only_names_tags_gallery_can_write() -> None:
    """**表里的标签必须都在白名单里** —— 写错一个字母，整条修复静悄悄地失效。

    而它失效的方式正是最难发现的那种：HEIC 的地点继续写不进去，没有一处报错。
    """
    for suffix, tags in UNSTORABLE_TAGS.items():
        for tag in tags:
            assert tag in WRITABLE_TAGS, f"{suffix} 那条写错了标签：{tag}"


def test_the_storage_gap_table_never_drops_the_xmp_place_names() -> None:
    """**XMP 一个都不许进表** —— 每一个进表的容器都存得下它（实测）。

    砍掉 XMP 就等于把今天这个 bug 换个格式重演一遍：地点彻底写不进去。
    """
    for suffix, tags in UNSTORABLE_TAGS.items():
        assert set(XMP_PLACE_TAGS) & tags == set(), f"{suffix} 那条把 XMP 也砍掉了"


@pytest.mark.parametrize("suffix", [".heic", ".heif", ".webp"])
def test_the_containers_without_iptc_drop_exactly_the_iptc_place_names(suffix: str) -> None:
    """**这三个容器丢掉的正好是 IPTC 那三个地名标签** —— 一个不多，一个不少。

    坐标、拍摄时间在那几个容器里都是好的（实测），**多丢一个就是又一次「地点写不进去」**，
    少丢一个就还是今天这个 bug。
    """
    assert UNSTORABLE_TAGS[suffix] == frozenset(IPTC_PLACE_TAGS)


def test_the_storage_gap_table_only_covers_formats_the_library_shows() -> None:
    """**表里的每个后缀都得是 Gallery 认得的照片格式** —— 否则那是给不存在的东西开的口子。"""
    assert set(UNSTORABLE_TAGS) <= SUPPORTED_SUFFIXES


def test_bmp_is_left_to_the_message_exiftool_already_gives() -> None:
    """**BMP 不进这张表**：exiftool 对它明确报错并返回非 0，那条路本来就是通的。

    这张表只装**不说话的**。把 BMP 也塞进来「静默跳过」，等于把一句现成的清楚错误
    换成一句沉默的失败 —— 那比现在糟。
    """
    assert ".bmp" not in UNSTORABLE_TAGS


def test_the_place_edit_a_heic_gets_is_the_one_that_actually_lands(library: Path) -> None:
    """**从计划到写盘的整条路**（跨 :mod:`place_edit` 与 :mod:`writeback` 两层）。

    ``plan_place_edit`` 仍然同时生成 XMP 与 IPTC —— 那是对的（它不知道你要写进哪种容器）。
    **由写回这一层按容器去掉存不下的那一份**，于是 HEIC 上落到文件里的是 XMP，
    而「地点写不进去」这个症状就此消失。
    """
    plan = plan_place_edit(PlaceState(), PlaceEdit(city="杭州", region="浙江省", country="中国"))
    assert {"IPTC:City", "XMP:City"} <= {item.tag for item in plan.assignments}

    writer = FakeWriter()
    ref = _photo_with_suffix(library, ".heic")

    result = write_metadata(library, ref, plan.assignments, writer=writer)

    assert result.written
    assert writer.fields["XMP:City"] == "杭州"
    assert "IPTC:City" not in writer.fields
    assert {c.tag for c in result.changes} == {"XMP:City", "XMP:State", "XMP:Country"}


# ---- 写与记账 ------------------------------------------------------------------


def test_writing_records_the_original_value_of_every_field(library: Path, photo: PhotoRef) -> None:
    writer = FakeWriter({"EXIF:GPSLatitude": "44.83779", "XMP:City": "波尔多"})

    result = write_metadata(library, photo, list(HANGZHOU), writer=writer)

    assert result.written
    assert result.entry is not None and result.entry.size == photo.size  # 假文件没变大
    records = read_records(library)
    # **两条**：先记「打算改」（原值与新值已经齐了），动完文件再记终态。
    # 少掉前面那一条，写入与落盘之间那一下崩溃就什么都留不下（红线 4）。
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
    # 「打算改」与「没改成」两条都在：没有前者，一次中途的崩溃就查不出动过什么。
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_FAILED]


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
    assert [record.result for record in records] == [RESULT_PENDING, RESULT_FAILED]
    assert records[-1].message


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


# ---- 回滚也要校验（红线 4 的「如实报错」那一半）---------------------------------


class _RollbackCorruptingWriter(FakeWriter):
    """写进去的值**总是错的**，但退出码为 0 —— exiftool 在这一支要处理的情形里正是如此。

    实测能造出的局面：正向写入把经度写成了别的值（写进去了），回滚那次又写成了
    另一个错值，于是**用户原来的坐标被冲掉**，而文件仍然报告「写入成功」。
    """

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
    """**回滚本身要被回读校验** —— 「按原值写回去」是一次请求，不是一次保证。

    曾经这里不查就宣布「照片已经恢复原样」。那句话是给用户看的，它必须为真：
    照片里原来的经度被写成 ``0.000000`` 而界面说恢复原样，用户拿回的是一张坐标
    被改坏的照片 —— 而日志里前后值都齐着，看上去一切正常。
    """
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
    """反过来那条：回滚确实把原值写回去了，就照旧给「已经恢复原样」。

    校验是为了不再说假话，不是为了让每次失败都吓用户一跳。
    """
    writer = FakeWriter({"XMP:City": "波尔多"})
    writer.silently_ignores = True  # 两次写都没进去，于是文件一直是原样

    with pytest.raises(MetadataWriteError, match="已经恢复原样"):
        write_metadata(library, photo, [Assignment("XMP:City", "杭州")], writer=writer)


def test_the_intent_is_recorded_before_the_file_is_touched(library: Path, photo: PhotoRef) -> None:
    """**先记账，再动文件。**

    原值与新值在动文件之前就已经齐了，落一条 pending 只要几十微秒；而写入要起一到两次
    exiftool 子进程（几十到几百毫秒）。那个窗口里断电、被强杀、磁盘写满，原图就改了
    而日志上一条记录也没有 —— 用户拿不回任何东西，红线 4 的「写回前记录原值」落空。
    """
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
