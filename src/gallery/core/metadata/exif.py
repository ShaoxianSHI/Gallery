"""读取照片元数据（只读）。

数据来源优先 **exiftool 子进程** —— 它与元数据写回同源，EXIF / IPTC / XMP 一次拿全。拿不到时
降级为 Pillow，并在报告里**如实说明少了什么**：降级可以，假装没降级不行。这里只读不写，
不起任何缓存文件；把原始键值整理成报告（``report``）与取值规则分开住，取值助手只有这一份。
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from gallery.core.errors import MetadataError
from gallery.core.metadata.pillow_fallback import _read_with_pillow

if TYPE_CHECKING:
    from gallery.core.metadata.report import MetadataReport

#: exiftool 可执行文件名。它属于运行时外部依赖，不进 pyproject.toml。
EXIFTOOL_EXECUTABLE: Final = "exiftool"

#: 起一次 exiftool 的超时（秒）。超时就降级，不能让界面等下去。
EXIFTOOL_TIMEOUT_SECONDS: Final = 15.0

#: exiftool 用这些串表示「这个字段是空的」。
#: exiftool 用这几个词表示「这个字段是空的」。**读取与写回共用这一份**：
#: 写完要回读校验，两边的「空」判错一次就等于刚写进去的东西被自己判成没写。
#: 那个空串成员在两处都够不着（都先 ``strip()``、再用真值判断挡掉），留着是为了
#: 「这一份就是 exiftool 的全部空值写法」这句话本身成立。
EMPTY_VALUES: Final = frozenset({"-", "--", "unknown", ""})

#: 读文件时要带的编码设置。``-charset IPTC=UTF8`` 不能省：exiftool 13 起泛指的 ``-charset
#: UTF8`` 不再覆盖 IPTC，而它的缺省编码仍是 Latin-1 —— 少了这一句，照片里的中文地点名读回来
#: 是一串乱码。写回那一侧用同一份：编码必须是同一套，否则回读校验会把成功的写入判成失败。
EXIFTOOL_CHARSET_ARGS: Final[tuple[str, ...]] = ("-charset", "UTF8", "-charset", "IPTC=UTF8")


class MetadataSource(Protocol):
    """元数据来源：默认是 exiftool，测试注入假实现。"""

    def available(self) -> bool:
        """这个来源现在能不能用。"""
        ...

    def read(self, path: Path) -> Mapping[str, object]:
        """读出原始键值；失败时抛 :class:`MetadataError`。"""
        ...


class ExifToolReader:
    """真正去起 exiftool 子进程的实现。"""

    def __init__(
        self,
        executable: str = EXIFTOOL_EXECUTABLE,
        *,
        timeout: float = EXIFTOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout = timeout

    @property
    def executable(self) -> str:
        return self._executable

    def available(self) -> bool:
        return shutil.which(self._executable) is not None

    def read(self, path: Path) -> Mapping[str, object]:
        # -G 给每个键带上组前缀（EXIF:Make），-j 输出 JSON，-sort 让结果稳定可测。
        argv = [self._executable, "-j", "-G", "-sort", *EXIFTOOL_CHARSET_ARGS, str(path)]
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MetadataError(
                f"读取元数据超时（超过 {self._timeout:g} 秒）：{path.name}。",
                path=path,
                detail=str(exc),
            ) from exc
        except OSError as exc:
            raise MetadataError(
                f"无法运行 exiftool：{exc.strerror or exc}。",
                path=path,
                detail=str(exc),
            ) from exc

        payload = completed.stdout.decode("utf-8", "replace")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MetadataError(
                f"exiftool 的输出无法解析：{path.name}。",
                path=path,
                detail=f"{exc}；输出前 200 字：{payload[:200]}",
            ) from exc
        if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
            raise MetadataError(
                f"exiftool 没有返回可用信息：{path.name}。",
                path=path,
                detail=payload[:200],
            )
        return parsed[0]


def read_metadata(
    path: Path,
    *,
    source: MetadataSource | None = None,
    allow_fallback: bool = True,
) -> MetadataReport:
    """读取 ``path`` 的元数据。

    :param source: 替换默认的 exiftool 来源（测试用）。
    :param allow_fallback: 拿不到 exiftool 时是否降级到 Pillow。
    :raises MetadataError: 来源缺失且不允许降级，或两条路径都失败。
    """
    # 展示层依赖本模块的取值助手，这里延迟取以避开环。
    from gallery.core.metadata.report import build_report

    path = Path(path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise MetadataError(
            f"读不到这个文件：{path.name}（{exc.strerror or exc}）。",
            path=path,
            detail=str(exc),
        ) from exc

    tool: MetadataSource = source if source is not None else ExifToolReader()
    notice: str | None = None
    if tool.available():
        try:
            raw = tool.read(path)
        except MetadataError as exc:
            if not allow_fallback:
                raise
            raw = _read_with_pillow(path)
            notice = f"exiftool 读取失败（{exc.message}），下面显示的是基础信息。"
            return build_report(
                raw,
                path=path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                source="pillow",
                notice=notice,
            )
        return build_report(
            raw,
            path=path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            source="exiftool",
        )

    if not allow_fallback:
        raise MetadataError(
            "没有找到 exiftool，无法读取完整元数据。安装后重试：brew install exiftool",
            path=path,
        )
    return build_report(
        _read_with_pillow(path),
        path=path,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        source="pillow",
        notice=(
            "未安装 exiftool，只显示基础 EXIF（读不到 IPTC / XMP / 制造商注释）。"
            "安装后可看到完整信息：brew install exiftool"
        ),
    )


# ---- 取值与格式化 ----------------------------------------------------------------


def as_text(value: object) -> str:
    """原始字段值 → 字符串（数组按顿号连接，字节按 UTF-8 解，解不出就替换）。

    **分组 / 排序与元数据面板读的是同一批字段**，两处对同一个值必须说出同一句话 ——
    这份实现在 :mod:`gallery.core.metadata.facets` 里也用。
    """
    if isinstance(value, (list, tuple)):
        return "、".join(as_text(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def first_value(raw: Mapping[str, str], *keys: str) -> str | None:
    """按顺序取第一个有内容的值。与 :func:`camera_name` 等一样对外公开：``facets`` 要从同一批
    原始键值里抽字段，规则必须只有这一份，否则两边迟早对不上。
    """
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = value.strip()
        if text and text.lower() not in EMPTY_VALUES:
            return text
    return None


def leading_number(value: str) -> float | None:
    """取字符串开头的数字：``"0.0 mm"`` → 0.0，``"25 mm"`` → 25.0，``"1/500"`` → 1.0。"""
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def positive_value(value: str | None) -> str | None:
    """这类字段里 0（或负数）等于「没记录」：有导出工具把 FNumber / FocalLength / ISO 一律填 0
    还带着单位（``0.0 mm``），显示成 f/0.0 只会让人以为软件坏了。取不到数字的值原样保留。
    """
    if value is None:
        return None
    number = leading_number(value)
    if number is None:
        return value
    return value if number > 0 else None


def camera_name(raw: Mapping[str, str]) -> str | None:
    make = first_value(raw, "EXIF:Make")
    model = first_value(raw, "EXIF:Model")
    if make and model:
        # 「Canon」+「Canon EOS R6」不该重复写成「Canon Canon EOS R6」
        return model if model.lower().startswith(make.lower()) else f"{make} {model}"
    return make or model


# ---- 拍摄时间（ADR 0032）--------------------------------------------------------
# **EXIF 的拍摄时间是墙上时间，不带时区**：时区在单独的可选字段 ``OffsetTimeOriginal``
# （EXIF 2.31 才有）里，相机十有八九不写它。归一化只有一份（下面那几个函数），读报告与
# 写回校验都用它 —— 两边说出的话不一样的话，面板上显示的时间与写进去的时间就对不上。

#: 拍摄时间的那几个标签，与 ``_shooting_time`` 取值的顺序一致。
CAPTURE_TIME_FIELDS: Final[tuple[str, ...]] = (
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "EXIF:DateTimeDigitized",
    "EXIF:DateTime",
)

#: 时区偏移的那两个标签。``OffsetTime`` 是没有 Original 的那些机型的写法。
OFFSET_TIME_FIELDS: Final[tuple[str, ...]] = ("EXIF:OffsetTimeOriginal", "EXIF:OffsetTime")

#: 分隔符不限（EXIF 用 ``:``，界面与 XMP 用 ``-``），秒可省。
_DATETIME_TEXT: Final = re.compile(
    r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"
)

#: ``+08:00`` / ``-0500``。``Z`` 单独认（那是 UTC，与 ``+00:00`` 同一件事）。
_OFFSET_TEXT: Final = re.compile(r"([+-])(\d{2}):?(\d{2})")

#: 现实中存在的最大时区偏移是 **+14:00**（Kiribati）。越过它的一定是手滑。
_MAX_OFFSET_HOURS: Final = 14


def normalize_capture_time(text: str | None) -> str | None:
    """``2024-05-16 20:52:02`` / ``2024:05:16 20:52:02`` → EXIF 的写法；看不懂返回 ``None``。

    **只做「成不合法」的判断，不猜**：``2024-13-01``、``2024-02-31`` 这类原样抛 ``ValueError``
    并被判为看不懂 —— 一个不存在的时间比没有时间糟糕得多。返回值一律补齐到秒：少了秒会被
    exiftool 读回来时补上，校验随即把一次成功的写入判成没写进去。
    """
    if not text:
        return None
    match = _DATETIME_TEXT.match(text.strip())
    if match is None:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups()[:5])
    second = int(match.group(6) or 0)
    try:
        datetime.datetime(year, month, day, hour, minute, second)  # noqa: DTZ001 - 只要它不抛
    except ValueError:
        return None
    return f"{year:04d}:{month:02d}:{day:02d} {hour:02d}:{minute:02d}:{second:02d}"


def normalize_offset(text: str | None) -> str | None:
    """``+08:00`` / ``-0500`` / ``Z`` → ``+08:00``；看不懂返回 ``None``。``Z`` 归到 ``+00:00``：
    它们是同一件事，而留着 ``Z`` 会让「改了没改」比出来是两个写法。
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.upper() == "Z":
        return "+00:00"
    match = _OFFSET_TEXT.fullmatch(stripped)
    if match is None:
        return None
    hours, minutes = int(match.group(2)), int(match.group(3))
    if minutes > 59 or hours > _MAX_OFFSET_HOURS or (hours == _MAX_OFFSET_HOURS and minutes):
        return None
    return f"{'-' if match.group(1) == '-' else '+'}{hours:02d}:{minutes:02d}"


def capture_time_parts(raw: Mapping[str, str]) -> tuple[str | None, str | None]:
    """照片此刻的 ``(拍摄时间, 时区偏移)``，都已归一；没有就是 ``None``（ADR 0032）。
    **绝不按本机时区补一个** —— 凭空猜错的后果是排序整体偏几个小时。
    """
    return (
        normalize_capture_time(first_value(raw, *CAPTURE_TIME_FIELDS)),
        normalize_offset(first_value(raw, *OFFSET_TIME_FIELDS)),
    )


@dataclass(frozen=True, slots=True)
class CoordinateAxis:
    """一个坐标轴：数值标签、半球标签，以及两个半球各写哪个字母。

    纬度与经度**只差这几个字面量**，而它们原先在五处各写了一遍（``writeback`` 的标签
    元组、``place_edit`` 的赋值、``facets`` 的请求清单、``pillow_fallback`` 的兜底，以及
    ``writeback`` 里那两条分派梯子）。写坐标时少写一个标签的后果是另一半留着上一个
    位置的值 —— 一个自信的错答案，比没有坐标糟糕得多。
    """

    value_tag: str
    ref_tag: str
    positive: str
    negative: str

    def decimal(self, raw: Mapping[str, str]) -> float | None:
        """把这一轴解析成十进制度（带正负号）。"""
        return _axis_decimal(raw, self)


#: 两个坐标轴。**这一族标签的唯一来源**。
COORDINATE_AXES: Final[tuple[CoordinateAxis, ...]] = (
    CoordinateAxis("EXIF:GPSLatitude", "EXIF:GPSLatitudeRef", "N", "S"),
    CoordinateAxis("EXIF:GPSLongitude", "EXIF:GPSLongitudeRef", "E", "W"),
)

LATITUDE_AXIS: Final = COORDINATE_AXES[0]
LONGITUDE_AXIS: Final = COORDINATE_AXES[1]

#: 坐标的四个标签。**四个必须一起写**：少写一个，另一半就是上一个位置留下的值 ——
#: 那是一个自信的错答案，比没有坐标糟糕得多。
COORDINATE_TAGS: Final[tuple[str, ...]] = tuple(
    tag for axis in COORDINATE_AXES for tag in (axis.value_tag, axis.ref_tag)
)


def coordinates_decimal(raw: Mapping[str, str]) -> tuple[float | None, float | None]:
    """把 ``GPSLatitude`` / ``GPSLongitude``（带半球标记）解析成十进制度。"""
    latitude = latitude_decimal(raw)
    longitude = longitude_decimal(raw)
    if latitude is None or longitude is None:
        return (None, None)
    return (latitude, longitude)


def latitude_decimal(raw: Mapping[str, str]) -> float | None:
    """只有纬度时用它（写回校验逐轴比对）。单独暴露是为了让 ``writeback`` 不必为了比一个轴而凑出
    一对坐标：两边都得过 :func:`_degrees_to_decimal` 同一份解析，抄一份迟早对不上。
    """
    return LATITUDE_AXIS.decimal(raw)


def longitude_decimal(raw: Mapping[str, str]) -> float | None:
    """只有经度时用它。理由见 :func:`latitude_decimal`。"""
    return LONGITUDE_AXIS.decimal(raw)


def _axis_decimal(raw: Mapping[str, str], axis: CoordinateAxis) -> float | None:
    value = first_value(raw, axis.value_tag)
    if value is None:
        return None
    decimal = _degrees_to_decimal(value)
    if decimal is None:
        return None
    # exiftool 给的是 "North"/"West"，Pillow 给的是 "N"/"W"，取首字母统一处理。
    hemisphere = (first_value(raw, axis.ref_tag) or "").strip().upper()[:1]
    return -abs(decimal) if hemisphere == axis.negative else abs(decimal)


_DEGREE_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _degrees_to_decimal(value: str) -> float | None:
    """``44.83779`` 与 ``44 deg 50' 16.04"`` 都要认 —— exiftool 默认输出的就是度分秒，只认十进制
    会让坐标解析失败、连带反查功能整个不工作。
    """
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    numbers = _DEGREE_NUMBER.findall(text)
    if not numbers:
        return None
    degrees = float(numbers[0])
    minutes = float(numbers[1]) if len(numbers) > 1 else 0.0
    seconds = float(numbers[2]) if len(numbers) > 2 else 0.0
    if minutes >= 60 or seconds >= 60:
        return None  # 不像度分秒，别硬算
    sign = -1.0 if text.startswith("-") else 1.0
    return sign * (degrees + minutes / 60 + seconds / 3600)


@dataclass(frozen=True, slots=True)
class PlaceField:
    """地名的一层：**三个标签**，读写各用其中的一些。

    ``XMP`` 有两种写法要分清，这不是笔误：

    - **写**用一级名 ``XMP:City`` —— exiftool 的 ``-G`` 按**一级族**回填键，写全名会让
      **每一次成功的写入都被判成没写进去**（ADR 0030 决策 2）；
    - **读**要认 ``XMP-photoshop:City``（``-G`` 回填给的就是这个）与 ``XMP:City`` 两种 ——
      相机原图与 Lightroom 导出的键名不一样，只认一种就会有一类照片读不到地点。

    IPTC 那一份永远照原样。子地点（``_sub_location``）不在这里：它用的是另一个 XMP 族
    （``XMP-iptcCore``），而且只用来显示，不参与写回与分组。
    """

    xmp_tag: str
    read_tag: str
    iptc_tag: str

    def from_file(self, raw: Mapping[str, str]) -> str | None:
        """文件里这一层写了什么：先读全名，再读一级名，最后 IPTC。"""
        return (
            first_value(raw, self.read_tag)
            or first_value(raw, self.xmp_tag)
            or first_value(raw, self.iptc_tag)
        )


#: 地名的三层。**这是这一族标签的唯一来源**：请求哪些标签（``facets.WANTED_TAGS``）、
#: 读的时候怎么回退（:meth:`PlaceField.from_file`）、写回时用哪些（``writeback`` 的两张
#: 清单）——全部由它推出来。原先这三族标签在四处各写一遍（``facets`` 的请求清单与
#: ``extract`` 的回退链、``writeback`` 的两张清单、这里的读取链），而漏一处的症状是
#: 「某类照片的地点字段永远是空的」——一个不报错、只是不对的答案。
PLACE_FIELDS: Final[tuple[PlaceField, ...]] = (
    PlaceField("XMP:City", "XMP-photoshop:City", "IPTC:City"),
    PlaceField("XMP:State", "XMP-photoshop:State", "IPTC:Province-State"),
    PlaceField("XMP:Country", "XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"),
)

#: 写回时用的两组标签。
XMP_PLACE_TAGS: Final[tuple[str, ...]] = tuple(field.xmp_tag for field in PLACE_FIELDS)
IPTC_PLACE_TAGS: Final[tuple[str, ...]] = tuple(field.iptc_tag for field in PLACE_FIELDS)

#: 读这一族要**点名要**的全部标签 —— exiftool 只回填你问过的那几个，
#: 少问一个的症状是那一层永远是空的。
WANTED_PLACE_TAGS: Final[tuple[str, ...]] = tuple(
    tag for field in PLACE_FIELDS for tag in (field.read_tag, field.xmp_tag, field.iptc_tag)
)


def place_parts(raw: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    """文件里写的三层地点（市、省、国）。相机原图通常没有，Lightroom 导出常有。"""
    city, region, country = (field.from_file(raw) for field in PLACE_FIELDS)
    return (city, region, country)


__all__ = [
    "CAPTURE_TIME_FIELDS",
    "COORDINATE_AXES",
    "COORDINATE_TAGS",
    "EMPTY_VALUES",
    "EXIFTOOL_CHARSET_ARGS",
    "EXIFTOOL_EXECUTABLE",
    "EXIFTOOL_TIMEOUT_SECONDS",
    "IPTC_PLACE_TAGS",
    "OFFSET_TIME_FIELDS",
    "PLACE_FIELDS",
    "WANTED_PLACE_TAGS",
    "XMP_PLACE_TAGS",
    "ExifToolReader",
    "MetadataError",
    "MetadataSource",
    "PlaceField",
    "as_text",
    "camera_name",
    "capture_time_parts",
    "coordinates_decimal",
    "first_value",
    "latitude_decimal",
    "leading_number",
    "longitude_decimal",
    "normalize_capture_time",
    "normalize_offset",
    "place_parts",
    "positive_value",
    "read_metadata",
]
