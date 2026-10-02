"""可分组 / 可排序的元数据字段（facet）。

**为什么不直接用 `MetadataReport`**：元数据面板要的是给人看的字符串（``f/2.8``、
``1/2000 秒``、``2024 年 5 月``）。按这种串排序，``1/2000 秒`` 与 ``1/400 秒`` 的先后就
取决于字典序了；按它分组，``ISO 100`` 与 ``ISO 1000`` 干脆是两组。**显示归格式化，
排序归原始值** —— 所以这里定义的每个字段都带两个取值函数：分组用的「组键 + 显示名」，
排序用的「可比较的值」。

字段分两处来：

- **文件自带的**（文件名、大小、修改时间、所在文件夹、格式）：扫描时就有了，零成本。
- **元数据里的**（拍摄时间、相机、镜头、地点、关键词、拍摄参数）：必须真去读 EXIF，
  由 ``core.metadata.batch`` 批量读出来、由 ``core.library.index`` 缓存。

纯 Python，不碰 Qt、不碰文件。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from gallery.core.errors import MetadataError
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.exif import (
    MetadataSource,
    camera_name,
    first_value,
    leading_number,
    positive_value,
)

# ---- 字段 id -----------------------------------------------------------------------

#: 拍摄时间（排序用；分组的三个粒度见下面三个）。
FACET_CAPTURED: Final = "captured"
#: 按年分组。
FACET_YEAR: Final = "captured_year"
#: 按年月分组。
FACET_MONTH: Final = "captured_month"
#: 按年月日分组。
FACET_DAY: Final = "captured_day"

FACET_CAMERA: Final = "camera"
FACET_LENS: Final = "lens"
FACET_COUNTRY: Final = "country"
FACET_REGION: Final = "region"
FACET_CITY: Final = "city"
FACET_KEYWORD: Final = "keyword"

FACET_FOLDER: Final = "folder"
FACET_FORMAT: Final = "format"
FACET_NAME: Final = "name"
FACET_SIZE: Final = "size"
FACET_MODIFIED: Final = "modified"

FACET_APERTURE: Final = "aperture"
FACET_SHUTTER: Final = "shutter"
FACET_ISO: Final = "iso"
FACET_FOCAL: Final = "focal"
FACET_PIXELS: Final = "pixels"

#: 字段的取值种类。决定分组键与排序值的形状，界面也据此选文案。
FacetKind = Literal["text", "number", "captured", "path"]

#: 排序时能拿到的值。同一个字段内部类型是统一的（要么全数字，要么全字符串），
#: 所以拿它排序不会撞上「数字和字符串不能比」这种问题。
type SortValue = float | str


@dataclass(frozen=True, slots=True)
class FacetInput:
    """取值时能看到的全部东西：照片本身、它的元数据快照、库根目录。"""

    ref: PhotoRef
    facets: PhotoFacets
    root: Path


@dataclass(frozen=True, slots=True)
class Facet:
    """一个可分组 / 可排序的字段。

    ``group`` 与 ``sort`` 都是纯函数：给同样的输入必得同样的输出，因此单测可以直接
    喂一个构造好的 :class:`FacetInput`，不必碰磁盘。
    """

    id: str
    label: str
    kind: FacetKind
    #: 分组取值：``(组键, 显示名)`` 的列表。**可以多于一个** —— 一张有多个关键词的
    #: 照片同时属于多个组（标签的通常语义）。返回空列表表示没有值。
    #: ``None`` 表示这个字段只能排序、不能分组。
    group: Callable[[FacetInput], Sequence[tuple[str, str]]] | None
    #: 排序取值：没有值就返回 ``None``（一律排在最后，正序逆序都一样）。
    sort: Callable[[FacetInput], SortValue | None] = lambda _input: None
    #: 组键怎么比较。``"2024-05"`` 这类日期键必须**当数字**比
    #: （``2024-05`` 反过来字典序排会得到「11 月排在 2 月前面」），其余按名称升序。
    #: 返回数字的字段，组内所有键都是数字；返回字符串的反之。
    group_order: Callable[[str], SortValue] | None = None
    #: 组的默认排列方向。时间为真时是「新 → 旧」，其余按名称升序。
    newest_first: bool = False
    #: 只有装了 exiftool 才读得到（Pillow 降级路径拿不到 IPTC / XMP）。
    needs_exiftool: bool = False
    #: 取不到值时那张组卡片的标题。
    empty_label: str = "未知"


# ---- 元数据快照 ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhotoFacets:
    """一张照片的分组 / 排序用元数据。

    这是**索引里存的东西**，也是分组排序唯一依赖的输入：所有需要读 EXIF 的字段
    都提前算成可比较的值，读一次、存一次、以后排序不再碰原文件。
    """

    #: 拍摄时间（epoch 秒）。用来排序。
    captured_at: float | None = None
    #: 拍摄时的年月日（按**照片里记的那个时间**，不换算到本机时区）。
    year: int | None = None
    month: int | None = None
    day: int | None = None
    camera: str | None = None
    lens: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    keywords: tuple[str, ...] = ()
    #: 光圈 f 值（如 2.8）。0 与负数等价于「没记录」，已按此归一。
    aperture: float | None = None
    #: 快门时间（秒，如 0.0005）。**秒数越小越快**，界面文案会写反，排序不会。
    shutter: float | None = None
    iso: float | None = None
    #: 焦距（毫米）。
    focal: float | None = None
    #: 总像素数（宽 × 高）。
    pixels: float | None = None


#: exiftool 只需要给出这些标签。
#:
#: 点名要而不是拉全量：一张照片的完整标签有好几百个，5000 张的 JSON 有几十兆，
#: 解码它的时间与内存都比排序本身贵得多。**加字段时往这里加一行**。
WANTED_TAGS: Final[tuple[str, ...]] = (
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "EXIF:OffsetTimeOriginal",
    "EXIF:OffsetTime",
    "EXIF:Make",
    "EXIF:Model",
    "EXIF:LensModel",
    "EXIF:LensID",
    "EXIF:FNumber",
    "EXIF:ApertureValue",
    "EXIF:ExposureTime",
    "EXIF:ISO",
    "EXIF:ISOSpeedRatings",
    "EXIF:FocalLength",
    "EXIF:ExifImageWidth",
    "EXIF:ExifImageHeight",
    "File:ImageWidth",
    "File:ImageHeight",
    "IPTC:City",
    "IPTC:Province-State",
    "IPTC:Country-PrimaryLocationName",
    "IPTC:Keywords",
    "XMP-photoshop:City",
    "XMP-photoshop:State",
    "XMP-photoshop:Country",
    "XMP:Subject",
)

#: IPTC 的关键词字段可能是数组或用分号分隔的字符串。
_KEYWORD_SPLIT = re.compile(r"[;,、]")

#: ``2024:05:16 20:52:02``（EXIF）与 ``2024-05-16T20:52:02+02:00``（XMP）都认。
#:
#: 时区偏移**在同一个正则里**一起吃掉：它紧贴在秒后面（``20:52:02+02:00``），
#: 分开用另一个正则去找，``+`` 前面没有空格就会漏掉 —— 漏掉的后果是把东八区的
#: 照片当成 UTC，排序整整偏八小时。
_DATETIME = re.compile(
    r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?\s*$"
)


def extract(raw: Mapping[str, object]) -> PhotoFacets:
    """从原始键值里抽出分组 / 排序用的字段（纯函数，测试直接喂字典）。

    取值规则与元数据面板同源（都走 ``exif`` 里的 ``first_value`` 等），但**不共用
    格式化**：这里要的是能比较的东西。

    值一律先转成字符串再解析：exiftool 同一批字段里数字与字符串混着给
    （``File:ImageWidth`` 是 1440、``EXIF:FNumber`` 是 "2.8"），按类型分两套取值函数
    等于给自己埋一个「某个字段类型一变就静默失效」的坑。
    """
    values = {key: value for key, value in raw.items() if key != "SourceFile"}
    text = {key: _as_text(value) for key, value in values.items()}
    when = parse_capture_time(
        first_value(text, "EXIF:DateTimeOriginal", "EXIF:CreateDate", "EXIF:DateTimeDigitized")
    )
    width = _number(text, "EXIF:ExifImageWidth", "File:ImageWidth", "EXIF:ImageWidth")
    height = _number(text, "EXIF:ExifImageHeight", "File:ImageHeight", "EXIF:ImageLength")
    return PhotoFacets(
        captured_at=when.timestamp if when else None,
        year=when.year if when else None,
        month=when.month if when else None,
        day=when.day if when else None,
        camera=camera_name(text),
        lens=first_value(text, "EXIF:LensModel", "EXIF:LensID", "EXIF:Lens"),
        city=first_value(text, "XMP-photoshop:City", "IPTC:City"),
        region=first_value(text, "XMP-photoshop:State", "IPTC:Province-State"),
        country=first_value(text, "XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"),
        keywords=_keywords(values),
        aperture=_number(text, "EXIF:FNumber", "EXIF:Aperture"),
        shutter=_shutter_seconds(text),
        iso=_number(text, "EXIF:ISO", "EXIF:ISOSpeedRatings"),
        focal=_number(text, "EXIF:FocalLength"),
        pixels=width * height if width and height else None,
    )


@dataclass(frozen=True, slots=True)
class CaptureMoment:
    """照片记下的那个拍摄时刻。

    ``timestamp`` 用来**排序**（真实的时间先后）；``year`` / ``month`` / ``day`` 用来
    **分组**，取的是照片里记下的墙上时间，不换算到本机时区 —— 否则一张在东京拍的
    照片会在分组时被塞进相邻的某一天。
    """

    timestamp: float
    year: int
    month: int
    day: int


def parse_capture_time(value: str | None) -> CaptureMoment | None:
    """``2024:05:16 20:52:02+02:00`` → 时刻；无法解析或明显不合法就返回 ``None``。"""
    if not value:
        return None
    text = value.strip()
    match = _DATETIME.search(text)
    if match is None:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups()[:5])
    second = int(match.group(6) or 0)
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60):
        return None
    offset = _offset_seconds(match.group(7))
    try:
        # 没有时区信息时按本机时间理解：EXIF 习惯不写偏移，而相机写下的墙上时间
        # 对用户来说就是「几点几分」，换算反而会让分组漂到别的日期。
        epoch = _epoch(year, month, day, hour, minute, second, offset)
    except (ValueError, OverflowError, OSError):
        return None
    return CaptureMoment(timestamp=epoch, year=year, month=month, day=day)


def _offset_seconds(token: str | None) -> int | None:
    """``+02:00`` / ``-0500`` / ``Z`` → 偏移秒数。相机没写就是 ``None``。"""
    if not token:
        return None
    if token == "Z":
        return 0
    hours = int(token[1:3])
    minutes = int(token[-2:])
    seconds = hours * 3600 + minutes * 60
    return -seconds if token[0] == "-" else seconds


def _epoch(
    year: int, month: int, day: int, hour: int, minute: int, second: int, offset: int | None
) -> float:
    """把「墙上时间」换成 epoch 秒。

    - **写了时区偏移**（含 ``Z``）就按 UTC 理解再减去偏移，得到真实时刻。
    - **没写**（相机 EXIF 的常态）就按本机时区理解：EXIF 习惯不写偏移，而相机
      写下的墙上时间对用户来说就是「几点几分」，强行当成 UTC 会把排序整体平移
      几个小时。分组的年月日本来就取自这串墙上时间，两者保持一致才不会漂到别的
      日期去。
    """
    wall = datetime.datetime(year, month, day, hour, minute, second)  # noqa: DTZ001
    if offset is None:
        return wall.timestamp()
    utc = datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=datetime.UTC
    ) - datetime.timedelta(seconds=offset)
    return utc.timestamp()


def _number(values: Mapping[str, str], *keys: str) -> float | None:
    value = positive_value(first_value(values, *keys))
    return leading_number(value) if value else None


def _shutter_seconds(values: Mapping[str, str]) -> float | None:
    """快门时间（秒）。``1/2000`` 这类分数要自己算，不能只取前面的 1。"""
    value = first_value(values, "EXIF:ExposureTime")
    if not value:
        return None
    if "/" in value:
        head, _, tail = value.partition("/")
        try:
            numerator, denominator = float(head), float(tail)
        except ValueError:
            return None
        seconds = numerator / denominator if denominator else 0.0
    else:
        seconds = leading_number(value) or 0.0
    return seconds if seconds > 0 else None


def _as_text(value: object) -> str:
    """原始值 → 字符串（数组按顿号连接，与元数据面板同一套写法）。"""
    if isinstance(value, (list, tuple)):
        return "、".join(_as_text(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _keywords(values: Mapping[str, object]) -> tuple[str, ...]:
    """关键词：多个来源合并去重，保持出现顺序。"""
    found: list[str] = []
    for key in ("IPTC:Keywords", "XMP:Subject", "XMP-dc:Subject"):
        raw = values.get(key)
        if raw is None:
            continue
        items = raw if isinstance(raw, (list, tuple)) else _KEYWORD_SPLIT.split(str(raw))
        for item in items:
            word = str(item).strip()
            if word and word not in found:
                found.append(word)
    return tuple(found)


# ---- 分组取值 -----------------------------------------------------------------------


def _relative_folder(item: FacetInput) -> str:
    """照片所在文件夹相对库根的位置；就在根下时返回空串。

    库外（理论上不该出现）返回绝对路径 —— 宁可显示得啰嗦，也别显示成空的那一档，
    那样所有库外照片会挤进同一个组。
    """
    try:
        relative = item.ref.path.parent.relative_to(item.root)
    except ValueError:
        return str(item.ref.path.parent)
    return "" if str(relative) == "." else str(relative)


def _folder_label(item: FacetInput) -> str:
    relative = _relative_folder(item)
    return relative or "（根目录）"


def _group_by_year(item: FacetInput) -> Sequence[tuple[str, str]]:
    if item.facets.year is None:
        return ()
    return ((f"{item.facets.year:04d}", f"{item.facets.year} 年"),)


def _group_by_month(item: FacetInput) -> Sequence[tuple[str, str]]:
    year, month = item.facets.year, item.facets.month
    if year is None or month is None:
        return ()
    return ((f"{year:04d}-{month:02d}", f"{year} 年 {month} 月"),)


def _group_by_day(item: FacetInput) -> Sequence[tuple[str, str]]:
    year, month, day = item.facets.year, item.facets.month, item.facets.day
    if year is None or month is None or day is None:
        return ()
    return ((f"{year:04d}-{month:02d}-{day:02d}", f"{year} 年 {month} 月 {day} 日"),)


def _text_group(attribute: str) -> Callable[[FacetInput], Sequence[tuple[str, str]]]:
    """单值文本字段的分组：``(值, 值)``，没有值就是没有组。"""

    def group(item: FacetInput) -> Sequence[tuple[str, str]]:
        text = (getattr(item.facets, attribute) or "").strip()
        return ((text, text),) if text else ()

    return group


def _camera_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    camera = item.facets.camera
    return ((camera, camera),) if camera else ()


def _lens_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    lens = item.facets.lens
    return ((lens, lens),) if lens else ()


def _keyword_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    """一张照片有几个关键词就属于几个组（标签的通常语义）。"""
    return tuple((word, word) for word in item.facets.keywords)


def _date_group_order(key: str) -> float:
    """``"2024-05-16"`` → ``20240516.0``，让日期组按真正的时间先后排。"""
    digits = "".join(character for character in key if character.isdigit())
    return float(digits) if digits else 0.0


def _text_group_order(key: str) -> str:
    return key.casefold()


def _folder_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    return ((_relative_folder(item), _folder_label(item)),)


def _format_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    label = item.ref.format_label
    return ((item.ref.suffix, label),)


# ---- 排序取值 -----------------------------------------------------------------------


def _facet_number(attribute: str) -> Callable[[FacetInput], float | None]:
    def value(item: FacetInput) -> float | None:
        number = getattr(item.facets, attribute)
        return float(number) if number is not None else None

    return value


def _facet_text(attribute: str) -> Callable[[FacetInput], str | None]:
    def value(item: FacetInput) -> str | None:
        text = getattr(item.facets, attribute)
        return text.casefold() if text else None

    return value


def _file_number(attribute: str) -> Callable[[FacetInput], float | None]:
    def value(item: FacetInput) -> float | None:
        return float(getattr(item.ref, attribute))

    return value


def _file_text(attribute: str) -> Callable[[FacetInput], str | None]:
    def value(item: FacetInput) -> str | None:
        text = str(getattr(item.ref, attribute))
        return text.casefold() if text else None

    return value


def _folder_sort(item: FacetInput) -> str:
    return _relative_folder(item).casefold()


# ---- 字段表 -------------------------------------------------------------------------


def _facet(
    id: str,
    label: str,
    kind: FacetKind,
    *,
    group: Callable[[FacetInput], Sequence[tuple[str, str]]] | None = None,
    sort: Callable[[FacetInput], SortValue | None] | None = None,
    group_order: Callable[[str], SortValue] | None = None,
    newest_first: bool = False,
    needs_exiftool: bool = False,
    empty_label: str = "未知",
) -> Facet:
    return Facet(
        id=id,
        label=label,
        kind=kind,
        group=group,
        sort=sort if sort is not None else (lambda _item: None),
        group_order=group_order if group_order is not None else _text_group_order,
        newest_first=newest_first,
        needs_exiftool=needs_exiftool,
        empty_label=empty_label,
    )


_FACETS: Final[tuple[Facet, ...]] = (
    _facet(
        FACET_CAPTURED,
        "拍摄时间",
        "captured",
        sort=lambda item: item.facets.captured_at,
    ),
    _facet(
        FACET_YEAR,
        "拍摄年份",
        "captured",
        group=_group_by_year,
        group_order=_date_group_order,
        newest_first=True,
        empty_label="时间未知",
    ),
    _facet(
        FACET_MONTH,
        "拍摄月份",
        "captured",
        group=_group_by_month,
        group_order=_date_group_order,
        newest_first=True,
        empty_label="时间未知",
    ),
    _facet(
        FACET_DAY,
        "拍摄日期",
        "captured",
        group=_group_by_day,
        group_order=_date_group_order,
        newest_first=True,
        empty_label="时间未知",
    ),
    _facet(
        FACET_CAMERA,
        "相机",
        "text",
        group=_camera_group,
        sort=_facet_text("camera"),
        empty_label="相机未知",
    ),
    _facet(
        FACET_LENS,
        "镜头",
        "text",
        group=_lens_group,
        sort=_facet_text("lens"),
        empty_label="镜头未知",
    ),
    _facet(
        FACET_COUNTRY,
        "国家",
        "text",
        group=_text_group("country"),
        sort=_facet_text("country"),
        needs_exiftool=True,
        empty_label="国家未知",
    ),
    _facet(
        FACET_REGION,
        "省 / 州",
        "text",
        group=_text_group("region"),
        sort=_facet_text("region"),
        needs_exiftool=True,
        empty_label="省州未知",
    ),
    _facet(
        FACET_CITY,
        "城市",
        "text",
        group=_text_group("city"),
        sort=_facet_text("city"),
        needs_exiftool=True,
        empty_label="城市未知",
    ),
    _facet(
        FACET_KEYWORD,
        "关键词",
        "text",
        group=_keyword_group,
        empty_label="无关键词",
    ),
    _facet(
        FACET_FOLDER,
        "所在文件夹",
        "path",
        group=_folder_group,
        sort=_folder_sort,
        empty_label="（根目录）",
    ),
    _facet(FACET_FORMAT, "文件格式", "text", group=_format_group, sort=_file_text("suffix")),
    _facet(FACET_NAME, "文件名", "text", sort=_file_text("name")),
    _facet(FACET_SIZE, "文件大小", "number", sort=_file_number("size")),
    _facet(FACET_MODIFIED, "修改时间", "number", sort=_file_number("mtime_ns")),
    _facet(FACET_APERTURE, "光圈", "number", sort=_facet_number("aperture")),
    _facet(FACET_SHUTTER, "快门", "number", sort=_facet_number("shutter")),
    _facet(FACET_ISO, "ISO", "number", sort=_facet_number("iso")),
    _facet(FACET_FOCAL, "焦距", "number", sort=_facet_number("focal")),
    _facet(FACET_PIXELS, "像素尺寸", "number", sort=_facet_number("pixels")),
)

FACETS_BY_ID: Final[dict[str, Facet]] = {facet.id: facet for facet in _FACETS}

#: 只看文件本身就能拿到的字段：不必等 exiftool，排序可以立刻生效。
FILE_FACET_IDS: Final[frozenset[str]] = frozenset(
    {FACET_FOLDER, FACET_FORMAT, FACET_NAME, FACET_SIZE, FACET_MODIFIED}
)

#: 「分组」子菜单按它生成，顺序即菜单顺序。
GROUP_FACET_IDS: Final[tuple[str, ...]] = (
    FACET_YEAR,
    FACET_MONTH,
    FACET_DAY,
    FACET_CAMERA,
    FACET_LENS,
    FACET_COUNTRY,
    FACET_REGION,
    FACET_CITY,
    FACET_KEYWORD,
    FACET_FOLDER,
    FACET_FORMAT,
)

#: 「排序」子菜单按它生成，顺序即菜单顺序。
#:
#: 先是不用读元数据的一档（立刻生效），再是需要读 EXIF 的一档 —— 排在后面等于
#: 让用户先点快的那一档。关键词不在其中：按一串标签排序没有意义。
SORT_FACET_IDS: Final[tuple[str, ...]] = (
    FACET_NAME,
    FACET_SIZE,
    FACET_MODIFIED,
    FACET_FOLDER,
    FACET_FORMAT,
    FACET_CAPTURED,
    FACET_CAMERA,
    FACET_APERTURE,
    FACET_SHUTTER,
    FACET_ISO,
    FACET_FOCAL,
    FACET_PIXELS,
)

GROUP_FACETS: Final[tuple[Facet, ...]] = tuple(FACETS_BY_ID[fid] for fid in GROUP_FACET_IDS)

SORT_FACETS: Final[tuple[Facet, ...]] = tuple(FACETS_BY_ID[fid] for fid in SORT_FACET_IDS)


def group_facet(facet_id: str | None) -> Facet | None:
    """按 id 取可分组的字段；``None`` 表示不分组。非法 id 也返回 ``None``。"""
    if facet_id is None or facet_id not in GROUP_FACET_IDS:
        return None
    return FACETS_BY_ID[facet_id]


def sort_facet(facet_id: str | None) -> Facet | None:
    """按 id 取可排序的字段；``None`` 表示保持扫描顺序。非法 id 也返回 ``None``。"""
    if facet_id is None or facet_id not in SORT_FACET_IDS:
        return None
    return FACETS_BY_ID[facet_id]


def needs_metadata(facets: Sequence[Facet | None]) -> bool:
    """这些字段里有没有一个必须真去读 EXIF 才能拿到。"""
    return any(facet is not None and facet.id not in FILE_FACET_IDS for facet in facets)


def input_for(ref: PhotoRef, facets: PhotoFacets, root: Path) -> FacetInput:
    """组装取值上下文。"""
    return FacetInput(ref=ref, facets=facets, root=Path(root))


def read_many(
    source: MetadataSource,
    paths: Sequence[Path],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[Path, PhotoFacets]:
    """从来源批量读取一组照片的 facet 值（逐张降级路径用）。

    批量快路径见 ``core.metadata.batch``；这里是同一份提取规则的薄封装，
    给「没有 exiftool，只能逐张读」的场合用。
    """
    result: dict[Path, PhotoFacets] = {}
    for path in paths:
        if should_cancel is not None and should_cancel():
            break
        try:
            raw = source.read(path)
        except MetadataError:
            continue
        result[Path(path)] = extract(raw)
    return result


__all__ = [
    "FACET_APERTURE",
    "FACET_CAMERA",
    "FACET_CAPTURED",
    "FACET_CITY",
    "FACET_COUNTRY",
    "FACET_DAY",
    "FACET_FOLDER",
    "FACET_FORMAT",
    "FACET_FOCAL",
    "FACET_ISO",
    "FACET_KEYWORD",
    "FACET_LENS",
    "FACET_MODIFIED",
    "FACET_MONTH",
    "FACET_NAME",
    "FACET_PIXELS",
    "FACET_REGION",
    "FACET_SHUTTER",
    "FACET_SIZE",
    "FACET_YEAR",
    "FACETS_BY_ID",
    "FILE_FACET_IDS",
    "GROUP_FACET_IDS",
    "GROUP_FACETS",
    "SORT_FACET_IDS",
    "SORT_FACETS",
    "WANTED_TAGS",
    "CaptureMoment",
    "Facet",
    "FacetInput",
    "FacetKind",
    "PhotoFacets",
    "SortValue",
    "extract",
    "group_facet",
    "input_for",
    "needs_metadata",
    "parse_capture_time",
    "read_many",
    "sort_facet",
]
