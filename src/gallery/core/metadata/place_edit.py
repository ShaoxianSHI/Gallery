"""用户在编辑器里改的那份位置，翻译成要写进文件的字段（ADR 0031）。

**只做翻译，不碰文件** —— 写盘、记账、校验与撤销都在 ``metadata.writeback``。坐标 → 地点
是反查、地点 → 坐标是正查（查不到就只写地名、坐标原样不动）；两个方向撞在一起时用户填的
说了算，不用反查结果盖掉他填的地名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from gallery.core.errors import MetadataWriteError
from gallery.core.i18n import t
from gallery.core.metadata.exif import COORDINATE_AXES, PLACE_FIELDS
from gallery.core.metadata.writeback import COORDINATE_TAGS, STALE_GPS_TAGS, Assignment
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import is_real_coordinate

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver

#: 判「坐标有没有变」的容差（度）。约 1.1 米 —— 比这更小的改动不值得改一次文件。
_COORDINATE_EPSILON: Final = 1e-5


@dataclass(frozen=True, slots=True)
class PlaceState:
    """照片此刻的位置（面板上显示的那一份）。"""

    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def label(self) -> str:
        return place_label(self.city, self.region, self.country)

    def coordinates(self) -> tuple[float, float] | None:
        """坐标成对给出：只有一半的坐标没有任何意义（见 ``is_real_coordinate``）。"""
        if self.latitude is None or self.longitude is None:
            return None
        return (self.latitude, self.longitude)

    def names(self) -> tuple[str | None, str | None, str | None]:
        return (self.city, self.region, self.country)


@dataclass(frozen=True, slots=True)
class PlaceEdit:
    """用户在编辑器里填完的**那一份**，不是「改了哪些」。

    编辑器打开时就把照片此刻的值填进去，用户改完直接保存 —— 收到的是「最终应该是什么样」
    而不是补丁：**空串表示那一层要清掉**（与「没动它」是两件事，那种形状没法表达「清空城市」）。
    """

    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    #: **只删坐标、留地名**（定位漂了而地名没错时，用户要的是这一下）。「两个框都空了」本身
    #: 说不清是「删掉它」还是「没动它」，所以由界面明确说「原本有值、现在被清空了」——
    #: 「把位置整个清掉」不需要单独的开关，全清空就是那个意思。
    clear_coordinates: bool = False

    def names(self) -> tuple[str | None, str | None, str | None]:
        return (self.city, self.region, self.country)


@dataclass(frozen=True, slots=True)
class PlacePlan:
    """一次编辑的结果。"""

    #: 要写进文件的字段。**为空表示什么都没改**，调用方不该走到写盘那一步。
    assignments: tuple[Assignment, ...] = ()
    #: 写完之后照片里会有的地点（**写进文件的那一份**，不是推断的那一份）。
    city: str | None = None
    region: str | None = None
    country: str | None = None
    #: 写完之后照片里会有的坐标。
    coordinates: tuple[float, float] | None = None

    @property
    def label(self) -> str:
        return place_label(self.city, self.region, self.country)

    @property
    def is_empty(self) -> bool:
        return not self.assignments


def plan_place_edit(
    state: PlaceState,
    edit: PlaceEdit,
    *,
    resolver: PlaceResolver | None = None,
) -> PlacePlan:
    """把「用户填完的那一份」翻译成要写进文件的字段。

    **只写用户填的那几层，不再隐式跨层填充**（ADR 0039 决策 4）：跨层填充只发生在对话框的按钮上，
    按钮改的就是框本身。``resolver`` 仍被接受但这里**不用它**，保留是为了签名与调用点不必跟着改。
    :raises MetadataWriteError: 坐标不合法（超出范围、只填了一个、填成了 0,0）。
    """
    del resolver  # 跨层填充搬到了对话框的按钮上，这里不再需要反查（ADR 0039）
    names = edit.names()
    names_changed = names != state.names()
    if edit.clear_coordinates:
        return _clearing_coordinates(state, names, names_changed)
    coordinates = _coordinates(state, edit)
    assignments: list[Assignment] = []
    final_names = names
    final_coordinates = coordinates or state.coordinates()
    #: 地名要不要跟着写。**没改过就一个字都不写** —— 重写一遍同样的值在写回那一层会被
    #: 判成「没变」而跳过，但列进计划里会让人以为程序动过它。
    names_written = names_changed

    if coordinates is not None:
        assignments.extend(_coordinate_assignments(*coordinates))

    if names_written:
        assignments.extend(_place_assignments(*final_names))
    return PlacePlan(
        assignments=tuple(assignments),
        city=final_names[0],
        region=final_names[1],
        country=final_names[2],
        coordinates=final_coordinates,
    )


def _clearing_coordinates(
    state: PlaceState, names: tuple[str | None, str | None, str | None], names_changed: bool
) -> PlacePlan:
    """「把坐标删掉，但地名留着」——定位漂了、地名没错时用户真正想要的那一下。

    **海拔与定位时间一并清掉**，与 :func:`_coordinate_assignments` 同一份定义
    （:data:`~gallery.core.metadata.writeback.STALE_GPS_TAGS`）：只清经纬度会在文件里留下
    「有海拔、有 GPS 定位时间、但没有经纬度」的记录，读 EXIF 的软件看到都困惑。
    """
    assignments: list[Assignment] = [Assignment(tag, "") for tag in _all_coordinate_tags()]
    if names_changed:
        assignments.extend(_place_assignments(*names))
    return PlacePlan(
        assignments=tuple(assignments),
        city=names[0],
        region=names[1],
        country=names[2],
        coordinates=state.coordinates(),
    )


# ---- 坐标 ---------------------------------------------------------------------


def _coordinates(state: PlaceState, edit: PlaceEdit) -> tuple[float, float] | None:
    """用户填的坐标；没填、或与原来一样，就返回 ``None``（这一轮不动坐标）。"""
    if edit.latitude is None and edit.longitude is None:
        return None
    if edit.latitude is None or edit.longitude is None:
        raise MetadataWriteError(t("metadata.place_edit.pair_required"))
    latitude, longitude = float(edit.latitude), float(edit.longitude)
    if not -90.0 <= latitude <= 90.0:
        raise MetadataWriteError(t("metadata.place_edit.latitude_range", value=latitude))
    if not -180.0 <= longitude <= 180.0:
        raise MetadataWriteError(t("metadata.place_edit.longitude_range", value=longitude))
    if not is_real_coordinate(latitude, longitude):
        # 0,0 是几内亚湾里一个不存在的点（见 is_real_coordinate），十有八九是「我没定到 GPS，
        # 程序给我填了 0」的原样搬运。
        raise MetadataWriteError(t("metadata.place_edit.null_island"))
    current = state.coordinates()
    if current is not None and all(
        abs(a - b) <= _COORDINATE_EPSILON
        for a, b in zip(current, (latitude, longitude), strict=True)
    ):
        return None
    return (latitude, longitude)


def _all_coordinate_tags() -> tuple[str, ...]:
    """**改坐标**与**清空坐标**都要动的全部坐标标签。

    海拔与定位时间记的是「另一个位置」上的测量值：坐标在杭州、海拔是珠峰，留着一条 GPS 记录就
    自相矛盾（ADR 0031 决策 6）。**只有这一处定义** —— 两条路各写一份清单的代价已经付过一次。
    """
    return (*COORDINATE_TAGS, *STALE_GPS_TAGS)


def _coordinate_assignments(latitude: float, longitude: float) -> list[Assignment]:
    """坐标 → 四个标签，外加**把旧位置的海拔与定位时间清掉**。

    要动的全部标签见 :func:`_all_coordinate_tags` —— 与「清空坐标」共用那一份。
    四个标签与两个半球字面量从 :data:`~gallery.core.metadata.exif.COORDINATE_AXES`
    来：纬度与经度只差那几个字面量，写两遍就有一遍会漏。
    """
    assignments: list[Assignment] = []
    for axis, value in zip(COORDINATE_AXES, (latitude, longitude), strict=True):
        assignments.append(Assignment(axis.value_tag, f"{abs(value):.6f}"))
        hemisphere = axis.positive if value >= 0 else axis.negative
        assignments.append(Assignment(axis.ref_tag, hemisphere))
    assignments.extend(Assignment(tag, "") for tag in STALE_GPS_TAGS)
    return assignments


# ---- 地名 ---------------------------------------------------------------------


def _place_assignments(
    city: str | None, region: str | None, country: str | None
) -> list[Assignment]:
    """三层地名 → XMP 与 IPTC 各一份。**空的那一层 = 删掉那两个字段**（XMP 与 IPTC 都删）。

    标签对从 :data:`~gallery.core.metadata.exif.PLACE_FIELDS` 来 —— 与读取那一侧同一份，
    写出去的名字与读回来的名字不会各自漂。
    """
    assignments: list[Assignment] = []
    for field, value in zip(PLACE_FIELDS, (city, region, country), strict=True):
        assignments.append(Assignment(field.xmp_tag, value or ""))
        assignments.append(Assignment(field.iptc_tag, value or ""))
    return assignments


# ---- 两个方向的查 -------------------------------------------------------------


def _lookup_place(
    coordinates: tuple[float, float],
    resolver: PlaceResolver | None,
) -> tuple[list[Assignment], tuple[str | None, str | None, str | None] | None]:
    """坐标 → 地点（反查）。**查不到就什么都不加**，照片里原来的地名原样留着。

    保留而不是清空：该由用户判断是地名错了还是坐标错了，替他删掉只会让事情更难查。
    返回 ``(字段, 结论)``，结论为 ``None`` 就是「附近没有城市」。
    """
    if resolver is None:
        return [], None
    place = resolver.place_for(coordinates[0], coordinates[1])
    if place is None:
        return [], None  # 附近 300 公里没有城市：照片里原来的地名原样留着
    names = (place.city, place.region, place.country)
    return _place_assignments(*names), names


def _lookup_coordinates(
    names: tuple[str | None, str | None, str | None],
    resolver: PlaceResolver | None,
) -> tuple[list[Assignment], tuple[float, float] | None]:
    """地点 → 坐标（正查，ADR 0031）。

    **查不到就是查不到**：库里没有的名字（用户自己写的门牌、景区、小店）绝不能配一个坐标
    —— 那是一个看起来精确、实际错得离谱的值。返回 ``(字段, 坐标)``，坐标为 ``None`` 时
    调用方原样保留照片里原来的坐标。
    """
    city, region, country = names
    if resolver is None or not city:
        return [], None
    found = resolver.place_named(city, region=region, country=country)
    if not found:
        return [], None  # 库里没有这个名字：只写地名，坐标原样不动
    place = found[0]
    coordinates = (place.latitude, place.longitude)
    return _coordinate_assignments(*coordinates), coordinates


__all__ = [
    "PlaceEdit",
    "PlacePlan",
    "PlaceState",
    "plan_place_edit",
]
