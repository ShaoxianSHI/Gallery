"""用户在编辑器里改的那份位置，翻译成要写进文件的字段（ADR 0031）。

**这一层只做翻译，不碰文件**：输入是「照片此刻的位置」与「用户填完的那一份」，输出是
一组 :class:`~gallery.core.metadata.writeback.Assignment`。真正写盘的是
:func:`gallery.core.metadata.writeback.write_metadata`，它记账、校验、回滚、可撤销。

两条方向在这里合流：

- **坐标 → 地点**：按新坐标反查一次城市（``place_for``），把结论一并写进去，于是照片的
  坐标与地点永远说同一句话；
- **地点 → 坐标**：按新地名正查一次（``place_named``，ADR 0031），查得到就把那个地点的
  坐标写进去，查不到就**只写地名、坐标原样不动** —— 库里没有的名字绝不配一个坐标
  （那是拿「市一级的位置」冒充「这张照片在哪」）。

**两个方向撞在一起时，用户填的说了算**：两个方向都在同一个对话框里，就不去反查覆盖
用户填的地名 —— 否则用户改完坐标、顺手把城市名也调了一下，回来却发现自己的地名被
反查结果盖掉了。那一刻只说一句「它们之间的对应关系是你定的」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from gallery.core.errors import MetadataWriteError
from gallery.core.metadata.writeback import (
    COORDINATE_TAGS,
    PLACE_TAGS,
    STALE_GPS_TAGS,
    Assignment,
)
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import is_real_coordinate

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver

#: 判「坐标有没有变」的容差（度）。约 1.1 米 —— 比这更小的改动不值得改一次文件。
_COORDINATE_EPSILON: Final = 1e-5

#: 反查出来的地点离得多远才值得提醒用户一句「这只是最近的城市」。
_DISTANCE_HINT_KM: Final = 1.0


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

    编辑器是三个输入框加一对坐标框，**打开时就把照片此刻的值填进去**，用户改完直接保存 ——
    那是访达「显示简介」的用法。于是这里收到的是「最终应该是什么样」，而不是一个补丁：
    **空串表示那一层要清掉**（这是与「没动它」不同的一件事，正因为如此才不该让界面传
    「改了哪些」过来 —— 那种形状没法表达「把城市清空」）。
    """

    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    #: 改了地名时，要不要顺手把这个地点的坐标也写进去（见 :func:`plan_place_edit`）。
    update_coordinates: bool = True
    #: **只删坐标、留地名**。
    #:
    #: 「两个框都空了」本身说不清是「删掉它」还是「没动它」——后者是绝大多数情况（用户只是
    #: 改了个地名）。所以由界面明确说「这两个框原本有值、现在被清空了」，core 才当真去删。
    #: 「连地名一起清」有单独的 :attr:`clear`，不必用这个。
    clear_coordinates: bool = False
    #: 清空位置（坐标与三层地名一起清）。它盖过上面所有字段。
    clear: bool = False

    def names(self) -> tuple[str | None, str | None, str | None]:
        return (self.city, self.region, self.country)


@dataclass(frozen=True, slots=True)
class PlacePlan:
    """一次编辑的落定结果。"""

    #: 要写进文件的字段。**为空表示什么都没改**，调用方不该走到写盘那一步。
    assignments: tuple[Assignment, ...] = ()
    #: 写完之后照片里会有的地点（**写进文件的那一份**，不是推断的那一份）。
    city: str | None = None
    region: str | None = None
    country: str | None = None
    #: 写完之后照片里会有的坐标。
    coordinates: tuple[float, float] | None = None
    #: 要对用户说的话。每一条都是**已经发生的事**，不是建议 —— 横幅照抄即可。
    notes: tuple[str, ...] = ()

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

    :raises MetadataWriteError: 坐标不合法（超出范围、只填了一个、填成了 0,0）。
    """
    if edit.clear:
        return PlacePlan(
            assignments=tuple(Assignment(tag, "") for tag in (*COORDINATE_TAGS, *PLACE_TAGS)),
            notes=("坐标与地名都已从照片里清除。",),
        )

    names = edit.names()
    names_changed = names != state.names()
    if edit.clear_coordinates:
        return _clearing_coordinates(state, names, names_changed)
    coordinates = _coordinates(state, edit)
    notes: list[str] = []
    assignments: list[Assignment] = []
    final_names = names
    final_coordinates = coordinates or state.coordinates()
    #: 地名要不要跟着写。**没改过、也没反查出新结论时一个字都不写** —— 重写一遍同样的
    #: 值在写回那一层会被判成「没变」而跳过，但把它列进计划里会让人以为程序动过它。
    names_written = names_changed

    if coordinates is not None:
        assignments.extend(_coordinate_assignments(*coordinates))
        if not names_changed:
            looked_up, found_names = _lookup_place(coordinates, resolver, notes)
            if found_names is not None:
                assignments.extend(looked_up)
                final_names = found_names
                names_written = True
        else:
            # 两个方向都在同一个对话框里改了：填了的说了算，不去反查覆盖它。
            notes.append("地名与坐标都是你填的，它们之间的对应关系 Gallery 没有替你调整。")
    elif edit.update_coordinates and names_changed:
        found, found_coordinates = _lookup_coordinates(names, resolver, notes)
        assignments.extend(found)
        # 正查出来的坐标**优先于**照片里原来那个：用户明确要求「按这个地点改坐标」，
        # 原来的坐标在这条要求里就是要被替换掉的那一个。
        final_coordinates = found_coordinates or final_coordinates

    if names_written:
        assignments.extend(_place_assignments(*final_names))
    return PlacePlan(
        assignments=tuple(assignments),
        city=final_names[0],
        region=final_names[1],
        country=final_names[2],
        coordinates=final_coordinates,
        notes=tuple(notes),
    )


def _clearing_coordinates(
    state: PlaceState, names: tuple[str | None, str | None, str | None], names_changed: bool
) -> PlacePlan:
    """「把坐标删掉，但地名留着」——定位漂了、地名没错时用户真正想要的那一下。"""
    assignments: list[Assignment] = [Assignment(tag, "") for tag in COORDINATE_TAGS]
    notes = ["坐标已从照片里清除，地点保留。"]
    if names_changed:
        assignments.extend(_place_assignments(*names))
    return PlacePlan(
        assignments=tuple(assignments),
        city=names[0],
        region=names[1],
        country=names[2],
        coordinates=state.coordinates(),
        notes=tuple(notes),
    )


# ---- 坐标 ---------------------------------------------------------------------


def _coordinates(state: PlaceState, edit: PlaceEdit) -> tuple[float, float] | None:
    """用户填的坐标；没填、或与原来一样，就返回 ``None``（这一轮不动坐标）。"""
    if edit.latitude is None and edit.longitude is None:
        return None
    if edit.latitude is None or edit.longitude is None:
        raise MetadataWriteError("经度和纬度要一起填：只填一个，说不出一个位置。")
    latitude, longitude = float(edit.latitude), float(edit.longitude)
    if not -90.0 <= latitude <= 90.0:
        raise MetadataWriteError(f"纬度要在 -90 到 90 之间，写的是 {latitude}。")
    if not -180.0 <= longitude <= 180.0:
        raise MetadataWriteError(f"经度要在 -180 到 180 之间，写的是 {longitude}。")
    if not is_real_coordinate(latitude, longitude):
        # 0,0 是几内亚湾里一个不存在的点（见 is_real_coordinate）。用户真的要去那儿也是
        # 一个合法意图 —— 但十有八九是「我没定到 GPS，程序给我填了 0」的原样搬运。
        raise MetadataWriteError("经纬度都是 0 —— 那是几内亚湾里的一个点，世上并不存在。")
    current = state.coordinates()
    if current is not None and all(
        abs(a - b) <= _COORDINATE_EPSILON
        for a, b in zip(current, (latitude, longitude), strict=True)
    ):
        return None
    return (latitude, longitude)


def _coordinate_assignments(latitude: float, longitude: float) -> list[Assignment]:
    """坐标 → 四个标签，外加**把旧位置的海拔与定位时间清掉**。

    那两个字段记的是「另一个位置」上的测量值：留着它们，一条 GPS 记录内部就自相矛盾了
    （坐标在杭州、海拔是珠峰的）。清掉比留着好，而清掉这件事必须说给用户听。
    """
    return [
        Assignment("EXIF:GPSLatitude", f"{abs(latitude):.6f}"),
        Assignment("EXIF:GPSLatitudeRef", "N" if latitude >= 0 else "S"),
        Assignment("EXIF:GPSLongitude", f"{abs(longitude):.6f}"),
        Assignment("EXIF:GPSLongitudeRef", "E" if longitude >= 0 else "W"),
        *(Assignment(tag, "") for tag in STALE_GPS_TAGS),
    ]


# ---- 地名 ---------------------------------------------------------------------


def _place_assignments(
    city: str | None, region: str | None, country: str | None
) -> list[Assignment]:
    """三层地名 → XMP 与 IPTC 各一份。**空的那一层 = 删掉那两个字段**（XMP 与 IPTC 都删）。"""
    pairs = (
        ("XMP:City", "IPTC:City", city),
        ("XMP:State", "IPTC:Province-State", region),
        ("XMP:Country", "IPTC:Country-PrimaryLocationName", country),
    )
    result: list[Assignment] = []
    for xmp_tag, iptc_tag, value in pairs:
        result.append(Assignment(xmp_tag, value or ""))
        result.append(Assignment(iptc_tag, value or ""))
    return result


# ---- 两个方向的查 -------------------------------------------------------------


def _lookup_place(
    coordinates: tuple[float, float],
    resolver: PlaceResolver | None,
    notes: list[str],
) -> tuple[list[Assignment], tuple[str | None, str | None, str | None] | None]:
    """坐标 → 地点（反查）。**查不到就什么都不加**，照片里原来的地名原样留着。

    保留而不是清空：那张照片本来就写着「南京」，用户只是把坐标挪到了别处 —— 该由用户
    判断是地名错了还是坐标错了，Gallery 替他删掉一个字段只会让事情更难查。

    返回 ``(字段, 结论)``，结论为 ``None`` 就是「附近没有城市」——调用方据此不动地名。
    """
    if resolver is None:
        return [], None
    place = resolver.place_for(coordinates[0], coordinates[1])
    if place is None:
        notes.append("新坐标附近 300 公里没有城市记录，照片里原来的地名没有改动。")
        return [], None
    names = (place.city, place.region, place.country)
    notes.append(f"地点已按新坐标更新为「{place_label(*names)}」。")
    if place.distance_km is not None and place.distance_km >= _DISTANCE_HINT_KM:
        notes.append(f"这是离照片位置 {_round_km(place.distance_km)} 公里的最近的城市。")
    return _place_assignments(*names), names


def _lookup_coordinates(
    names: tuple[str | None, str | None, str | None],
    resolver: PlaceResolver | None,
    notes: list[str],
) -> tuple[list[Assignment], tuple[float, float] | None]:
    """地点 → 坐标（正查，ADR 0031）。

    **查不到就是查不到**：库里没有的名字（用户自己写的门牌、景区、小店）绝不能配一个
    坐标 —— 那是一个看起来精确、实际错得离谱的值，比留着没有坐标糟糕得多。

    返回 ``(字段, 坐标)``，坐标为 ``None`` 就是「库里没有这个名字」，调用方据此
    **原样保留照片里原来的坐标**，并把原因说给用户。
    """
    city, region, country = names
    if resolver is None or not city:
        return [], None
    found = resolver.place_named(city, region=region, country=country)
    if not found:
        notes.append(_not_in_database(city))
        return [], None
    place = found[0]
    notes.append(
        f"坐标已按「{place_label(place.city, place.region, place.country)}」更新 —— "
        "那是这座城市的位置，不是照片的具体位置。"
    )
    coordinates = (place.latitude, place.longitude)
    return _coordinate_assignments(*coordinates), coordinates


def _not_in_database(name: str) -> str:
    """城市库里查不到时的说明。

    **顺带说清境外用当地写法**（ADR 0011）：输「京都」查不到，是因为库里那一带写的是
    ``Kyoto``。不说这一句，用户只会以为这个功能坏了。
    """
    return (
        f"城市库里没有「{name}」，坐标保持原样。境外地点按当地写法收录"
        "（例如京都写作 Kyoto），也可以直接填经纬度。"
    )


def _round_km(value: float) -> str:
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


__all__ = [
    "PlaceEdit",
    "PlacePlan",
    "PlaceState",
    "plan_place_edit",
]
