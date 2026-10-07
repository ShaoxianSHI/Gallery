"""``gallery.core.metadata.facets`` 的测试。

它决定字段值的形状：重点是**别把该是数字的东西变成字符串**、**读不到就老实给 None**。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import (
    FACET_APERTURE,
    FACET_CAMERA,
    FACET_CAPTURED,
    FACET_CITY,
    FACET_COUNTRY,
    FACET_FOLDER,
    FACET_FORMAT,
    FACET_ISO,
    FACET_KEYWORD,
    FACET_MONTH,
    FACET_NAME,
    FACET_REGION,
    FACET_SIZE,
    FILE_FACET_IDS,
    GROUP_FACETS,
    SORT_FACETS,
    WANTED_TAGS,
    PhotoFacets,
    extract,
    group_facet,
    input_for,
    mark_place_resolved,
    needs_metadata,
    parse_capture_time,
    sort_facet,
    with_resolved_place,
)
from gallery.core.metadata.pillow_fallback import _read_with_pillow
from gallery.core.places.resolver import ResolvedPlace

#: 主 IFD 里的标签（写入文件时也用它）；下面那五个在 Exif 子 IFD 里。
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME_ORIGINAL = 0x9003
TAG_SHUTTER = 0x829A
TAG_APERTURE = 0x829D
TAG_ISO = 0x8827
TAG_FOCAL = 0x920A


def _ref(tmp_path: Path, name: str = "a.jpg", size: int = 100) -> PhotoRef:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return PhotoRef(path=path, size=size, mtime_ns=1)


def test_extract_reads_exif_fields() -> None:
    raw = {
        "SourceFile": "/照片/a.jpg",
        "EXIF:Make": "Canon",
        "EXIF:Model": "Canon EOS R6",
        "EXIF:DateTimeOriginal": "2024:05:16 20:52:02",
        "EXIF:FNumber": "4.0",
        "EXIF:ExposureTime": "1/200",
        "EXIF:ISO": "400",
        "EXIF:FocalLength": "35.0 mm",
        "EXIF:LensModel": "RF35mm F1.8 MACRO IS STM",
    }

    facets = extract(raw)

    assert (facets.year, facets.month, facets.day) == (2024, 5, 16)
    assert facets.captured_at is not None
    assert facets.camera == "Canon EOS R6"
    assert facets.lens == "RF35mm F1.8 MACRO IS STM"
    assert facets.aperture == 4.0
    assert facets.shutter == pytest.approx(0.005)  # 1/200 秒，不是 1！
    assert facets.iso == 400
    assert facets.focal == 35.0


def test_extract_reads_iptc_and_xmp() -> None:
    raw = {
        "IPTC:City": "Bordeaux",
        "IPTC:Country-PrimaryLocationName": "France",
        "XMP-photoshop:State": "Nouvelle-Aquitaine",
        "IPTC:Keywords": ["旅行", "海边"],
    }

    facets = extract(raw)

    assert facets.city == "Bordeaux"
    assert facets.country == "France"
    assert facets.region == "Nouvelle-Aquitaine"
    assert facets.keywords == ("旅行", "海边")


def test_extract_joins_keywords_from_several_sources() -> None:
    raw = {"IPTC:Keywords": "旅行; 海边", "XMP:Subject": ["海边", "夜景"]}

    facets = extract(raw)

    # 顺序按来源、去重后保留 —— 同一张照片上「海边」不该分出两组。
    assert facets.keywords == ("旅行", "海边", "夜景")


def test_extract_treats_zero_fnumber_as_missing() -> None:
    """有导出工具把 FNumber / ISO 一律填 0 —— 显示成 f/0、ISO 0 看着像坏了。"""
    raw = {"EXIF:FNumber": "0.0", "EXIF:ISO": "0", "EXIF:FocalLength": "0.0 mm"}

    facets = extract(raw)

    assert facets.aperture is None
    assert facets.iso is None
    assert facets.focal is None


def test_extract_computes_pixels() -> None:
    facets = extract({"File:ImageWidth": 1440, "File:ImageHeight": 1080})

    assert facets.pixels == 1440 * 1080


def test_extract_tolerates_numeric_values() -> None:
    """exiftool 会把宽高给成数字，把光圈给成字符串；两种都要认。"""
    facets = extract({"File:ImageWidth": 4000, "File:ImageHeight": 3000, "EXIF:FNumber": "2.8"})

    assert facets.pixels == 12_000_000
    assert facets.aperture == 2.8


def test_extract_of_empty_mapping_is_all_unknown() -> None:
    facets = extract({})

    assert facets == PhotoFacets()


def test_extract_reads_gps_coordinates() -> None:
    """相机原图几乎只写 GPS、不写 IPTC 地点：读不到坐标就分不出国家（见 WANTED_TAGS）。"""
    raw = {
        "EXIF:GPSLatitude": "34 deg 12' 54.34\"",
        "EXIF:GPSLatitudeRef": "North",
        "EXIF:GPSLongitude": "108 deg 57' 52.44\"",
        "EXIF:GPSLongitudeRef": "East",
    }

    facets = extract(raw)

    assert facets.latitude == pytest.approx(34.215, abs=0.001)
    assert facets.longitude == pytest.approx(108.965, abs=0.001)


def test_extract_honours_the_hemisphere() -> None:
    raw = {
        "EXIF:GPSLatitude": "48.8",
        "EXIF:GPSLatitudeRef": "South",
        "EXIF:GPSLongitude": "2.3",
        "EXIF:GPSLongitudeRef": "West",
    }

    facets = extract(raw)

    assert facets.latitude == pytest.approx(-48.8)
    assert facets.longitude == pytest.approx(-2.3)


def test_gps_tags_are_requested() -> None:
    """回归测试：漏掉这几行，每张照片的地点字段就都是空的。"""
    for tag in ("EXIF:GPSLatitude", "EXIF:GPSLongitude", "EXIF:GPSLatitudeRef"):
        assert tag in WANTED_TAGS


def test_resolved_place_fills_the_empty_fields() -> None:
    facets = PhotoFacets(latitude=34.2, longitude=108.9)

    filled = with_resolved_place(
        facets, ResolvedPlace(city="西安", region="陕西省", country="中国")
    )

    assert (filled.city, filled.region, filled.country) == ("西安", "陕西省", "中国")
    assert filled.place_inferred is True
    assert filled.latitude == 34.2  # 坐标照旧留着


def test_place_written_in_the_file_wins() -> None:
    """Lightroom 导出的地点比反查准，文件里写了就以文件为准。"""
    facets = PhotoFacets(city="Bordeaux", latitude=48.8, longitude=2.3)

    filled = with_resolved_place(facets, ResolvedPlace(city="Paris", country="France"))

    assert filled.city == "Bordeaux"
    assert filled.country == "France"  # 缺的照补
    assert filled.place_inferred is True


def test_a_fully_written_place_is_not_called_inferred() -> None:
    facets = PhotoFacets(city="巴黎", region="法兰西岛", country="法国")

    filled = with_resolved_place(facets, ResolvedPlace(city="巴黎", country="法国"))

    assert filled.place_inferred is False


def test_no_answer_leaves_the_facets_alone() -> None:
    """查不到就一个字段也不改 —— 那三列仍然是「文件里没写」。"""
    facets = PhotoFacets(latitude=0.0, longitude=0.0)

    assert with_resolved_place(facets, None) is facets


def test_the_distance_to_the_city_is_kept() -> None:
    """距离要留下来：面板靠它提醒「这只是最近的城市」。"""
    facets = PhotoFacets(latitude=0.0, longitude=0.0)

    filled = with_resolved_place(
        facets, ResolvedPlace(city="假市", country="假国", distance_km=182.4)
    )

    assert filled.place_distance_km == 182.4


def test_marking_a_lookup_as_done() -> None:
    """「查过了」是单独的一步：补字段与记结论是两件事。"""
    facets = PhotoFacets(latitude=34.2, longitude=108.9)

    marked = mark_place_resolved(facets)

    assert marked.place_checked is True
    assert facets.place_checked is False  # 原对象不动


def test_a_lookup_that_could_not_happen_is_not_marked() -> None:
    """数据没下好时那次**不算**结论，否则这些照片会被永久钉在「未知」上。"""
    facets = PhotoFacets(latitude=34.2, longitude=108.9, place_checked=True)

    unmarked = mark_place_resolved(facets, usable=False)

    assert unmarked.place_checked is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024:05:16 20:52:02", (2024, 5, 16)),
        ("2024-05-16T20:52:02", (2024, 5, 16)),
        ("2024:05:16 20:52:02+02:00", (2024, 5, 16)),
    ],
)
def test_parse_capture_time_accepts_both_spellings(
    text: str, expected: tuple[int, int, int]
) -> None:
    moment = parse_capture_time(text)

    assert moment is not None
    assert (moment.year, moment.month, moment.day) == expected


def test_parse_capture_time_uses_offset_for_instant() -> None:
    """带偏移时按**真实时刻**排序：``20:52+02:00`` 与 ``18:52Z`` 是同一刻。"""
    shifted = parse_capture_time("2024:05:16 20:52:02+02:00")
    utc = parse_capture_time("2024:05:16 18:52:02Z")

    assert shifted is not None and utc is not None
    assert shifted.timestamp == utc.timestamp


@pytest.mark.parametrize(
    "text", [None, "", "garbage", "0000:00:00 00:00:00", "2024:13:45 99:99:99"]
)
def test_parse_capture_time_rejects_nonsense(text: str | None) -> None:
    assert parse_capture_time(text) is None


def test_parse_capture_time_rejects_impossible_dates() -> None:
    """2 月 30 日、4 月 31 日这类不合法日期必须拒绝——不能只靠「月份 1-12、天数 1-31」
    这种宽松检查，否则会传到 datetime.date 抛异常（性能差）或漏过非法日期。"""
    assert parse_capture_time("2024:02:30 12:00:00") is None
    assert parse_capture_time("2024:04:31 12:00:00") is None
    assert parse_capture_time("2023:02:29 12:00:00") is None  # 非闰年


def test_unknown_ids_fall_back_to_none() -> None:
    assert group_facet("没有这个字段") is None
    assert sort_facet("没有这个字段") is None
    assert group_facet(None) is None
    assert sort_facet(None) is None


def test_keyword_is_not_sortable() -> None:
    """按一串标签排序没有意义，所以它只出现在分组菜单里。"""
    assert group_facet(FACET_KEYWORD) is not None
    assert sort_facet(FACET_KEYWORD) is None


def test_place_fields_need_exiftool() -> None:
    """没装 exiftool 时界面据此把国家 / 省州 / 城市 / 关键词置灰。

    **关键词也别漏**：它只住在 IPTC / XMP，漏标那项菜单就全是空的。"""
    assert group_facet(FACET_CITY).needs_exiftool
    assert group_facet(FACET_COUNTRY).needs_exiftool
    assert group_facet(FACET_REGION).needs_exiftool
    assert group_facet(FACET_KEYWORD).needs_exiftool
    assert not group_facet(FACET_CAMERA).needs_exiftool
    assert not sort_facet(FACET_CAPTURED).needs_exiftool


def test_keywords_really_do_come_from_exiftool_only(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """上一条的前提：降级来源（Pillow）根本给不出关键词 —— 它只产出 ``EXIF:`` 前缀的键。

    将来若有人把关键词挪到某个基础 EXIF 标签上，就得回来改 ``needs_exiftool``。"""
    keyword_keys = {"IPTC:Keywords", "XMP:Subject", "XMP-dc:Subject"}
    # 关键词读的键都得在 WANTED_TAGS 里，否则 exiftool 那一路也读不到。
    assert keyword_keys <= set(WANTED_TAGS)

    raw = _read_with_pillow(make_photo("一张.jpg", size=(8, 8)))
    assert not (set(raw) & keyword_keys)
    assert all(key.startswith("EXIF:") for key in raw)
    assert extract(raw).keywords == ()


def test_needs_metadata_is_false_for_file_only_fields() -> None:
    """只看文件名 / 大小 / 时间的排序不必等 exiftool，应当立刻生效。"""
    from gallery.core.metadata.facets import FACETS_BY_ID

    file_only = [FACETS_BY_ID[fid] for fid in FILE_FACET_IDS]

    assert needs_metadata(file_only) is False
    assert needs_metadata([FACETS_BY_ID[FACET_CAPTURED]]) is True
    assert needs_metadata([None, None]) is False


def test_catalogs_have_no_duplicates_and_real_labels() -> None:
    assert len({facet.id for facet in GROUP_FACETS}) == len(GROUP_FACETS)
    assert len({facet.id for facet in SORT_FACETS}) == len(SORT_FACETS)
    assert all(facet.label for facet in (*GROUP_FACETS, *SORT_FACETS))


def _input(tmp_path: Path, name: str, facets: PhotoFacets) -> object:
    return input_for(_ref(tmp_path, name), facets, tmp_path)


def test_group_by_month_labels_and_keys(tmp_path: Path) -> None:
    facet = group_facet(FACET_MONTH)
    assert facet is not None and facet.group is not None

    groups = facet.group(_input(tmp_path, "a.jpg", PhotoFacets(year=2024, month=5)))

    assert list(groups) == [("2024-05", "2024 年 5 月")]


def test_group_without_value_returns_nothing(tmp_path: Path) -> None:
    facet = group_facet(FACET_MONTH)
    assert facet is not None and facet.group is not None

    assert list(facet.group(_input(tmp_path, "a.jpg", PhotoFacets()))) == []
    assert facet.empty_label == "时间未知"


def test_group_by_folder_is_relative_to_library_root(tmp_path: Path) -> None:
    facet = group_facet(FACET_FOLDER)
    assert facet is not None and facet.group is not None
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "a.jpg").write_bytes(b"x")

    groups = list(
        facet.group(input_for(PhotoRef.from_path(tmp_path / "sub/a.jpg"), PhotoFacets(), tmp_path))
    )

    assert groups == [("sub", "sub")]


def test_group_by_folder_at_root(tmp_path: Path) -> None:
    facet = group_facet(FACET_FOLDER)
    assert facet is not None and facet.group is not None

    groups = list(facet.group(input_for(_ref(tmp_path, "a.jpg"), PhotoFacets(), tmp_path)))

    assert groups == [("", "（根目录）")]


def test_group_by_format_uses_readable_label(tmp_path: Path) -> None:
    facet = group_facet(FACET_FORMAT)
    assert facet is not None and facet.group is not None

    groups = list(facet.group(input_for(_ref(tmp_path, "a.jpg"), PhotoFacets(), tmp_path)))

    assert groups == [(".jpg", "JPEG")]


def test_sort_values_are_comparable_types(tmp_path: Path) -> None:
    """同一次排序里类型必须统一：数字字段不许混进字符串。"""
    assert (
        sort_facet(FACET_APERTURE).sort(_input(tmp_path, "a.jpg", PhotoFacets(aperture=2.8))) == 2.8
    )
    assert sort_facet(FACET_NAME).sort(_input(tmp_path, "B.jpg", PhotoFacets())) == "b.jpg"
    assert sort_facet(FACET_SIZE).sort(_input(tmp_path, "a.jpg", PhotoFacets())) == 100.0


def test_sort_value_is_none_when_missing(tmp_path: Path) -> None:
    facet = sort_facet(FACET_ISO)

    assert facet is not None
    assert facet.sort(_input(tmp_path, "a.jpg", PhotoFacets())) is None


def test_group_order_of_date_keys_is_numeric(tmp_path: Path) -> None:
    """``2024-05`` 必须按数字比：反过来字典序会把「11 月」排到「2 月」前面。"""
    facet = group_facet(FACET_MONTH)
    assert facet is not None and facet.group_order is not None

    may = facet.group_order("2024-05")
    november = facet.group_order("2024-11")

    assert isinstance(may, float) and isinstance(november, float)
    assert november > may


def test_the_full_xmp_name_wins_over_the_primary_one_and_iptc() -> None:
    """三族都有值时按 ``XMP-photoshop`` > ``XMP`` > ``IPTC`` 取第一个非空的。

    **顺序就是这条回退链的全部内容**，而它原先写在 ``extract`` 里、现在写在
    ``exif.PlaceField`` 里。顺序倒过来不会报错 —— 只是换了另一个来源的值，而面板与
    分组用的是同一条链，于是**一起错、看不出异常**。
    """
    assert extract({"XMP-photoshop:City": "波尔多", "IPTC:City": "Bordeaux"}).city == "波尔多"
    assert extract({"XMP:City": "波尔多市", "IPTC:City": "Bordeaux"}).city == "波尔多市"
    assert extract({"IPTC:City": "Bordeaux"}).city == "Bordeaux"
    assert extract({}).city is None
    # 三层各自独立，一层缺失不影响另两层。
    partial = extract({"IPTC:City": "Bordeaux", "XMP-photoshop:Country": "France"})
    assert (partial.city, partial.region, partial.country) == ("Bordeaux", None, "France")
