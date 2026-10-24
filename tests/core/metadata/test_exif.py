"""``gallery.core.metadata.exif`` 的测试。

exiftool 那一路用**假的可执行脚本**验证，纯解析与格式化直接喂字典（不需本机装 exiftool）。"""

from __future__ import annotations

import dataclasses
import io
import json
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.errors import MetadataError
from gallery.core.metadata.exif import (
    ExifToolReader,
    read_metadata,
)
from gallery.core.metadata.report import build_report


def field_map(report_groups: object) -> dict[str, str]:
    return {field.label: field.value for group in report_groups for field in group.fields}  # type: ignore[attr-defined]


def report_of(raw: Mapping[str, object], tmp_path: Path) -> dict[str, str]:
    report = build_report(
        raw,
        path=tmp_path / "IMG_0001.JPG",
        size=5 * 1024 * 1024,
        mtime_ns=0,
        source="exiftool",
    )
    return field_map(report.groups)


def test_full_exif_maps_to_chinese_labels(tmp_path: Path) -> None:
    raw = {
        "EXIF:Make": "Canon",
        "EXIF:Model": "Canon EOS R6",
        "EXIF:LensModel": "RF24-70mm F2.8 L IS USM",
        "EXIF:DateTimeOriginal": "2022:04:16 20:52:02",
        "EXIF:ExposureTime": "1/500",
        "EXIF:FNumber": "4.0",
        "EXIF:ISO": "400",
        "EXIF:ExposureCompensation": "+0.3",
        "EXIF:ExposureProgram": "Aperture-priority AE",
        "EXIF:MeteringMode": "Multi-segment",
        "EXIF:Flash": "No Flash",
        "EXIF:FocalLength": "35.0 mm",
        "EXIF:FocalLengthIn35mmFormat": "35 mm",
        "EXIF:WhiteBalance": "Auto",
        "EXIF:Orientation": "Horizontal (normal)",
        "EXIF:ExifImageWidth": "4640",
        "EXIF:ExifImageHeight": "3472",
        "EXIF:ColorSpace": "sRGB",
        "EXIF:Software": "Lightroom 13.2",
    }

    fields = report_of(raw, tmp_path)

    assert fields["相机"] == "Canon EOS R6"  # 制造商不重复出现
    assert fields["拍摄时间"] == "2022-04-16 20:52:02"
    assert fields["曝光时间"] == "1/500 秒"
    assert fields["光圈"] == "f/4.0"
    assert fields["曝光补偿"] == "+0.3 EV"
    assert fields["焦距"] == "35.0 mm"
    assert fields["像素尺寸"] == "4640 × 3472"
    assert fields["色彩空间"] == "sRGB"


def test_camera_keeps_make_when_model_differs(tmp_path: Path) -> None:
    fields = report_of({"EXIF:Make": "Apple", "EXIF:Model": "iPhone 15 Pro"}, tmp_path)

    assert fields["相机"] == "Apple iPhone 15 Pro"


def test_model_alone_is_enough(tmp_path: Path) -> None:
    assert report_of({"EXIF:Model": "iPhone 15 Pro"}, tmp_path)["相机"] == "iPhone 15 Pro"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1/500", "1/500 秒"), ("0.002", "1/500 秒"), ("2", "2 秒"), ("0.5", "1/2 秒")],
)
def test_exposure_accepts_both_source_shapes(tmp_path: Path, value: str, expected: str) -> None:
    """exiftool 给 `1/500`，Pillow 给 `0.002`，两种都得认。"""
    assert report_of({"EXIF:ExposureTime": value}, tmp_path)["曝光时间"] == expected


def test_aperture_falls_back_to_apex_value(tmp_path: Path) -> None:
    """Pillow 拿不到 FNumber 时只有 APEX 的 ApertureValue（f = 2^(AV/2)）。"""
    fields = report_of({"EXIF:ApertureValue": "4"}, tmp_path)

    assert fields["光圈"] == "f/4.0"


def test_exposure_bias_zero_has_no_sign(tmp_path: Path) -> None:
    assert report_of({"EXIF:ExposureCompensation": "0"}, tmp_path)["曝光补偿"] == "0 EV"


def test_coordinates_combine_latitude_and_longitude(tmp_path: Path) -> None:
    fields = report_of(
        {
            "EXIF:GPSLatitude": "44.83780",
            "EXIF:GPSLatitudeRef": "N",
            "EXIF:GPSLongitude": "0.57920",
            "EXIF:GPSLongitudeRef": "W",
            "EXIF:GPSAltitude": "12.3 m",
        },
        tmp_path,
    )

    assert fields["坐标"] == "44.83780° N，0.57920° W"
    assert fields["海拔"] == "12.3 m"


def test_gps_below_sea_level_is_negative(tmp_path: Path) -> None:
    fields = report_of(
        {"EXIF:GPSAltitude": "12.3", "EXIF:GPSAltitudeRef": "Below Sea Level"}, tmp_path
    )

    assert fields["海拔"] == "-12.3 m"


def test_keywords_list_is_joined(tmp_path: Path) -> None:
    fields = report_of({"IPTC:Keywords": ["波尔多", "沙丘"]}, tmp_path)

    assert fields["关键词"] == "波尔多、沙丘"


def test_empty_values_and_groups_are_hidden(tmp_path: Path) -> None:
    """exiftool 用 `--` 表示空；空字段不显示，整组为空则整组不显示。"""
    report = build_report(
        {"EXIF:Make": "--", "EXIF:Model": "", "EXIF:Orientation": "Horizontal (normal)"},
        path=tmp_path / "a.jpg",
        size=1,
        mtime_ns=0,
        source="exiftool",
    )

    titles = [group.title for group in report.groups]
    fields = field_map(report.groups)

    assert "位置" not in titles  # 没有 GPS 就整组不出现
    assert "内容" not in titles
    assert "相机" not in fields  # Make="--"、Model="" 都不算值
    assert fields["方向"] == "Horizontal (normal)"  # 同组里还有值的字段照常显示
    assert "文件" in titles  # 文件组始终存在


def test_file_group_is_always_present(tmp_path: Path) -> None:
    report = build_report(
        {}, path=tmp_path / "相册" / "IMG_1.JPG", size=2048, mtime_ns=0, source="exiftool"
    )

    fields = field_map(report.groups)

    assert fields["文件名"] == "IMG_1.JPG"
    assert fields["所在文件夹"] == str(tmp_path / "相册")
    assert fields["格式"] == "JPG"
    assert fields["大小"] == "2.0 KB"
    assert "修改时间" in fields


def test_all_fields_keep_raw_keys_sorted(tmp_path: Path) -> None:
    report = build_report(
        {"EXIF:Model": "X", "File:FileSize": "1 MB"},
        path=tmp_path / "a.jpg",
        size=1,
        mtime_ns=0,
        source="exiftool",
    )

    assert [field.label for field in report.all_fields] == ["EXIF:Model", "File:FileSize"]
    assert report.is_complete is True  # 来源是 exiftool 即视为完整


def test_as_text_contains_groups_and_notice(tmp_path: Path) -> None:
    report = build_report(
        {"EXIF:Model": "X"},
        path=tmp_path / "a.jpg",
        size=1,
        mtime_ns=0,
        source="pillow",
        notice="未安装 exiftool",
    )

    text = report.as_text()

    assert "【拍摄信息】" in text
    assert "相机：X" in text
    assert "未安装 exiftool" in text
    assert report.is_complete is False


def _fake_exiftool(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "exiftool"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_reader_parses_json_from_subprocess(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    photo = make_photo("a.jpg")
    payload = json.dumps([{"EXIF:Model": "假相机", "SourceFile": str(photo)}])
    script = _fake_exiftool(tmp_path, f"cat <<'EOF'\n{payload}\nEOF")

    reader = ExifToolReader(executable=str(script))
    report = read_metadata(photo, source=reader)

    assert reader.available() is True
    assert report.source == "exiftool"
    assert report.notice is None
    assert field_map(report.groups)["相机"] == "假相机"


def test_reader_rejects_unparsable_output(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    photo = make_photo("a.jpg")
    script = _fake_exiftool(tmp_path, "echo '这不是 JSON'")

    with pytest.raises(MetadataError) as excinfo:
        ExifToolReader(executable=str(script)).read(photo)

    assert "无法解析" in excinfo.value.message


def test_reader_times_out(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    photo = make_photo("a.jpg")
    script = _fake_exiftool(tmp_path, "sleep 5")

    with pytest.raises(MetadataError) as excinfo:
        ExifToolReader(executable=str(script), timeout=0.3).read(photo)

    assert "超时" in excinfo.value.message


def test_reader_reports_empty_output(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    photo = make_photo("a.jpg")
    script = _fake_exiftool(tmp_path, "echo '[]'")

    with pytest.raises(MetadataError) as excinfo:
        ExifToolReader(executable=str(script)).read(photo)

    assert "没有返回可用信息" in excinfo.value.message


class _Missing:
    """假来源：永远不可用（等价于没装 exiftool）。"""

    def available(self) -> bool:
        return False

    def read(self, path: Path) -> Mapping[str, object]:  # pragma: no cover - 不会被调用
        raise AssertionError("不可用的来源不该被调用")


class _Broken:
    """假来源：可用但读取失败。"""

    def available(self) -> bool:
        return True

    def read(self, path: Path) -> Mapping[str, object]:
        raise MetadataError("exiftool 崩了", path=path)


def test_missing_exiftool_falls_back_to_pillow(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    photo = make_photo("a.jpg", orientation=6)

    report = read_metadata(photo, source=_Missing())

    assert report.source == "pillow"
    assert report.notice is not None
    assert "brew install exiftool" in report.notice
    assert field_map(report.groups)["方向"] == "顺时针 90°"


def test_failed_exiftool_falls_back_and_says_why(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    photo = make_photo("a.jpg")

    report = read_metadata(photo, source=_Broken())

    assert report.source == "pillow"
    assert report.notice is not None and "exiftool 崩了" in report.notice


def test_fallback_can_be_refused(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    photo = make_photo("a.jpg")

    with pytest.raises(MetadataError):
        read_metadata(photo, source=_Missing(), allow_fallback=False)


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MetadataError) as excinfo:
        read_metadata(tmp_path / "不存在.jpg", source=_Missing())

    assert "读不到" in excinfo.value.message


def test_pillow_gps_is_converted_to_decimal() -> None:
    """Pillow 给的是 (度, 分, 秒) 三元组，要换算成十进制度。"""
    from gallery.core.metadata.pillow_fallback import _gps_fields

    fields = _gps_fields({1: "N", 2: (44.0, 50.0, 16.0), 3: "W", 4: (0.0, 34.0, 45.0), 6: 12.5})

    assert fields["EXIF:GPSLatitude"] == "44.83778"
    assert fields["EXIF:GPSLatitudeRef"] == "N"
    assert fields["EXIF:GPSLongitude"] == "0.57917"
    assert fields["EXIF:GPSLongitudeRef"] == "W"
    assert fields["EXIF:GPSAltitude"] == "12.5"


def test_pillow_gps_southern_hemisphere_is_negative() -> None:
    from gallery.core.metadata.pillow_fallback import _gps_fields

    fields = _gps_fields({1: "S", 2: (33.0, 51.0, 0.0), 3: "E", 4: (151.0, 12.0, 0.0)})

    assert fields["EXIF:GPSLatitudeRef"] == "S"
    assert fields["EXIF:GPSLongitudeRef"] == "E"


def test_pillow_gps_ignores_garbage() -> None:
    from gallery.core.metadata.pillow_fallback import _gps_fields

    assert _gps_fields({}) == {}
    assert _gps_fields({2: "不是坐标"}) == {}
    assert _gps_fields({2: (1.0, 2.0)}) == {}


def test_the_degraded_path_works_without_exiftool(tmp_path: Path) -> None:
    """**没有 exiftool 也能读**：降级路径只用 Pillow 与标准库，不依赖任何外部命令。

    断言要真的读得出字段：用「标准库恒真式」的那种写法，降级路径里悄悄多一个子进程
    也照样绿。
    """
    photo = tmp_path / "IMG_0001.jpg"
    photo.write_bytes(_jpeg_with_exif())

    missing = ExifToolReader(executable=str(tmp_path / "没有这个命令"))
    report = read_metadata(photo, source=missing)

    assert report.source == "pillow", f"没有 exiftool 时该降级到 Pillow，实际 {report.source}"
    assert field_map(report.groups), "降级之后也该读得出 EXIF，而不是空报告"
    assert report.notice and "exiftool" in report.notice, (
        f"降级要如实告诉用户，不许悄悄给一份缩水的信息：{report.notice!r}"
    )


def _jpeg_with_exif() -> bytes:
    """一张 8×6 的 JPEG + 一段最小 EXIF（厂商与型号），用 Pillow 现造。"""
    image = Image.new("RGB", (8, 6), (10, 20, 30))
    exif = image.getexif()
    exif[0x010F] = "Gallery"  # Make
    exif[0x0110] = "Test Camera"  # Model
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


class TestDegenerateValues:
    """有些导出工具把数值字段一律填 0；这种「有值但无意义」不该显示出来。"""

    @pytest.mark.parametrize(
        ("raw", "label"),
        [
            ({"EXIF:FNumber": "0.0"}, "光圈"),
            ({"EXIF:FocalLength": "0"}, "焦距"),
            ({"EXIF:ISO": "0"}, "ISO"),
            ({"EXIF:ISOSpeedRatings": "0"}, "ISO"),
            ({"EXIF:ExposureTime": "0"}, "曝光时间"),
            ({"EXIF:FocalLengthIn35mmFormat": "0"}, "35mm 等效"),
        ],
    )
    def test_zero_is_treated_as_missing(
        self, tmp_path: Path, raw: dict[str, str], label: str
    ) -> None:
        assert label not in report_of(raw, tmp_path)

    def test_zero_aperture_falls_back_to_apex(self, tmp_path: Path) -> None:
        """FNumber=0 时退回 APEX 光圈值，而不是显示 f/0.0。"""
        fields = report_of({"EXIF:FNumber": "0.0", "EXIF:ApertureValue": "4"}, tmp_path)

        assert fields["光圈"] == "f/4.0"

    def test_zero_dimensions_are_hidden(self, tmp_path: Path) -> None:
        assert "像素尺寸" not in report_of({"EXIF:ExifImageWidth": "0"}, tmp_path)

    def test_zero_exposure_bias_is_kept(self, tmp_path: Path) -> None:
        """0 EV 是合法且有意义的取值，不能当成缺失。"""
        assert report_of({"EXIF:ExposureCompensation": "0"}, tmp_path)["曝光补偿"] == "0 EV"


class TestPlaceFields:
    """地点相关：文件里写的、以及按坐标推断的，必须分得清。"""

    def test_coordinates_are_parsed_as_decimals(self, tmp_path: Path) -> None:
        report = build_report(
            {
                "EXIF:GPSLatitude": "44.83780",
                "EXIF:GPSLatitudeRef": "N",
                "EXIF:GPSLongitude": "0.57920",
                "EXIF:GPSLongitudeRef": "W",
            },
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.latitude == pytest.approx(44.8378)
        assert report.longitude == pytest.approx(-0.5792)

    def test_southern_and_western_hemispheres_are_negative(self, tmp_path: Path) -> None:
        report = build_report(
            {
                "EXIF:GPSLatitude": "33.86880",
                "EXIF:GPSLatitudeRef": "S",
                "EXIF:GPSLongitude": "151.20930",
                "EXIF:GPSLongitudeRef": "E",
            },
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.latitude == pytest.approx(-33.8688)
        assert report.longitude == pytest.approx(151.2093)

    def test_missing_coordinates_stay_none(self, tmp_path: Path) -> None:
        report = build_report(
            {"EXIF:Make": "Canon"}, path=tmp_path / "a.jpg", size=1, mtime_ns=0, source="exiftool"
        )

        assert report.latitude is None and report.longitude is None

    def test_place_written_in_the_file_is_shown_without_guessing(self, tmp_path: Path) -> None:
        report = build_report(
            {
                "XMP-photoshop:City": "Bordeaux",
                "XMP-photoshop:State": "Nouvelle-Aquitaine",
                "XMP-photoshop:Country": "France",
                "EXIF:GPSLatitude": "44.83780",
                "EXIF:GPSLatitudeRef": "N",
                "EXIF:GPSLongitude": "0.57920",
                "EXIF:GPSLongitudeRef": "W",
            },
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.place_from_file == "France · Nouvelle-Aquitaine · Bordeaux"
        # 三层写成一行（ADR 0031）；子地点仍单列 —— 那是另一个字段。
        fields = field_map(report.groups)
        assert fields["地点"] == "France · Nouvelle-Aquitaine · Bordeaux"
        assert "省 / 州" not in fields and "国家" not in fields

    def test_the_written_and_the_inferred_place_read_in_the_same_order(
        self, tmp_path: Path
    ) -> None:
        """**「地点」与「地点（推断）」必须从大到小，同一个顺序**：面板上这两行挨着，用户
        正要拿来对照，一个写「中国 · 浙江省 · 杭州」、另一个写「杭州 · 浙江省 · 中国」。"""
        report = build_report(
            {
                "XMP-photoshop:City": "杭州",
                "XMP-photoshop:State": "浙江省",
                "XMP-photoshop:Country": "中国",
                "EXIF:GPSLatitude": "30.27415",
                "EXIF:GPSLatitudeRef": "N",
                "EXIF:GPSLongitude": "120.15515",
                "EXIF:GPSLongitudeRef": "E",
            },
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        written = field_map(report.groups)["地点"]
        inferred = field_map(report.with_place(written).groups)["地点（推断）"]

        assert written == "中国 · 浙江省 · 杭州"
        assert inferred == written

    def test_the_place_is_one_line_whatever_family_exiftool_names_it(self, tmp_path: Path) -> None:
        """**两种族名都要认**：exiftool 13.x 的 ``-G`` 把这类键回填成 ``XMP:City``，
        只认全名的话，Lightroom 那种只写 XMP 的照片「地点」永远是空的。"""
        for keys in (
            {"XMP:City": "京都", "XMP:State": "京都府", "XMP:Country": "日本"},
            {
                "XMP-photoshop:City": "京都",
                "XMP-photoshop:State": "京都府",
                "XMP-photoshop:Country": "日本",
            },
            {
                "IPTC:City": "京都",
                "IPTC:Province-State": "京都府",
                "IPTC:Country-PrimaryLocationName": "日本",
            },
        ):
            report = build_report(
                keys, path=tmp_path / "a.jpg", size=1, mtime_ns=0, source="exiftool"
            )

            assert field_map(report.groups)["地点"] == "日本 · 京都府 · 京都"

    def test_no_place_in_file_means_nothing_to_show(self, tmp_path: Path) -> None:
        report = build_report(
            {"EXIF:GPSLatitude": "44.83780", "EXIF:GPSLatitudeRef": "N"},
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.place_from_file is None

    def test_inferred_place_is_labelled_as_inferred(self, tmp_path: Path) -> None:
        report = build_report(
            {
                "EXIF:GPSLatitude": "44.83780",
                "EXIF:GPSLatitudeRef": "N",
                "EXIF:GPSLongitude": "0.57920",
                "EXIF:GPSLongitudeRef": "W",
            },
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        updated = report.with_place("法国 · 新亚奎丹 · 波尔多（按坐标推断）")

        fields = field_map(updated.groups)
        assert fields["地点（推断）"] == "法国 · 新亚奎丹 · 波尔多（按坐标推断）"
        position = next(group for group in updated.groups if group.title == "位置")
        assert position.fields[0].label == "地点（推断）"
        assert "地点（推断）" not in field_map(report.groups)
        assert "地点（推断）" in updated.as_text()

    def test_inferred_place_creates_position_group_when_missing(self, tmp_path: Path) -> None:
        report = build_report(
            {"EXIF:Make": "Canon"}, path=tmp_path / "a.jpg", size=1, mtime_ns=0, source="exiftool"
        )

        updated = report.with_place("某地（按坐标推断）")

        assert "位置" in [group.title for group in updated.groups]


class TestRealExifToolOutput:
    """用 exiftool 对真实照片的实际输出核对字段映射：先前只拿十进制坐标测，而 exiftool
    默认给度分秒配 ``North`` 这种长半球名，坐标解析整个失败。"""

    #: 取自 exiftool 13.55 对一张真实照片的输出（只留相关字段）。
    REAL = {
        "EXIF:Make": "--",
        "EXIF:Model": "--",
        "EXIF:DateTimeOriginal": "2022:04:16 20:52:02",
        "EXIF:ExposureTime": 1,
        "EXIF:FNumber": 0,
        "EXIF:ApertureValue": 1.9,
        "EXIF:ISO": 0,
        "EXIF:ExposureCompensation": 0,
        "EXIF:FocalLength": "0.0 mm",
        "EXIF:FocalLengthIn35mmFormat": "25 mm",
        "EXIF:Flash": "No Flash",
        "EXIF:MeteringMode": "Center-weighted average",
        "EXIF:Orientation": "Horizontal (normal)",
        "EXIF:WhiteBalance": "Auto",
        "EXIF:ColorSpace": "sRGB",
        "EXIF:ExifImageWidth": 4640,
        "EXIF:ExifImageHeight": 3472,
        "EXIF:GPSLatitude": "44 deg 50' 16.04\"",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "0 deg 34' 45.05\"",
        "EXIF:GPSLongitudeRef": "West",
        "EXIF:GPSTimeStamp": "00:00:00",
        "Composite:GPSDateTime": "00:00:00 00:00:00Z",
    }

    def report(self, tmp_path: Path) -> object:
        return build_report(
            self.REAL,
            path=tmp_path / "真实照片.jpg",
            size=6 * 1024**2,
            mtime_ns=0,
            source="exiftool",
        )

    def test_degrees_minutes_seconds_are_parsed(self, tmp_path: Path) -> None:
        report = self.report(tmp_path)

        assert report.latitude == pytest.approx(44.8378, abs=1e-4)  # type: ignore[attr-defined]
        assert report.longitude == pytest.approx(-0.5792, abs=1e-4)  # type: ignore[attr-defined]

    def test_coordinates_are_shown_as_decimals(self, tmp_path: Path) -> None:
        assert field_map(self.report(tmp_path).groups)["坐标"] == "44.83779° N，0.57918° W"  # type: ignore[attr-defined]

    def test_degenerate_values_are_hidden(self, tmp_path: Path) -> None:
        fields = field_map(self.report(tmp_path).groups)  # type: ignore[attr-defined]

        assert "相机" not in fields  # Make/Model 都是 "--"
        assert "ISO" not in fields  # 0
        assert "焦距" not in fields  # "0.0 mm"
        assert "定位时间" not in fields  # 00:00:00 00:00:00Z
        assert fields["光圈"] == "f/1.9"  # FNumber=0 退回 APEX 1.9
        assert fields["35mm 等效"] == "25 mm"
        assert fields["闪光灯"] == "No Flash"

    def test_no_place_name_in_this_file(self, tmp_path: Path) -> None:
        report = self.report(tmp_path)

        assert report.place_from_file is None  # type: ignore[attr-defined]
        assert "地点" not in field_map(report.groups)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("44.83779", 44.83779),
        ("44 deg 50' 16.04\"", 44.8378),
        ("44°50'16.04\"", 44.8378),
        ("-33 deg 52' 8.0\"", -33.8689),
        ("44 deg 50'", 44.8333),
    ],
)
def test_degrees_to_decimal_accepts_both_shapes(value: str, expected: float) -> None:
    from gallery.core.metadata.exif import _degrees_to_decimal

    assert _degrees_to_decimal(value) == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize("value", ["", "不是坐标", "44 deg 99' 99\""])
def test_degrees_to_decimal_rejects_garbage(value: str) -> None:
    from gallery.core.metadata.exif import _degrees_to_decimal

    assert _degrees_to_decimal(value) is None


class TestPlaceResolvedFlag:
    """必须区分「还没推断过」与「推断过但没有结果」。"""

    def test_fresh_report_is_not_resolved(self, tmp_path: Path) -> None:
        report = build_report(
            {"EXIF:GPSLatitude": "44.83780", "EXIF:GPSLatitudeRef": "North"},
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.place_resolved is False

    def test_with_place_marks_it_resolved(self, tmp_path: Path) -> None:
        report = build_report(
            {"EXIF:GPSLatitude": "44.83780", "EXIF:GPSLatitudeRef": "North"},
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.with_place("波尔多").place_resolved is True

    def test_negative_result_also_counts_as_resolved(self, tmp_path: Path) -> None:
        """「附近没有记录」也是结论，不该反复重试。"""
        report = build_report(
            {"EXIF:GPSLatitude": "44.83780", "EXIF:GPSLatitudeRef": "North"},
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )

        assert report.with_place("坐标附近没有城市记录").place_resolved is True

    def test_copy_keeps_the_flag(self, tmp_path: Path) -> None:
        """dataclasses.replace 造出的新报告不能把标记弄丢。"""
        report = build_report(
            {"EXIF:GPSLatitude": "44.83780", "EXIF:GPSLatitudeRef": "North"},
            path=tmp_path / "a.jpg",
            size=1,
            mtime_ns=0,
            source="exiftool",
        )
        resolved = report.with_place("波尔多")

        assert dataclasses.replace(resolved).place_resolved is True


# ---- 视频的 QuickTime 标签（ADR 0092）---------------------------------------------


def test_a_video_report_shows_quicktime_fields(tmp_path: Path, beijing_timezone: None) -> None:
    """视频的拍摄时间、位置、设备与编码都要进面板，且与照片用同一套中文标签。"""
    raw = {
        "QuickTime:CreationDate": "2024:05:16 20:52:02+08:00",
        "QuickTime:CreateDate": "2024:05:16 12:52:02",
        "QuickTime:GPSCoordinates": (
            "31 deg 13' 49.44\" N, 121 deg 28' 25.32\" E, 10 m Above Sea Level"
        ),
        "QuickTime:Make": "Apple",
        "QuickTime:Model": "iPhone 15 Pro",
        "QuickTime:Duration": "0:01:30",
        "QuickTime:VideoFrameRate": "30.001",
        "QuickTime:CompressorID": "hvc1",
        "QuickTime:AvgBitrate": "7.3 Mbps",
        "QuickTime:ImageWidth": "3840",
        "QuickTime:ImageHeight": "2160",
    }
    report = build_report(raw, path=tmp_path / "片子.mov", size=1024, mtime_ns=0, source="exiftool")

    fields = field_map(report.groups)
    assert fields["拍摄时间"] == "2024-05-16 20:52:02+08:00"
    assert fields["相机"] == "Apple iPhone 15 Pro"
    assert fields["坐标"] == "31.23040° N，121.47370° E"
    assert fields["像素尺寸"] == "3840 × 2160"
    assert fields["时长"] == "0:01:30"
    assert fields["帧率"] == "30 fps"
    assert fields["编码"] == "HEVC"
    assert fields["码率"] == "7.3 Mbps"
    assert (report.latitude, report.longitude) == pytest.approx((31.2304, 121.4737))


def test_a_utc_only_video_time_is_shown_in_local_time(
    tmp_path: Path, beijing_timezone: None
) -> None:
    """只有 ``CreateDate`` 时它是 UTC：面板要换成本机时区，不能原样当墙钟。"""
    report = build_report(
        {"QuickTime:CreateDate": "2024:05:16 12:52:02"},
        path=tmp_path / "片子.mp4",
        size=1,
        mtime_ns=0,
        source="exiftool",
    )

    assert field_map(report.groups)["拍摄时间"] == "2024-05-16 20:52:02+08:00"


def test_a_zero_video_time_is_not_shown(tmp_path: Path) -> None:
    """导出工具填的 ``0000:00:00`` 与照片那条路一样当「没有」。"""
    report = build_report(
        {"QuickTime:CreateDate": "0000:00:00 00:00:00"},
        path=tmp_path / "片子.mp4",
        size=1,
        mtime_ns=0,
        source="exiftool",
    )

    assert "拍摄时间" not in field_map(report.groups)


def test_a_video_gps_in_iso_6709_is_understood(tmp_path: Path) -> None:
    """文件里存的是 ISO 6709；exiftool 有时也原样给，两种都要认。"""
    report = build_report(
        {"QuickTime:GPSCoordinates": "+31.2304+121.4737/"},
        path=tmp_path / "片子.mp4",
        size=1,
        mtime_ns=0,
        source="exiftool",
    )

    assert (report.latitude, report.longitude) == pytest.approx((31.2304, 121.4737))
    assert field_map(report.groups)["坐标"] == "31.23040° N，121.47370° E"
