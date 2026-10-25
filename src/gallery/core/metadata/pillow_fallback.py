"""元数据读取的 Pillow 降级路径。

exiftool 拿不到时（没装、超时、无法解析）改走这里：只用 Pillow 读基础 EXIF（含 GPS 与
Exif 子 IFD），不起子进程。它躲在 :class:`~gallery.core.metadata.exif.MetadataSource`
协议后面，因此与解析 / 展示层分开住 —— 单独成模块是为了让「降级能读到什么」这件事
只有一份来源（单张读取与批量读取共用它）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from PIL import ExifTags, Image

from gallery.core.errors import MetadataError
from gallery.core.i18n import t

#: GPS IFD 里用数字标签，这里给出可读的名字。
_GPS_LATITUDE_REF: Final = 1
_GPS_LATITUDE: Final = 2
_GPS_LONGITUDE_REF: Final = 3
_GPS_LONGITUDE: Final = 4
_GPS_ALTITUDE_REF: Final = 5
_GPS_ALTITUDE: Final = 6
_GPS_DATE_STAMP: Final = 29

#: 与 :data:`COORDINATE_AXES` **按位置配对**的 Pillow 数字标签：``(值, 半球)``。
#: 两边数量对不上时 :func:`_gps_fields` 里那句 ``zip(strict=True)`` 会当场抛。
_GPS_AXIS_KEYS: Final[tuple[tuple[int, int], ...]] = (
    (_GPS_LATITUDE, _GPS_LATITUDE_REF),
    (_GPS_LONGITUDE, _GPS_LONGITUDE_REF),
)

#: Pillow 只能给出数字的枚举，降级时补一份可读映射（exiftool 本身给的是英文串）。
_PILLOW_ENUMS: Final[dict[str, dict[int, str]]] = {
    "EXIF:Flash": {
        0: "metadata.exif.flash.off",
        1: "metadata.exif.flash.on",
        5: "metadata.exif.flash.no_return",
        7: "metadata.exif.flash.return",
        9: "metadata.exif.flash.forced",
        16: "metadata.exif.flash.off_forced",
        24: "metadata.exif.flash.off_auto",
        25: "metadata.exif.flash.auto",
    },
    "EXIF:WhiteBalance": {
        0: "metadata.exif.white_balance.auto",
        1: "metadata.exif.white_balance.manual",
    },
    "EXIF:Orientation": {
        1: "metadata.exif.orientation.normal",
        3: "metadata.exif.orientation.rotated_180",
        6: "metadata.exif.orientation.clockwise_90",
        8: "metadata.exif.orientation.counterclockwise_90",
    },
    "EXIF:ColorSpace": {1: "sRGB", 65535: "metadata.exif.color_space.uncalibrated"},
    "EXIF:ExposureProgram": {
        0: "metadata.exif.exposure_program.undefined",
        1: "metadata.exif.exposure_program.manual",
        2: "metadata.exif.exposure_program.program",
        3: "metadata.exif.exposure_program.aperture_priority",
        4: "metadata.exif.exposure_program.shutter_priority",
        5: "metadata.exif.exposure_program.creative",
        6: "metadata.exif.exposure_program.action",
        7: "metadata.exif.exposure_program.portrait",
        8: "metadata.exif.exposure_program.landscape",
    },
    "EXIF:MeteringMode": {
        0: "metadata.exif.metering.unknown",
        1: "metadata.exif.metering.average",
        2: "metadata.exif.metering.center_weighted",
        3: "metadata.exif.metering.spot",
        4: "metadata.exif.metering.multi",
        5: "metadata.exif.metering.evaluative",
        6: "metadata.exif.metering.partial",
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
                t("metadata.exif.pillow_unreadable", name=Path(path).name, error=exc),
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
        label = mapping.get(value)
        return t(label) if label is not None else value
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

    # 标签表住在 exif，而它要 import 本模块的降级来源，延迟到这里取以避开环。
    from gallery.core.metadata.exif import COORDINATE_AXES

    result: dict[str, object] = {}
    for axis, keys in zip(COORDINATE_AXES, _GPS_AXIS_KEYS, strict=True):
        value_key, ref_key = keys
        decimal = _gps_decimal(gps.get(value_key), gps.get(ref_key))
        if decimal is None:
            continue
        # 保留 **5 位**：这是「读回来的样子」，不是我们写回去的那一份（``place_edit`` 写 6 位）。
        result[axis.value_tag] = f"{abs(decimal):.5f}"
        result[axis.ref_tag] = axis.positive if decimal >= 0 else axis.negative

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


__all__ = ["PillowSource"]
