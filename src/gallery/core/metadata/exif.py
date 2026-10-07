"""读取照片元数据（只读）。

数据来源优先 **exiftool 子进程** —— 它与元数据写回同源，EXIF / IPTC / XMP 一次拿全。拿不到时
降级为 Pillow，并在报告里**如实说明少了什么**：降级可以，假装没降级不行。这里只读不写，
不起任何缓存文件；字段表集中在 ``_CURATED_GROUPS``，加字段只改一处。
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol

from PIL import ExifTags, Image

from gallery.core.errors import MetadataError
from gallery.core.formatting import format_bytes, format_timestamp
from gallery.core.places.countries import place_label

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


@dataclass(frozen=True, slots=True)
class MetadataField:
    """一条元数据：中文标签 + 原样的值。"""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class MetadataGroup:
    """一组元数据（拍摄信息 / 位置 / 图像 / 内容 / 文件）。"""

    title: str
    fields: tuple[MetadataField, ...]


@dataclass(frozen=True, slots=True)
class MetadataReport:
    """一次读取的完整结果。"""

    path: Path
    groups: tuple[MetadataGroup, ...]
    #: 来源返回的全部键值（标签就是原始标签名），供「显示全部字段」用。
    all_fields: tuple[MetadataField, ...]
    #: ``"exiftool"`` 或 ``"pillow"``。
    source: str
    #: 降级或异常时的说明，没有则为 ``None``。
    notice: str | None = None
    #: 十进制度的坐标（有 GPS 时才有），供离线反查用。
    latitude: float | None = None
    longitude: float | None = None
    #: 文件里写的地点名（IPTC/XMP），拼成一句 ``中国 · 浙江省 · 杭州``。
    #: 有它就不再按坐标推断，免得给出两个说法。
    place_from_file: str | None = None
    #: 文件里那三层地点**分开**的值，给「编辑元数据」的输入框用（ADR 0031）。**从
    #: ``place_from_file`` 那句拼好的话里拆是不行的** —— 地名本身就有「 · 」。
    place_city: str | None = None
    place_region: str | None = None
    place_country: str | None = None
    #: 照片记下的**拍摄时间**与**时区偏移**，都已归一（``+08:00``）（ADR 0032）。**不从面板
    #: 显示的那一行反解** —— 拆回两个输入框要写第二套解析，而两套规则早晚会各自漂移。
    captured_at: str | None = None
    captured_offset: str | None = None
    #: 推断地点是否已经查过了。**必须区分「没查」与「查了但没有结果」**：只有还没查过的报告
    #: 才需要再查一次，少了这个标记，命中缓存的路径会把「尚未推断」当成「推断不出」。
    place_resolved: bool = False

    @property
    def is_complete(self) -> bool:
        """是否拿到了完整信息（只有 exiftool 路径才算）。"""
        return self.source == "exiftool"

    def with_place(self, place: str) -> MetadataReport:
        """把推断出来的地点插进「位置」组的最前面。

        标签是「地点（推断）」而不是「地点」：文件里写的和软件推的不是一个可信级别，
        混在一起用户会以为那是照片自带的元数据。
        """
        field = MetadataField(label="地点（推断）", value=place)
        groups: list[MetadataGroup] = []
        inserted = False
        for group in self.groups:
            if group.title == "位置":
                groups.append(MetadataGroup(title=group.title, fields=(field, *group.fields)))
                inserted = True
            else:
                groups.append(group)
        if not inserted:
            groups.append(MetadataGroup(title="位置", fields=(field,)))
        return replace(self, groups=tuple(groups), place_resolved=True)

    def as_text(self) -> str:
        """纯文本形式，用于「复制全部」。"""
        lines: list[str] = []
        if self.notice:
            lines.append(f"（{self.notice}）")
        for group in self.groups:
            lines.append(f"【{group.title}】")
            lines.extend(f"{field.label}：{field.value}" for field in group.fields)
        return "\n".join(lines)


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


def build_report(
    raw: Mapping[str, object],
    *,
    path: Path,
    size: int,
    mtime_ns: int,
    source: str,
    notice: str | None = None,
) -> MetadataReport:
    """把来源的原始键值整理成分组报告（纯函数，测试直接喂字典）。"""
    normalized = {key: as_text(value) for key, value in raw.items()}
    latitude, longitude = coordinates_decimal(normalized)
    groups: list[MetadataGroup] = []
    for title, specs in _CURATED_GROUPS:
        fields: list[MetadataField] = []
        for spec in specs:
            value = spec.read(normalized)
            if value:
                fields.append(MetadataField(label=spec.label, value=value))
        if fields:
            groups.append(MetadataGroup(title=title, fields=tuple(fields)))
    groups.append(MetadataGroup(title="文件", fields=_file_fields(path, size, mtime_ns)))
    all_fields = tuple(
        MetadataField(label=key, value=value) for key, value in sorted(normalized.items())
    )
    place_city, place_region, place_country = _place_parts(normalized)
    captured_at, captured_offset = capture_time_parts(normalized)
    return MetadataReport(
        path=path,
        groups=tuple(groups),
        all_fields=all_fields,
        source=source,
        notice=notice,
        latitude=latitude,
        longitude=longitude,
        place_from_file=_place_from_file(normalized),
        place_city=place_city,
        place_region=place_region,
        place_country=place_country,
        captured_at=captured_at,
        captured_offset=captured_offset,
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


def _shooting_time(raw: Mapping[str, str]) -> str | None:
    """面板上那一行「拍摄时间」。**原样显示墙上时间，不换算到本机时区**，带得上偏移就跟在后面
    （``2022-04-16 20:52:02 +08:00``）—— 那正是照片自己记下的说法，换算一遍只会让用户对不上。
    """
    value = first_value(raw, *CAPTURE_TIME_FIELDS)
    if value is None:
        return None
    text = value.replace(":", "-", 2)  # 2022:04:16 20:52:02 → 2022-04-16 20:52:02
    offset = first_value(raw, *OFFSET_TIME_FIELDS)
    return f"{text} {offset}" if offset else text


def _fraction_text(value: str) -> str:
    """把 ``0.002`` 这类小数写成 ``1/500``；已经是分数就原样返回。"""
    if "/" in value:
        return value
    try:
        seconds = float(value)
    except ValueError:
        return value
    if seconds <= 0:
        return value
    if seconds >= 1:
        return f"{seconds:g}"
    return f"1/{round(1 / seconds)}"


def _exposure_time(raw: Mapping[str, str]) -> str | None:
    value = positive_value(first_value(raw, "EXIF:ExposureTime", "EXIF:ShutterSpeedValue"))
    if value is None:
        return None
    return f"{_fraction_text(value)} 秒"


def _aperture(raw: Mapping[str, str]) -> str | None:
    value = positive_value(first_value(raw, "EXIF:FNumber", "EXIF:Aperture"))
    if value is not None:
        return value if value.lower().startswith("f/") else f"f/{value}"
    # FNumber 缺失或被填成 0 时退回 APEX 光圈值：f = 2^(AV/2)
    apex = positive_value(first_value(raw, "EXIF:ApertureValue", "EXIF:MaxApertureValue"))
    if apex is None:
        return None
    try:
        return f"f/{2 ** (float(apex) / 2):.1f}"
    except ValueError:
        return None


def _exposure_bias(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "EXIF:ExposureCompensation", "EXIF:ExposureBiasValue")
    if value is None:
        return None
    if "ev" in value.lower():
        return value
    try:
        amount = float(value)
    except ValueError:
        return value
    return "0 EV" if amount == 0 else f"{amount:+g} EV"


def _focal_length(raw: Mapping[str, str]) -> str | None:
    value = positive_value(first_value(raw, "EXIF:FocalLength"))
    if value is None:
        return None
    if "mm" in value:
        return value
    try:
        return f"{float(value):g} mm"
    except ValueError:
        return value


def _pixel_size(raw: Mapping[str, str]) -> str | None:
    width = positive_value(
        first_value(raw, "EXIF:ExifImageWidth", "File:ImageWidth", "EXIF:ImageWidth")
    )
    height = positive_value(
        first_value(raw, "EXIF:ExifImageHeight", "File:ImageHeight", "EXIF:ImageLength")
    )
    if width and height:
        return f"{width} × {height}"
    return None


def _iso(raw: Mapping[str, str]) -> str | None:
    return positive_value(first_value(raw, "EXIF:ISO", "EXIF:ISOSpeedRatings"))


def _equivalent_focal_length(raw: Mapping[str, str]) -> str | None:
    return positive_value(first_value(raw, "EXIF:FocalLengthIn35mmFormat"))


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
    return _axis_decimal(raw, "EXIF:GPSLatitude", "EXIF:GPSLatitudeRef", negative={"S"})


def longitude_decimal(raw: Mapping[str, str]) -> float | None:
    """只有经度时用它。理由见 :func:`latitude_decimal`。"""
    return _axis_decimal(raw, "EXIF:GPSLongitude", "EXIF:GPSLongitudeRef", negative={"W"})


def _axis_decimal(
    raw: Mapping[str, str], value_key: str, ref_key: str, *, negative: set[str]
) -> float | None:
    value = first_value(raw, value_key)
    if value is None:
        return None
    decimal = _degrees_to_decimal(value)
    if decimal is None:
        return None
    # exiftool 给的是 "North"/"West"，Pillow 给的是 "N"/"W"，取首字母统一处理。
    hemisphere = (first_value(raw, ref_key) or "").strip().upper()[:1]
    return -abs(decimal) if hemisphere in negative else abs(decimal)


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


def _sub_location(raw: Mapping[str, str]) -> str | None:
    """子地点（「西湖 · 断桥残雪」那种更细的位置）。同样两种族名都认，理由见 :func:`_xmp_value`。"""
    return (
        first_value(raw, "XMP-iptcCore:Location")
        or first_value(raw, "XMP:Location")
        or first_value(raw, "IPTC:Sub-location")
    )


def _xmp_value(raw: Mapping[str, str], tag: str) -> str | None:
    """按 ``XMP-photoshop:City`` 这样的全名取，取不到再按一级族名 ``XMP:City`` 取。

    **两个都要认**：exiftool 的 ``-G`` 按**一级族**回填键，13.x 给的是 ``XMP:City``；只认全名的话，
    Lightroom 那种**只写 XMP、不写 IPTC** 的照片，面板上的「地点」永远是空的。
    """
    return first_value(raw, f"XMP-photoshop:{tag}") or first_value(raw, f"XMP:{tag}")


def _place_parts(raw: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    """文件里写的三层地点（IPTC/XMP）。相机原图通常没有，Lightroom 导出常有。"""
    return (
        _xmp_value(raw, "City") or first_value(raw, "IPTC:City"),
        _xmp_value(raw, "State") or first_value(raw, "IPTC:Province-State"),
        _xmp_value(raw, "Country") or first_value(raw, "IPTC:Country-PrimaryLocationName"),
    )


def _place_from_file(raw: Mapping[str, str]) -> str | None:
    """文件里那三层地点拼成一句话（显示用）。**顺序与推断出来的那个一模一样（国家 · 省 · 市）**，
    而且走同一个 :func:`~gallery.core.places.countries.place_label` —— 面板上「地点」与
    「地点（推断）」是挨着的两行，用户正要拿来对照，两处各拼一次早晚漂回去。
    """
    city, region, country = _place_parts(raw)
    return place_label(city, region, country) or None


def _coordinates(raw: Mapping[str, str]) -> str | None:
    """统一显示成十进制：度分秒是给机器看的，复制出去也没法直接粘进地图。"""
    latitude, longitude = coordinates_decimal(raw)
    if latitude is None and longitude is None:
        return None
    parts: list[str] = []
    if latitude is not None:
        parts.append(f"{abs(latitude):.5f}° {'N' if latitude >= 0 else 'S'}")
    if longitude is not None:
        parts.append(f"{abs(longitude):.5f}° {'E' if longitude >= 0 else 'W'}")
    return "，".join(parts)


def _altitude(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "EXIF:GPSAltitude")
    if value is None:
        return None
    text = value if value.endswith("m") else f"{value} m"
    below = first_value(raw, "EXIF:GPSAltitudeRef") or ""
    return f"-{text}" if "below" in below.lower() else text


def _gps_time(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "EXIF:GPSDateTime", "Composite:GPSDateTime")
    if value is None:
        return None
    if value.startswith("00:00:00") or value.startswith("0000:00:00"):
        return None  # 导出工具填的零值，显示出来只是噪音
    return value.replace(":", "-", 2)


def _color_space(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "EXIF:ColorSpace")
    if value is None:
        return None
    return {"1": "sRGB", "65535": "未校准（Uncalibrated）"}.get(value, value)


def _keywords(raw: Mapping[str, str]) -> str | None:
    return first_value(raw, "IPTC:Keywords", "XMP:Subject", "XMP-dc:Subject", "PDF:Keywords")


def _creator(raw: Mapping[str, str]) -> str | None:
    return first_value(raw, "IPTC:By-line", "XMP-dc:Creator", "EXIF:Artist")


def _copyright(raw: Mapping[str, str]) -> str | None:
    return first_value(raw, "EXIF:Copyright", "IPTC:CopyrightNotice", "XMP-dc:Rights")


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """一个精选字段：中文标签 + 怎么从原始键值里取值。"""

    label: str
    read: Callable[[Mapping[str, str]], str | None]


def _pick(*keys: str) -> Callable[[Mapping[str, str]], str | None]:
    def read(raw: Mapping[str, str]) -> str | None:
        return first_value(raw, *keys)

    return read


_CURATED_GROUPS: Final[tuple[tuple[str, tuple[_FieldSpec, ...]], ...]] = (
    (
        "拍摄信息",
        (
            _FieldSpec("相机", camera_name),
            _FieldSpec("镜头", _pick("EXIF:LensModel", "EXIF:LensID", "EXIF:Lens")),
            _FieldSpec("拍摄时间", _shooting_time),
            _FieldSpec("曝光时间", _exposure_time),
            _FieldSpec("光圈", _aperture),
            _FieldSpec("ISO", _iso),
            _FieldSpec("曝光补偿", _exposure_bias),
            _FieldSpec("曝光程序", _pick("EXIF:ExposureProgram")),
            _FieldSpec("测光模式", _pick("EXIF:MeteringMode")),
            _FieldSpec("闪光灯", _pick("EXIF:Flash")),
            _FieldSpec("焦距", _focal_length),
            _FieldSpec("35mm 等效", _equivalent_focal_length),
            _FieldSpec("白平衡", _pick("EXIF:WhiteBalance")),
            _FieldSpec("方向", _pick("EXIF:Orientation")),
        ),
    ),
    (
        "位置",
        (
            # **三层地名写成一行**，用「 · 」连起来 —— 与推断出来的地点同一个说法。分三行摆着看着像
            # 三件无关的事，而它们说的其实是同一个地方；子地点是另一个字段，仍单列一行。
            _FieldSpec("地点", _place_from_file),
            _FieldSpec("子地点", _sub_location),
            _FieldSpec("坐标", _coordinates),
            _FieldSpec("海拔", _altitude),
            _FieldSpec("定位时间", _gps_time),
        ),
    ),
    (
        "图像",
        (
            _FieldSpec("像素尺寸", _pixel_size),
            _FieldSpec("色彩空间", _color_space),
            _FieldSpec("处理软件", _pick("EXIF:Software")),
        ),
    ),
    (
        "内容",
        (
            _FieldSpec("评级", _pick("XMP:Rating", "EXIF:Rating")),
            _FieldSpec("关键词", _keywords),
            _FieldSpec("标题", _pick("XMP-dc:Title", "IPTC:ObjectName")),
            _FieldSpec(
                "说明",
                _pick("XMP-dc:Description", "IPTC:Caption-Abstract", "EXIF:ImageDescription"),
            ),
            _FieldSpec("创建者", _creator),
            _FieldSpec("版权", _copyright),
        ),
    ),
)


def _file_fields(path: Path, size: int, mtime_ns: int) -> tuple[MetadataField, ...]:
    return (
        MetadataField("文件名", path.name),
        MetadataField("所在文件夹", str(path.parent)),
        MetadataField("格式", path.suffix.lstrip(".").upper() or "未知"),
        MetadataField("大小", format_bytes(size)),
        MetadataField("修改时间", format_timestamp(mtime_ns)),
    )


# ---- Pillow 降级路径 -------------------------------------------------------------

#: GPS IFD 里用数字标签，这里给出可读的名字。
_GPS_LATITUDE_REF: Final = 1
_GPS_LATITUDE: Final = 2
_GPS_LONGITUDE_REF: Final = 3
_GPS_LONGITUDE: Final = 4
_GPS_ALTITUDE_REF: Final = 5
_GPS_ALTITUDE: Final = 6
_GPS_DATE_STAMP: Final = 29

#: Pillow 只能给出数字的枚举，降级时补一份可读映射（exiftool 本身给的是英文串）。
_PILLOW_ENUMS: Final[dict[str, dict[int, str]]] = {
    "EXIF:Flash": {
        0: "未闪光",
        1: "闪光",
        5: "闪光（未检测到回光）",
        7: "闪光（检测到回光）",
        9: "闪光（强制）",
        16: "未闪光（强制）",
        24: "未闪光（自动）",
        25: "闪光（自动）",
    },
    "EXIF:WhiteBalance": {0: "自动", 1: "手动"},
    "EXIF:Orientation": {1: "正常", 3: "旋转 180°", 6: "顺时针 90°", 8: "逆时针 90°"},
    "EXIF:ColorSpace": {1: "sRGB", 65535: "未校准（Uncalibrated）"},
    "EXIF:ExposureProgram": {
        0: "未定义",
        1: "手动",
        2: "程序自动",
        3: "光圈优先",
        4: "快门优先",
        5: "创意",
        6: "运动",
        7: "人像",
        8: "风景",
    },
    "EXIF:MeteringMode": {
        0: "未知",
        1: "平均",
        2: "中央重点",
        3: "点测光",
        4: "多点",
        5: "评价",
        6: "局部",
    },
}


class PillowSource:
    """降级来源：只用 Pillow 读基础 EXIF，不起子进程。批量读分组 / 排序字段时缺 exiftool 也走这里：
    逐张用 Pillow 读比每张起一个子进程快得多，只是拿不到 IPTC / XMP。
    """

    def available(self) -> bool:
        return True

    def read(self, path: Path) -> Mapping[str, object]:
        try:
            return _read_with_pillow(Path(path))
        except (OSError, ValueError, SyntaxError) as exc:
            raise MetadataError(
                f"读不出这个文件的基本信息：{Path(path).name}（{exc}）。",
                path=Path(path),
                detail=str(exc),
            ) from exc


def _read_with_pillow(path: Path) -> dict[str, object]:
    """降级路径：读出能读到的 EXIF（含 GPS 与 Exif 子 IFD）。"""
    with Image.open(path) as image:
        exif = image.getexif()
        raw: dict[str, object] = {}
        for tag_id, value in exif.items():
            raw[f"EXIF:{_tag_name(tag_id)}"] = _pillow_value(f"EXIF:{_tag_name(tag_id)}", value)
        for tag_id, value in exif.get_ifd(ExifTags.IFD.Exif).items():
            key = f"EXIF:{_tag_name(tag_id)}"
            raw.setdefault(key, _pillow_value(key, value))
        raw.update(_pillow_gps(exif))
    return raw


def _tag_name(tag_id: int) -> str:
    return str(ExifTags.TAGS.get(tag_id, tag_id))


def _pillow_value(key: str, value: object) -> object:
    mapping = _PILLOW_ENUMS.get(key)
    if mapping is not None and isinstance(value, int) and not isinstance(value, bool):
        return mapping.get(value, value)
    return value


def _pillow_gps(exif: Image.Exif) -> dict[str, object]:
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except (KeyError, AttributeError):  # pragma: no cover - 没有 GPS IFD 的文件
        return {}
    return _gps_fields(gps)


def _gps_fields(gps: Mapping[int, object]) -> dict[str, object]:
    """GPS IFD（数字标签 → 值）→ 与 exiftool 同形的键值。单独抽出来是因为它纯函数、可单测：
    Pillow 给的是 ``(度, 分, 秒)`` 三元组，写成 JPEG 再读回来看结果太绕。
    """
    if not gps:
        return {}

    result: dict[str, object] = {}
    latitude = _gps_decimal(gps.get(_GPS_LATITUDE), gps.get(_GPS_LATITUDE_REF))
    if latitude is not None:
        result["EXIF:GPSLatitude"] = f"{abs(latitude):.5f}"
        result["EXIF:GPSLatitudeRef"] = "N" if latitude >= 0 else "S"
    longitude = _gps_decimal(gps.get(_GPS_LONGITUDE), gps.get(_GPS_LONGITUDE_REF))
    if longitude is not None:
        result["EXIF:GPSLongitude"] = f"{abs(longitude):.5f}"
        result["EXIF:GPSLongitudeRef"] = "E" if longitude >= 0 else "W"

    altitude = gps.get(_GPS_ALTITUDE)
    if isinstance(altitude, (int, float)):
        result["EXIF:GPSAltitude"] = f"{float(altitude):.1f}"
        below = gps.get(_GPS_ALTITUDE_REF)
        result["EXIF:GPSAltitudeRef"] = "Below Sea Level" if below else "Above Sea Level"

    stamp = gps.get(_GPS_DATE_STAMP)
    if isinstance(stamp, str) and stamp.count(":") == 2 and not stamp.startswith("00:00"):
        result["EXIF:GPSDateTime"] = stamp
    return result


def _gps_decimal(value: object, ref: object) -> float | None:
    """``(度, 分, 秒)`` + 半球标记 → 十进制度。"""
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in value[:3])
    except (TypeError, ValueError):
        return None
    decimal = degrees + minutes / 60 + seconds / 3600
    if isinstance(ref, str) and ref.strip().upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


__all__ = [
    "CAPTURE_TIME_FIELDS",
    "EMPTY_VALUES",
    "EXIFTOOL_CHARSET_ARGS",
    "EXIFTOOL_EXECUTABLE",
    "EXIFTOOL_TIMEOUT_SECONDS",
    "OFFSET_TIME_FIELDS",
    "ExifToolReader",
    "MetadataError",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "PillowSource",
    "as_text",
    "build_report",
    "camera_name",
    "capture_time_parts",
    "coordinates_decimal",
    "first_value",
    "latitude_decimal",
    "leading_number",
    "longitude_decimal",
    "normalize_capture_time",
    "normalize_offset",
    "positive_value",
    "read_metadata",
]
