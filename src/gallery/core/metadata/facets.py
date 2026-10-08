"""可分组 / 可排序的元数据字段（facet）—— **显示归格式化，排序归原始值**。

每个字段带两个取值函数：分组的「组键 + 显示名」与排序用的可比较的值。按给人看的字符串排，
``1/2000 秒`` 与 ``1/400 秒`` 的先后就取决于字典序了。字段分「文件自带」与「读 EXIF」两处来源。
纯 Python，不碰 Qt、不碰文件。
"""

from __future__ import annotations

import calendar
import datetime
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from gallery.core import paths
from gallery.core.events.store import EventRef
from gallery.core.library.photos import PhotoRef

if TYPE_CHECKING:
    # ResolvedPlace 住在 places 域：facets 只按结构用它，运行期不 import，两个域因此不必互相依赖。
    from gallery.core.places.resolver import ResolvedPlace

from gallery.core.metadata.exif import (
    COORDINATE_TAGS,
    WANTED_PLACE_TAGS,
    as_text,
    camera_name,
    coordinates_decimal,
    first_value,
    leading_number,
    place_parts,
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

#: 字段：照片属于哪些事件（ADR 0043）。**取值来自我们自己的库而不是照片**，于是它既不需要
#: exiftool，也不在 :data:`FILE_FACET_IDS` 里 —— 那个集合的语义是「只看文件本身就能拿到」。
FACET_EVENT: Final = "event"

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

#: 排序时能拿到的值。同一个字段内部类型统一（全数字或全字符串），不会撞上「数字和字符串不能比」。
type SortValue = float | str


@dataclass(frozen=True, slots=True)
class FacetInput:
    """取值时能看到的全部东西：照片本身、它的元数据快照、库根目录。

    ``events`` 是这张照片属于哪些事件，**只给 :data:`FACET_EVENT` 那一个字段看**（ADR 0043
    决策 7）—— 事件不是从照片里读出来的元数据，所以它不进 :class:`PhotoFacets`（那是索引里
    存的东西，而事件绑定会被索引重建抹掉），只在这一层多带一份。
    """

    ref: PhotoRef
    facets: PhotoFacets
    root: Path
    events: tuple[EventRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Facet:
    """一个可分组 / 可排序的字段。

    ``group`` 与 ``sort`` 都是纯函数：给同样的输入必得同样的输出，因此单测可以直接
    喂一个构造好的 :class:`FacetInput`，不必碰磁盘。
    """

    id: str
    label: str
    kind: FacetKind
    #: 分组取值：``(组键, 显示名)`` 的列表。**可以多于一个** —— 一张有多个关键词的照片同时属于
    #: 多个组（标签的通常语义）。返回空列表表示没有值；``None`` 表示只能排序、不能分组。
    group: Callable[[FacetInput], Sequence[tuple[str, str]]] | None
    #: 排序取值：没有值就返回 ``None``（一律排在最后，正序逆序都一样）。
    sort: Callable[[FacetInput], SortValue | None] = lambda _input: None
    #: 组键怎么比较。``"2024-05"`` 这类日期键必须**当数字**比（按字典序排会得到「11 月排在
    #: 2 月前面」），其余按名称升序。返回数字的字段，组内所有键都是数字；返回字符串的反之。
    #: **交回 ``None`` 表示「这个组没有值」**，那一组沉在末尾（两个方向下都是）。
    group_order: Callable[[str], SortValue | None] | None = None
    #: 这个字段的**自然方向**是否为「新 → 旧」。时间为真（拍摄时间、年月日、事件），
    #: 其余按名称升序。「逆序」是偏离它而不是字面的 ``reverse``（ADR 0049）——
    #: 照片与组卡片共用这一个判据，所以同一层里两者永远朝着同一个方向。
    newest_first: bool = False
    #: 只有装了 exiftool 才读得到（Pillow 降级路径拿不到 IPTC / XMP）。
    needs_exiftool: bool = False
    #: 取不到值时那张组卡片的标题。
    empty_label: str = "未知"


# ---- 元数据快照 ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhotoFacets:
    """一张照片的分组 / 排序用元数据。这是**索引里存的东西**，也是分组排序唯一依赖的输入。"""

    #: 拍摄时间（epoch 秒）。用来排序。
    captured_at: float | None = None
    #: 拍摄时的年月日（按**照片里记的那个时间**，不换算到本机时区）。
    year: int | None = None
    month: int | None = None
    day: int | None = None
    #: 拍摄时的**钟点**（同上，墙上时间）。日历视图的小时格子按它们摆（ADR 0033）。
    hour: int | None = None
    minute: int | None = None
    camera: str | None = None
    lens: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    keywords: tuple[str, ...] = ()
    #: 十进制度坐标。**只用来离线反查地点**（见 ``core.places``），不做别的用途。
    latitude: float | None = None
    longitude: float | None = None
    #: 地点是不是**按坐标推断**的（文件里没写）。记在索引里是为了**溯源**：这个城市名是照片
    #: 自带的还是算出来的，两者可信级别不同（界面另有「地点（推断）」标注，不为此弹提示）。
    place_inferred: bool = False
    #: 按坐标反查过了没有。**与「查到了什么」分开记，因为查不到也是结论** —— 每次重查既慢，
    #: 又可能因为城市数据刚好下好而给出不一样的答案。数据没下好那次不算（见
    #: ``mark_place_resolved``）：那不是结论，记下来会把「未知」永久钉死。
    place_checked: bool = False
    #: 坐标离反查出来的城市有多远（公里）。界面靠它说清「这只是最近的城市」。
    place_distance_km: float | None = None
    #: 光圈 f 值（如 2.8）。0 与负数等价于「没记录」，已按此归一。
    aperture: float | None = None
    #: 快门时间（秒，如 0.0005）。**秒数越小越快**，界面文案会写反，排序不会。
    shutter: float | None = None
    iso: float | None = None
    #: 焦距（毫米）。
    focal: float | None = None
    #: 总像素数（宽 × 高）。
    pixels: float | None = None


#: exiftool 只需要给出这些标签。点名要而不是拉全量：一张照片的完整标签有好几百个，5000 张
#: 的 JSON 有几十兆，解码它的时间与内存都比排序本身贵得多。**加字段时往这里加一行**。
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
    # 坐标：**国家 / 省州 / 城市这三个字段绝大多数照片靠它**。相机原图通常不写 IPTC / XMP 地点
    # （Lightroom 导出才写），只写 GPS —— 漏了这几行，于是每张照片的地点字段都是空的。
    *COORDINATE_TAGS,
    # 地名三族（IPTC / XMP 一级名 / XMP 全名）都点名要：**exiftool 的 ``-G`` 按一级族回填键**，
    # 只问全名的话这一批根本不会出现在返回值里。清单从 ``exif.PLACE_FIELDS`` 来 ——
    # 与读取那一侧同一份，不会各自漏一行。
    *WANTED_PLACE_TAGS,
    "IPTC:Keywords",
    "XMP:Subject",
    # ``_keywords`` 认 Dublin Core 的 ``dc:subject``（Lightroom / Capture One 把它当主要的关键词
    # 字段），不列在这里它就永远收不到 —— 键在读取端、没在请求端，是一条走不到的路。
    "XMP-dc:Subject",
)

#: IPTC 的关键词字段可能是数组或用分号分隔的字符串。
_KEYWORD_SPLIT = re.compile(r"[;,、]")

#: ``2024:05:16 20:52:02``（EXIF）与 ``2024-05-16T20:52:02+02:00``（XMP）都认。时区偏移**在同一个
#: 正则里**一起吃掉：分开找 ``+`` 前面没有空格会漏掉，漏掉就是把东八区的照片当成 UTC。
_DATETIME = re.compile(
    r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?\s*$"
)


def extract(raw: Mapping[str, object]) -> PhotoFacets:
    """从原始键值里抽出分组 / 排序用的字段（纯函数，测试直接喂字典）。

    取值规则与元数据面板同源（都走 ``exif`` 里的 ``first_value`` 等），但**不共用格式化**：
    这里要的是能比较的东西。值一律先转成字符串再解析 —— 按类型分两套取值函数等于给自己埋一个
    「某个字段类型一变就静默失效」的坑。
    """
    values = {key: value for key, value in raw.items() if key != "SourceFile"}
    text = {key: as_text(value) for key, value in values.items()}
    latitude, longitude = coordinates_decimal(text)
    city, region, country = place_parts(text)
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
        hour=when.hour if when else None,
        minute=when.minute if when else None,
        camera=camera_name(text),
        lens=first_value(text, "EXIF:LensModel", "EXIF:LensID", "EXIF:Lens"),
        # 三族地名的回退链在 ``exif.PlaceField`` 里，与面板那条路共用同一份。
        city=city,
        region=region,
        country=country,
        keywords=_keywords(values),
        latitude=latitude,
        longitude=longitude,
        aperture=_number(text, "EXIF:FNumber", "EXIF:Aperture"),
        shutter=_shutter_seconds(text),
        iso=_number(text, "EXIF:ISO", "EXIF:ISOSpeedRatings"),
        focal=_number(text, "EXIF:FocalLength"),
        pixels=width * height if width and height else None,
    )


@dataclass(frozen=True, slots=True)
class CaptureMoment:
    """照片记下的那个拍摄时刻。

    ``timestamp`` 用来**排序**（真实的时间先后）；其余字段用来**分组**，取照片里记下的墙上时间、
    **不换算到本机时区** —— 否则在东京拍的照片会被塞进相邻的某一天（ADR 0033 决策 3）。
    ``hour`` / ``minute`` 与年月日同一口径（所以**不是**从 ``timestamp`` 反推的）：日历的小时
    格子按它们摆，那是用户唯一认得的那个时间。
    """

    timestamp: float
    year: int
    month: int
    day: int
    hour: int
    minute: int

    @property
    def date(self) -> datetime.date:
        """墙上日期。"""
        return datetime.date(self.year, self.month, self.day)


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
    if not (
        1 <= month <= 12
        and 1 <= day <= calendar.monthrange(year, month)[1]
        and hour < 24
        and minute < 60
    ):
        return None
    offset = _offset_seconds(match.group(7))
    try:
        # 没有时区信息时按本机时间理解：EXIF 习惯不写偏移，强行当成 UTC 会把排序整体平移几个小时，
        # 而分组的年月日本来就取自这串墙上时间，两者保持一致才不会漂到别的日期去。
        epoch = _epoch(year, month, day, hour, minute, second, offset)
    except (ValueError, OverflowError, OSError):
        return None
    return CaptureMoment(timestamp=epoch, year=year, month=month, day=day, hour=hour, minute=minute)


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
    """把「墙上时间」换成 epoch 秒。**写了时区偏移**（含 ``Z``）就按 UTC 理解再减去偏移；
    **没写**（相机 EXIF 的常态）就按本机时区理解 —— 见 :func:`parse_capture_time` 里那一段。
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


def relative_folder(path: Path, root: Path) -> str:
    """照片所在文件夹相对库根的位置（``旅行/2024``）；就在根下时返回空串。

    库外（理论上不该出现）返回绝对路径 —— 宁可显示得啰嗦，也别显示成空的那一档。

    **搜索的「#相册」条件用的是同一个函数**（ADR 0026）：两处各写一份迟早算出不一样的写法。
    """
    folder = Path(path).parent
    # ``resolve=False``：照片路径由扫描器给出，from 库根那条链上已经解析过。
    relative = paths.relative_posix(root, folder, resolve=False)
    return str(folder) if relative is None else relative


def _relative_folder(item: FacetInput) -> str:
    return relative_folder(item.ref.path, item.root)


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
    group_order: Callable[[str], SortValue | None] | None = None,
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


def _event_group(item: FacetInput) -> Sequence[tuple[str, str]]:
    """一张照片属于几个事件就进几个组（与关键词同款的多归属，ADR 0043 决策 7）。

    组键是 ``起始日期#事件 id``：日期在前，:func:`_date_group_order` 那种「把键里的数字取出来
    拼成一个数」的比较法直接可用，而 id 在后是为了让两个同名同日的旅行不挤成一组。
    """
    return tuple((ref.group_key, ref.label) for ref in item.events)


def _event_group_order(key: str) -> float | None:
    """``"2026-05-16#42"`` 按起始日期排。**没填日期的那一组交回 ``None``**，于是它与
    「未归入事件」一样沉在末尾。

    **这里返回 ``None`` 而不是 0**：先前那一组靠 ``build_groups`` 把日期取负来沉底
    （``-0.0`` 大于任何负日期）。方向改成跟着「逆序」之后取负这条路就不成立了 ——
    「沉底」得由调用方显式摘出来，而不是靠键的大小在两个方向下都恰好对（ADR 0049）。
    """
    date_part = key.partition("#")[0]
    return _date_group_order(date_part) if date_part else None


_FACETS: Final[tuple[Facet, ...]] = (
    _facet(
        FACET_CAPTURED,
        "拍摄时间",
        "captured",
        sort=lambda item: item.facets.captured_at,
        newest_first=True,
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
        FACET_EVENT,
        "事件",
        "text",
        group=_event_group,
        group_order=_event_group_order,
        newest_first=True,
        empty_label="未归入事件",
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
        # 关键词只住在 IPTC / XMP 里（``WANTED_TAGS`` 的那三行），Pillow 读不出来。不标这一条，
        # 没装 exiftool 时菜单里那条「关键词」照常可点，点下去满屏都是「无关键词」（城市同理）。
        needs_exiftool=True,
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
    _facet(
        FACET_MODIFIED,
        "修改时间",
        "number",
        sort=_file_number("mtime_ns"),
        newest_first=True,
    ),
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

#: 取值来自**我们自己的数据库**而不是照片的字段（事件，ADR 0043）。既不在文件上也不在 EXIF 里，
#: 所以 :func:`needs_metadata` 要单独放它一马 —— 那一族的取值是一次 SQL，不是重读一遍 EXIF。
GALLERY_OWN_FACET_IDS: Final[frozenset[str]] = frozenset({FACET_EVENT})

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
    FACET_EVENT,
    FACET_FOLDER,
    FACET_FORMAT,
)

#: 「排序」子菜单按它生成，顺序即菜单顺序。先是不用读元数据的一档（立刻生效），再是需要读
#: EXIF 的一档 —— 排在后面等于让用户先点快的那一档。关键词不在其中：按一串标签排序没有意义。
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
    """这些字段里有没有一个必须真去读 EXIF 才能拿到。

    **事件那一族不算**：它不来自照片，而来自我们自己的库。不放它过去的话，用户点一次「按事件
    分组」会先起一个后台任务把整库 EXIF 重读一遍 + 报一条「正在读取 N 张照片…」——而事件值
    只是几毫秒的 SQL（ADR 0043 决策 7）。
    """
    return any(
        facet is not None
        and facet.id not in FILE_FACET_IDS
        and facet.id not in GALLERY_OWN_FACET_IDS
        for facet in facets
    )


def with_resolved_place(facets: PhotoFacets, place: ResolvedPlace | None) -> PhotoFacets:
    """用反查出来的地点补齐缺的那些字段，并记下这是**推断**来的。

    **文件里写了就以文件为准**，反查只填空缺，而且**一个字段一个字段补**（只写了国家时城市可以
    来自反查）；补过就置 ``place_inferred``，距离一并记下（面板要靠它提醒「这只是最近的城市」）。
    是否记「查过了」不归这里管 —— 那是 :func:`mark_place_resolved` 的事。
    """
    if place is None:
        return facets
    city = facets.city or place.city
    region = facets.region or place.region
    country = facets.country or place.country
    # 补了**任意一个**字段就是推断：文件写了城市、反查补的国家同样不是文件自带的。
    pairs = ((facets.city, city), (facets.region, region), (facets.country, country))
    inferred = any(before is None and after is not None for before, after in pairs)
    return replace(
        facets,
        city=city,
        region=region,
        country=country,
        place_inferred=facets.place_inferred or inferred,
        place_distance_km=facets.place_distance_km or place.distance_km,
    )


def mark_place_resolved(facets: PhotoFacets, *, usable: bool = True) -> PhotoFacets:
    """记下「已经按坐标反查过一次」，让索引不必为同一张照片反复反查。

    :param usable: 反查器当时的数据是不是可用。**不可用就别记** —— 数据没下好时 ``place_for``
        也返回 ``None``，但那不是「附近没有城市」的结论，记下来等于把这张照片永久钉在「未知」上。
    """
    return facets if facets.place_checked == usable else replace(facets, place_checked=usable)


def input_for(
    ref: PhotoRef, facets: PhotoFacets, root: Path, events: tuple[EventRef, ...] = ()
) -> FacetInput:
    """组装取值上下文。"""
    return FacetInput(ref=ref, facets=facets, root=Path(root), events=events)


__all__ = [
    "FACETS_BY_ID",
    "FACET_APERTURE",
    "FACET_CAMERA",
    "FACET_CAPTURED",
    "FACET_CITY",
    "FACET_COUNTRY",
    "FACET_DAY",
    "FACET_FOCAL",
    "FACET_FOLDER",
    "FACET_FORMAT",
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
    "FILE_FACET_IDS",
    "GROUP_FACETS",
    "GROUP_FACET_IDS",
    "SORT_FACETS",
    "SORT_FACET_IDS",
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
    "mark_place_resolved",
    "needs_metadata",
    "parse_capture_time",
    "relative_folder",
    "sort_facet",
    "with_resolved_place",
]
