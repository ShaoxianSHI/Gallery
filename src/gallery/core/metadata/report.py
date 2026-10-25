"""把元数据原始键值整理成给界面看的报告（纯展示，不碰文件）。

exiftool / Pillow 读回来的是一张标签→值的表；这里把它变成中文分组报告（拍摄信息 / 位置 /
图像 / 内容 / 文件），措辞会随界面一起改。**解析与展示分开住**：解析层（``exif`` 的取值助手
与标签表）是契约，改一处牵动写回校验与搜索；这一层只决定「怎么读给用户听」。两者寿命不同，
混在一起会让人以为改一句措辞要动解析。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from gallery.core.formatting import format_bytes, format_timestamp
from gallery.core.i18n import t
from gallery.core.metadata.exif import (
    CAPTURE_TIME_FIELDS,
    OFFSET_TIME_FIELDS,
    as_text,
    camera_name,
    capture_time_parts,
    coordinates_decimal,
    first_value,
    local_video_capture_text,
    place_parts,
    positive_value,
    quicktime_coordinates,
)
from gallery.core.places.countries import place_label


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
        field = MetadataField(label=t("metadata.text.inferred_place"), value=place)
        groups: list[MetadataGroup] = []
        inserted = False
        for group in self.groups:
            if group.title == t("metadata.group.location"):
                groups.append(MetadataGroup(title=group.title, fields=(field, *group.fields)))
                inserted = True
            else:
                groups.append(group)
        if not inserted:
            groups.append(MetadataGroup(title=t("metadata.group.location"), fields=(field,)))
        return replace(self, groups=tuple(groups), place_resolved=True)

    def as_text(self) -> str:
        """纯文本形式，用于「复制全部」。"""
        lines: list[str] = []
        if self.notice:
            lines.append(t("metadata.text.notice", notice=self.notice))
        for group in self.groups:
            lines.append(t("metadata.text.group", title=group.title))
            lines.extend(
                t("metadata.text.field", label=field.label, value=field.value)
                for field in group.fields
            )
        return "\n".join(lines)


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
    if latitude is None or longitude is None:
        latitude, longitude = quicktime_coordinates(normalized)
    groups: list[MetadataGroup] = []
    for title, specs in _curated_groups():
        fields: list[MetadataField] = []
        for spec in specs:
            value = spec.read(normalized)
            if value:
                fields.append(MetadataField(label=spec.label, value=value))
        if fields:
            groups.append(MetadataGroup(title=title, fields=tuple(fields)))
    groups.append(
        MetadataGroup(title=t("metadata.group.file"), fields=_file_fields(path, size, mtime_ns))
    )
    all_fields = tuple(
        MetadataField(label=key, value=value) for key, value in sorted(normalized.items())
    )
    place_city, place_region, place_country = place_parts(normalized)
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


def _shooting_time(raw: Mapping[str, str]) -> str | None:
    """面板上那一行「拍摄时间」。

    照片的 EXIF 是墙钟，原样显示；视频只有 ``CreateDate`` 时那是 UTC，已在
    :func:`~gallery.core.metadata.exif.local_video_capture_text` 里换成本机时区。
    """
    value = first_value(raw, *CAPTURE_TIME_FIELDS)
    if value is None:
        return local_video_capture_text(raw)
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
    return t("metadata.value.seconds", value=_fraction_text(value))


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
        first_value(
            raw,
            "EXIF:ExifImageWidth",
            "File:ImageWidth",
            "EXIF:ImageWidth",
            "QuickTime:ImageWidth",
        )
    )
    height = positive_value(
        first_value(
            raw,
            "EXIF:ExifImageHeight",
            "File:ImageHeight",
            "EXIF:ImageLength",
            "QuickTime:ImageHeight",
        )
    )
    if width and height:
        return f"{width} × {height}"
    return None


#: 视频编码那几串编号 → 用户认得的名字。认不出就原样显示。
_CODEC_LABELS: Final[dict[str, str]] = {
    "avc1": "H.264",
    "hvc1": "HEVC",
    "hev1": "HEVC",
    "mp4v": "MPEG-4",
    "jpeg": "Motion JPEG",
    "mjpg": "Motion JPEG",
}


def _duration(raw: Mapping[str, str]) -> str | None:
    return first_value(raw, "QuickTime:Duration")


def _frame_rate(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "QuickTime:VideoFrameRate")
    if value is None:
        return None
    try:
        rate = float(value)
    except ValueError:
        return f"{value} fps"
    # 30.001 这种是容器里的小数误差，别照抄给用户。
    rounded = round(rate)
    return f"{rounded} fps" if abs(rate - rounded) < 0.05 else f"{rate:g} fps"


def _codec(raw: Mapping[str, str]) -> str | None:
    value = first_value(raw, "QuickTime:CompressorID", "QuickTime:CompressorName")
    if value is None:
        return None
    return _CODEC_LABELS.get(value.casefold(), value)


def _bitrate(raw: Mapping[str, str]) -> str | None:
    return first_value(raw, "QuickTime:AvgBitrate")


def _iso(raw: Mapping[str, str]) -> str | None:
    return positive_value(first_value(raw, "EXIF:ISO", "EXIF:ISOSpeedRatings"))


def _equivalent_focal_length(raw: Mapping[str, str]) -> str | None:
    return positive_value(first_value(raw, "EXIF:FocalLengthIn35mmFormat"))


def _sub_location(raw: Mapping[str, str]) -> str | None:
    """子地点（「西湖 · 断桥残雪」那种更细的位置）。同样两种族名都认，理由见
    :class:`PlaceField`。
    """
    return (
        first_value(raw, "XMP-iptcCore:Location")
        or first_value(raw, "XMP:Location")
        or first_value(raw, "IPTC:Sub-location")
    )


def _place_from_file(raw: Mapping[str, str]) -> str | None:
    """文件里那三层地点拼成一句话（显示用）。**顺序与推断出来的那个一模一样（国家 · 省 · 市）**，
    而且走同一个 :func:`~gallery.core.places.countries.place_label` —— 面板上「地点」与
    「地点（推断）」是挨着的两行，用户正要拿来对照，两处各拼一次早晚漂回去。
    """
    city, region, country = place_parts(raw)
    return place_label(city, region, country) or None


def _coordinates(raw: Mapping[str, str]) -> str | None:
    """统一显示成十进制：度分秒是给机器看的，复制出去也没法直接粘进地图。"""
    latitude, longitude = coordinates_decimal(raw)
    if latitude is None or longitude is None:
        latitude, longitude = quicktime_coordinates(raw)
    if latitude is None and longitude is None:
        return None
    parts: list[str] = []
    if latitude is not None:
        parts.append(f"{abs(latitude):.5f}° {'N' if latitude >= 0 else 'S'}")
    if longitude is not None:
        parts.append(f"{abs(longitude):.5f}° {'E' if longitude >= 0 else 'W'}")
    return t("metadata.text.coordinates_separator").join(parts)


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
    return {"1": "sRGB", "65535": t("metadata.value.uncalibrated")}.get(value, value)


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


def _curated_groups() -> tuple[tuple[str, tuple[_FieldSpec, ...]], ...]:
    return (
        (
            t("metadata.group.shooting"),
            (
                _FieldSpec(t("metadata.label.camera"), camera_name),
                _FieldSpec(
                    t("metadata.label.lens"), _pick("EXIF:LensModel", "EXIF:LensID", "EXIF:Lens")
                ),
                _FieldSpec(t("metadata.label.captured"), _shooting_time),
                _FieldSpec(t("metadata.label.exposure_time"), _exposure_time),
                _FieldSpec(t("metadata.label.aperture"), _aperture),
                _FieldSpec("ISO", _iso),
                _FieldSpec(t("metadata.label.exposure_bias"), _exposure_bias),
                _FieldSpec(t("metadata.label.exposure_program"), _pick("EXIF:ExposureProgram")),
                _FieldSpec(t("metadata.label.metering"), _pick("EXIF:MeteringMode")),
                _FieldSpec(t("metadata.label.flash"), _pick("EXIF:Flash")),
                _FieldSpec(t("metadata.label.focal_length"), _focal_length),
                _FieldSpec(t("metadata.label.focal_35mm"), _equivalent_focal_length),
                _FieldSpec(t("metadata.label.white_balance"), _pick("EXIF:WhiteBalance")),
                _FieldSpec(t("metadata.label.orientation"), _pick("EXIF:Orientation")),
            ),
        ),
        (
            t("metadata.group.location"),
            (
                # **三层地名写成一行**，用「 · 」连起来 —— 与推断出来的地点同一个说法。
                # 分三行摆着看着像三件无关的事，而它们说的其实是同一个地方；子地点是另一个字段。
                _FieldSpec(t("metadata.label.place"), _place_from_file),
                _FieldSpec(t("metadata.label.sub_location"), _sub_location),
                _FieldSpec(t("metadata.label.coordinates"), _coordinates),
                _FieldSpec(t("metadata.label.altitude"), _altitude),
                _FieldSpec(t("metadata.label.gps_time"), _gps_time),
            ),
        ),
        (
            t("metadata.group.image"),
            (
                _FieldSpec(t("metadata.label.pixel_size"), _pixel_size),
                _FieldSpec(t("metadata.label.color_space"), _color_space),
                _FieldSpec(t("metadata.label.software"), _pick("EXIF:Software")),
            ),
        ),
        (
            t("metadata.group.video"),
            (
                _FieldSpec(t("metadata.label.duration"), _duration),
                _FieldSpec(t("metadata.label.frame_rate"), _frame_rate),
                _FieldSpec(t("metadata.label.codec"), _codec),
                _FieldSpec(t("metadata.label.bitrate"), _bitrate),
            ),
        ),
        (
            t("metadata.group.content"),
            (
                _FieldSpec(t("metadata.label.rating"), _pick("XMP:Rating", "EXIF:Rating")),
                _FieldSpec(t("metadata.label.keyword"), _keywords),
                _FieldSpec(t("metadata.label.title"), _pick("XMP-dc:Title", "IPTC:ObjectName")),
                _FieldSpec(
                    t("metadata.label.description"),
                    _pick("XMP-dc:Description", "IPTC:Caption-Abstract", "EXIF:ImageDescription"),
                ),
                _FieldSpec(t("metadata.label.creator"), _creator),
                _FieldSpec(t("metadata.label.copyright"), _copyright),
            ),
        ),
    )


def _file_fields(path: Path, size: int, mtime_ns: int) -> tuple[MetadataField, ...]:
    return (
        MetadataField(t("metadata.label.filename"), path.name),
        MetadataField(t("metadata.label.folder"), str(path.parent)),
        MetadataField(
            t("metadata.label.format"),
            path.suffix.lstrip(".").upper() or t("metadata.label.unknown"),
        ),
        MetadataField(t("metadata.label.size"), format_bytes(size)),
        MetadataField(t("metadata.label.modified"), format_timestamp(mtime_ns)),
    )


__all__ = [
    "MetadataField",
    "MetadataGroup",
    "MetadataReport",
    "build_report",
]
