"""读取照片元数据（只读）。

数据来源优先 **exiftool 子进程** —— 它与元数据写回同源，EXIF / IPTC / XMP 一次拿全，
用户看到的和将来写回去的是同一套字段。拿不到 exiftool（没装、超时、输出不可解析）时
降级为 Pillow 能读到的部分，并在报告里**如实说明少了什么**：降级可以，假装没降级不行。

这里只读不写：不起任何缓存文件。结果由界面按 ``(路径, mtime_ns)`` 在内存里缓存。

字段表集中在 ``_CURATED_GROUPS``，加字段只改一处；值一律保持来源原样（``Canon EOS R6``、
``1/500``），只有单位这类机械拼接才在格式化函数里做。
"""

from __future__ import annotations

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

#: exiftool 可执行文件名。它属于运行时外部依赖，不进 pyproject.toml。
EXIFTOOL_EXECUTABLE: Final = "exiftool"

#: 起一次 exiftool 的超时（秒）。超时就降级，不能让界面等下去。
EXIFTOOL_TIMEOUT_SECONDS: Final = 15.0

#: exiftool 用这些串表示「这个字段是空的」。
_EMPTY_VALUES: Final = frozenset({"-", "--", "unknown", ""})


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
    #: 文件里写的地点名（IPTC/XMP）。有它就不再按坐标推断，免得给出两个说法。
    place_from_file: str | None = None

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
        return replace(self, groups=tuple(groups))

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
        argv = [self._executable, "-j", "-G", "-sort", "-charset", "utf8", str(path)]
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
    normalized = {key: _stringify(value) for key, value in raw.items()}
    latitude, longitude = _coordinates_decimal(normalized)
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
    return MetadataReport(
        path=path,
        groups=tuple(groups),
        all_fields=all_fields,
        source=source,
        notice=notice,
        latitude=latitude,
        longitude=longitude,
        place_from_file=_place_from_file(normalized),
    )


# ---- 取值与格式化 ----------------------------------------------------------------


def _stringify(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(_stringify(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _first(raw: Mapping[str, str], *keys: str) -> str | None:
    """按顺序取第一个有内容的值。"""
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = value.strip()
        if text and text.lower() not in _EMPTY_VALUES:
            return text
    return None


def _leading_number(value: str) -> float | None:
    """取字符串开头的数字：``"0.0 mm"`` → 0.0，``"25 mm"`` → 25.0，``"1/500"`` → 1.0。"""
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def _positive(value: str | None) -> str | None:
    """这类字段里 0（或负数）等于「没记录」。

    实测有导出工具把 FNumber / FocalLength / ISO 一律填 0，而且焦距还带着单位
    （``0.0 mm``）；显示成 f/0.0、0.0 mm、ISO 0 只会让人以为软件坏了。
    取不到数字的值（``1/500``）原样保留。
    """
    if value is None:
        return None
    number = _leading_number(value)
    if number is None:
        return value
    return value if number > 0 else None


def _camera(raw: Mapping[str, str]) -> str | None:
    make = _first(raw, "EXIF:Make")
    model = _first(raw, "EXIF:Model")
    if make and model:
        # 「Canon」+「Canon EOS R6」不该重复写成「Canon Canon EOS R6」
        return model if model.lower().startswith(make.lower()) else f"{make} {model}"
    return make or model


def _shooting_time(raw: Mapping[str, str]) -> str | None:
    value = _first(
        raw,
        "EXIF:DateTimeOriginal",
        "EXIF:CreateDate",
        "EXIF:DateTimeDigitized",
        "EXIF:DateTime",
    )
    if value is None:
        return None
    text = value.replace(":", "-", 2)  # 2022:04:16 20:52:02 → 2022-04-16 20:52:02
    offset = _first(raw, "EXIF:OffsetTimeOriginal", "EXIF:OffsetTime")
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
    value = _positive(_first(raw, "EXIF:ExposureTime", "EXIF:ShutterSpeedValue"))
    if value is None:
        return None
    return f"{_fraction_text(value)} 秒"


def _aperture(raw: Mapping[str, str]) -> str | None:
    value = _positive(_first(raw, "EXIF:FNumber", "EXIF:Aperture"))
    if value is not None:
        return value if value.lower().startswith("f/") else f"f/{value}"
    # FNumber 缺失或被填成 0 时退回 APEX 光圈值：f = 2^(AV/2)
    apex = _positive(_first(raw, "EXIF:ApertureValue", "EXIF:MaxApertureValue"))
    if apex is None:
        return None
    try:
        return f"f/{2 ** (float(apex) / 2):.1f}"
    except ValueError:
        return None


def _exposure_bias(raw: Mapping[str, str]) -> str | None:
    value = _first(raw, "EXIF:ExposureCompensation", "EXIF:ExposureBiasValue")
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
    value = _positive(_first(raw, "EXIF:FocalLength"))
    if value is None:
        return None
    if "mm" in value:
        return value
    try:
        return f"{float(value):g} mm"
    except ValueError:
        return value


def _pixel_size(raw: Mapping[str, str]) -> str | None:
    width = _positive(_first(raw, "EXIF:ExifImageWidth", "File:ImageWidth", "EXIF:ImageWidth"))
    height = _positive(_first(raw, "EXIF:ExifImageHeight", "File:ImageHeight", "EXIF:ImageLength"))
    if width and height:
        return f"{width} × {height}"
    return None


def _iso(raw: Mapping[str, str]) -> str | None:
    return _positive(_first(raw, "EXIF:ISO", "EXIF:ISOSpeedRatings"))


def _equivalent_focal_length(raw: Mapping[str, str]) -> str | None:
    return _positive(_first(raw, "EXIF:FocalLengthIn35mmFormat"))


def _coordinates_decimal(raw: Mapping[str, str]) -> tuple[float | None, float | None]:
    """把 ``GPSLatitude`` / ``GPSLongitude``（带半球标记）解析成十进制度。"""
    latitude = _axis_decimal(raw, "EXIF:GPSLatitude", "EXIF:GPSLatitudeRef", negative={"S"})
    longitude = _axis_decimal(raw, "EXIF:GPSLongitude", "EXIF:GPSLongitudeRef", negative={"W"})
    if latitude is None or longitude is None:
        return (None, None)
    return (latitude, longitude)


def _axis_decimal(
    raw: Mapping[str, str], value_key: str, ref_key: str, *, negative: set[str]
) -> float | None:
    value = _first(raw, value_key)
    if value is None:
        return None
    decimal = _degrees_to_decimal(value)
    if decimal is None:
        return None
    # exiftool 给的是 "North"/"West"，Pillow 给的是 "N"/"W"，取首字母统一处理。
    hemisphere = (_first(raw, ref_key) or "").strip().upper()[:1]
    return -abs(decimal) if hemisphere in negative else abs(decimal)


_DEGREE_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _degrees_to_decimal(value: str) -> float | None:
    """``44.83779`` 与 ``44 deg 50' 16.04"`` 都要认。

    exiftool 默认输出的就是度分秒（``44 deg 50' 16.04"``），只认十进制会让
    坐标解析失败、连带反查功能整个不工作。
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


def _place_from_file(raw: Mapping[str, str]) -> str | None:
    """文件里写的地点名（IPTC/XMP）。相机原图通常没有，Lightroom 导出常有。"""
    parts = [
        _first(raw, "XMP-photoshop:City", "IPTC:City"),
        _first(raw, "XMP-photoshop:State", "IPTC:Province-State"),
        _first(raw, "XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"),
    ]
    kept: list[str] = []
    for part in parts:
        if part and part not in kept:
            kept.append(part)
    return " · ".join(kept) if kept else None


def _coordinates(raw: Mapping[str, str]) -> str | None:
    """统一显示成十进制：度分秒是给机器看的，复制出去也没法直接粘进地图。"""
    latitude, longitude = _coordinates_decimal(raw)
    if latitude is None and longitude is None:
        return None
    parts: list[str] = []
    if latitude is not None:
        parts.append(f"{abs(latitude):.5f}° {'N' if latitude >= 0 else 'S'}")
    if longitude is not None:
        parts.append(f"{abs(longitude):.5f}° {'E' if longitude >= 0 else 'W'}")
    return "，".join(parts)


def _altitude(raw: Mapping[str, str]) -> str | None:
    value = _first(raw, "EXIF:GPSAltitude")
    if value is None:
        return None
    text = value if value.endswith("m") else f"{value} m"
    below = _first(raw, "EXIF:GPSAltitudeRef") or ""
    return f"-{text}" if "below" in below.lower() else text


def _gps_time(raw: Mapping[str, str]) -> str | None:
    value = _first(raw, "EXIF:GPSDateTime", "Composite:GPSDateTime")
    if value is None:
        return None
    if value.startswith("00:00:00") or value.startswith("0000:00:00"):
        return None  # 导出工具填的零值，显示出来只是噪音
    return value.replace(":", "-", 2)


def _color_space(raw: Mapping[str, str]) -> str | None:
    value = _first(raw, "EXIF:ColorSpace")
    if value is None:
        return None
    return {"1": "sRGB", "65535": "未校准（Uncalibrated）"}.get(value, value)


def _keywords(raw: Mapping[str, str]) -> str | None:
    return _first(raw, "IPTC:Keywords", "XMP:Subject", "XMP-dc:Subject", "PDF:Keywords")


def _creator(raw: Mapping[str, str]) -> str | None:
    return _first(raw, "IPTC:By-line", "XMP-dc:Creator", "EXIF:Artist")


def _copyright(raw: Mapping[str, str]) -> str | None:
    return _first(raw, "EXIF:Copyright", "IPTC:CopyrightNotice", "XMP-dc:Rights")


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """一个精选字段：中文标签 + 怎么从原始键值里取值。"""

    label: str
    read: Callable[[Mapping[str, str]], str | None]


def _pick(*keys: str) -> Callable[[Mapping[str, str]], str | None]:
    def read(raw: Mapping[str, str]) -> str | None:
        return _first(raw, *keys)

    return read


_CURATED_GROUPS: Final[tuple[tuple[str, tuple[_FieldSpec, ...]], ...]] = (
    (
        "拍摄信息",
        (
            _FieldSpec("相机", _camera),
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
            _FieldSpec("地点", _pick("XMP-photoshop:City", "IPTC:City")),
            _FieldSpec("子地点", _pick("XMP-iptcCore:Location", "IPTC:Sub-location")),
            _FieldSpec("省 / 州", _pick("XMP-photoshop:State", "IPTC:Province-State")),
            _FieldSpec("国家", _pick("XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName")),
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
    """GPS IFD（数字标签 → 值）→ 与 exiftool 同形的键值。

    单独抽出来是因为它纯函数、可单测：Pillow 给的是 ``(度, 分, 秒)`` 三元组，
    写成 JPEG 再读回来看结果太绕。
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
    "EXIFTOOL_EXECUTABLE",
    "EXIFTOOL_TIMEOUT_SECONDS",
    "ExifToolReader",
    "MetadataError",
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "MetadataSource",
    "build_report",
    "read_metadata",
]
