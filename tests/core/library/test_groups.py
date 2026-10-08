"""``gallery.core.library.groups`` 的测试。

这里定的是「用户看到什么顺序」，所以测的都是**看得见的性质**：

- 时间分组新 → 旧，其余按名字升序；没值的组永远在最后。
- 排序里没值的一律沉底，正序逆序都一样（不然一堆没写拍摄时间的照片会霸占一整屏）。
- 同一秒拍到的照片按文件名兜底，顺序稳定可预期。
- **「逆序」偏离字段的自然方向，不是字面的 ``reverse``**（ADR 0049）：照片、组卡片、
  相册卡片三者因此永远朝着同一个方向。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gallery.core.library.groups import SortOrder, build_groups, flatten_groups, sort_photos
from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.core.metadata.facets import (
    FACET_CAMERA,
    FACET_CAPTURED,
    FACET_FOLDER,
    FACET_ISO,
    FACET_KEYWORD,
    FACET_MONTH,
    FACET_NAME,
    FACET_SIZE,
    Facet,
    PhotoFacets,
    group_facet,
    sort_facet,
)


def _ref(root: Path, name: str, size: int = 10, mtime: int = 100) -> PhotoRef:
    return PhotoRef(path=root / name, size=size, mtime_ns=mtime)


def _by_name(refs: list[PhotoRef]) -> list[str]:
    return [ref.name for ref in refs]


# ---- 排序 ----------------------------------------------------------------------------


def test_default_order_is_by_name(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "c.jpg"), _ref(tmp_path, "A.jpg"), _ref(tmp_path, "b.jpg")]

    ordered = sort_photos(photos, order=SortOrder(facet=None), root=tmp_path)

    assert _by_name(ordered) == ["A.jpg", "b.jpg", "c.jpg"]


def test_default_order_does_not_touch_the_input(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "b.jpg"), _ref(tmp_path, "a.jpg")]

    sort_photos(photos, order=SortOrder(facet=None), root=tmp_path)

    assert _by_name(photos) == ["b.jpg", "a.jpg"]


def test_sort_by_size_both_directions(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "a.jpg", size=30), _ref(tmp_path, "b.jpg", size=10)]
    order = sort_facet(FACET_SIZE)
    assert order is not None

    assert _by_name(sort_photos(photos, order=SortOrder(order), root=tmp_path)) == [
        "b.jpg",
        "a.jpg",
    ]
    assert _by_name(
        sort_photos(photos, order=SortOrder(order, descending=True), root=tmp_path)
    ) == ["a.jpg", "b.jpg"]


def test_missing_values_sink_to_the_end_in_both_directions(tmp_path: Path) -> None:
    """没写 ISO 的照片不该在「ISO 降序」里排到最前面。"""
    photos = [
        _ref(tmp_path, "没有.jpg"),
        _ref(tmp_path, "高.jpg"),
        _ref(tmp_path, "低.jpg"),
    ]
    values = {
        photos[1].path: PhotoFacets(iso=3200),
        photos[2].path: PhotoFacets(iso=100),
    }
    order = sort_facet(FACET_ISO)
    assert order is not None

    assert _by_name(sort_photos(photos, order=SortOrder(order), root=tmp_path, values=values)) == [
        "低.jpg",
        "高.jpg",
        "没有.jpg",
    ]
    assert _by_name(
        sort_photos(photos, order=SortOrder(order, descending=True), root=tmp_path, values=values)
    ) == ["高.jpg", "低.jpg", "没有.jpg"]


def test_equal_values_fall_back_to_file_name(tmp_path: Path) -> None:
    """同值时按文件名兜底 —— 但**方向仍跟着字段的自然方向**：拍摄时间是新→旧，
    于是同样两个值下面文件名是降序的。别把它当成「兜底键决定方向」。"""
    photos = [_ref(tmp_path, "b.jpg"), _ref(tmp_path, "a.jpg")]
    values = {ref.path: PhotoFacets(captured_at=1000.0) for ref in photos}
    order = sort_facet(FACET_CAPTURED)
    assert order is not None

    ordered = sort_photos(photos, order=SortOrder(order), root=tmp_path, values=values)

    assert _by_name(ordered) == ["b.jpg", "a.jpg"]
    assert _by_name(
        sort_photos(photos, order=SortOrder(order, descending=True), root=tmp_path, values=values)
    ) == ["a.jpg", "b.jpg"]


def test_sort_by_name_works_without_any_metadata(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "b.jpg", mtime=200), _ref(tmp_path, "a.jpg", mtime=100)]
    order = sort_facet(FACET_NAME)
    assert order is not None

    assert _by_name(sort_photos(photos, order=SortOrder(order), root=tmp_path)) == [
        "a.jpg",
        "b.jpg",
    ]


# ---- 分组 ----------------------------------------------------------------------------


def test_group_cards_follow_the_reverse_toggle(tmp_path: Path) -> None:
    """「逆序」要**真的把组卡片翻过来**（ADR 0049）。

    它原先只由字段自己声明，于是这一档对组卡片完全无效：同一层里照片翻了、组卡片没翻，
    读起来像两样东西没对齐。时间类与名称类各测一只 —— 前者的自然方向本就是降序，
    后者不是，两者的翻转方向因此相反。
    """
    months = [_ref(tmp_path, f"{月份}.jpg") for 月份 in ("三月", "十二月")]
    values = {
        months[0].path: PhotoFacets(year=2024, month=3),
        months[1].path: PhotoFacets(year=2024, month=12),
    }
    by_month = group_facet(FACET_MONTH)
    assert by_month is not None
    # 名称类拿**相机**来测：按文件夹分组时同一个根目录下的照片全落进同一组，
    # 造不出两个可比较的组键（那是 ``FACET_FOLDER`` 的定义，不是排序的问题）。
    brand_of = ("富士", "佳能")
    cameras = [_ref(tmp_path, f"{name}.jpg") for name in brand_of]
    camera_values = {
        ref.path: PhotoFacets(camera=name) for ref, name in zip(cameras, brand_of, strict=True)
    }
    by_camera = group_facet(FACET_CAMERA)
    assert by_camera is not None

    def labels(photos: list[PhotoRef], facet: Facet, values: dict[Path, PhotoFacets]) -> list[str]:
        forward = build_groups(photos, facet=facet, root=tmp_path, values=values)
        backward = build_groups(
            photos,
            facet=facet,
            root=tmp_path,
            values=values,
            order=SortOrder(facet=None, descending=True),
        )
        assert [group.label for group in forward] == [
            group.label for group in reversed(backward)
        ], "组卡片的顺序没跟着「逆序」翻"
        return [group.label for group in forward]

    # 时间类：自然方向新→旧，所以默认是十二月、三月。
    assert labels(months, by_month, values) == ["2024 年 12 月", "2024 年 3 月"]
    # 名称类：自然方向是升序，所以默认是佳能、富士。
    assert labels(cameras, by_camera, camera_values) == ["佳能", "富士"]


def test_group_by_month_is_newest_first(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, f"{name}.jpg") for name in ("三月", "十二月", "一月")]
    values = {
        photos[0].path: PhotoFacets(year=2024, month=3),
        photos[1].path: PhotoFacets(year=2024, month=12),
        photos[2].path: PhotoFacets(year=2023, month=1),
    }
    facet = group_facet(FACET_MONTH)
    assert facet is not None

    groups = build_groups(photos, facet=facet, root=tmp_path, values=values)

    assert [group.label for group in groups] == ["2024 年 12 月", "2024 年 3 月", "2023 年 1 月"]
    assert groups[0].photos[0].name == "十二月.jpg"


def test_photos_without_value_land_in_one_last_group(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "有.jpg"), _ref(tmp_path, "没有.jpg")]
    values = {photos[0].path: PhotoFacets(year=2024, month=5)}
    facet = group_facet(FACET_MONTH)
    assert facet is not None

    groups = build_groups(photos, facet=facet, root=tmp_path, values=values)

    assert [group.label for group in groups] == ["2024 年 5 月", "时间未知"]
    assert groups[1].count == 1


def test_the_last_group_stays_last_when_the_direction_flips(tmp_path: Path) -> None:
    """「没值的那一组沉底」在**两个方向下都得成立**。

    它原先是靠把日期键取负实现的（``-0.0`` 大于任何负日期），而取负只在新→旧时成立 ——
    一翻方向那一组就窜到最前面去了。这条钉住的是「沉底」由显式摘出来实现，不是靠键的大小。
    """
    photos = [_ref(tmp_path, f"{月份}.jpg") for 月份 in ("一月", "十二月")]
    values = {
        photos[0].path: PhotoFacets(year=2024, month=1),
        photos[1].path: PhotoFacets(year=2024, month=12),
    }
    undated = _ref(tmp_path, "没有时间.jpg")
    photos.append(undated)
    facet = group_facet(FACET_MONTH)
    assert facet is not None

    for descending in (False, True):
        groups = build_groups(
            photos,
            facet=facet,
            root=tmp_path,
            values=values,
            order=SortOrder(facet=None, descending=descending),
        )

        assert [group.label for group in groups][-1] == "时间未知", (
            f"勾了逆序（{descending=}）时「时间未知」跑到了前面"
        )


def test_group_by_keyword_can_put_one_photo_in_several_groups(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "a.jpg"), _ref(tmp_path, "b.jpg")]
    values = {
        photos[0].path: PhotoFacets(keywords=("海边", "夜景")),
        photos[1].path: PhotoFacets(keywords=("海边",)),
    }
    facet = group_facet(FACET_KEYWORD)
    assert facet is not None

    groups = build_groups(photos, facet=facet, root=tmp_path, values=values)

    assert [(group.label, group.count) for group in groups] == [("夜景", 1), ("海边", 2)]
    assert len(flatten_groups(groups)) == 2  # 摊平后按路径去重


def test_group_members_are_sorted_by_current_order(tmp_path: Path) -> None:
    """组内顺序跟着「排序」走 —— 这正是分组的用处。"""
    photos = [_ref(tmp_path, "b.jpg", size=10), _ref(tmp_path, "a.jpg", size=30)]
    facet = group_facet(FACET_FOLDER)
    order = sort_facet(FACET_SIZE)
    assert facet is not None and order is not None

    groups = build_groups(
        photos, facet=facet, root=tmp_path, order=SortOrder(order, descending=True)
    )

    assert _by_name(groups[0].photos) == ["a.jpg", "b.jpg"]


def test_group_cards_know_their_cover_and_count(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "a.jpg"), _ref(tmp_path, "b.jpg")]
    facet = group_facet(FACET_FOLDER)
    assert facet is not None

    group = build_groups(photos, facet=facet, root=tmp_path)[0]

    assert isinstance(group, GroupRef)
    assert group.count == 2
    assert group.cover == group.photos[0]
    assert group.name == "（根目录）"


def test_group_by_folder_splits_subdirectories(tmp_path: Path) -> None:
    photos = [_ref(tmp_path, "照片/a.jpg"), _ref(tmp_path, "b.jpg")]
    facet = group_facet(FACET_FOLDER)
    assert facet is not None

    groups = build_groups(photos, facet=facet, root=tmp_path)

    assert [(group.label, group.count) for group in groups] == [("照片", 1), ("（根目录）", 1)]


def test_grouping_by_a_non_groupable_field_is_a_programming_error(tmp_path: Path) -> None:
    """排序字段不一定能分组（关键词就没有顺序可言）：那是代码缺陷，要炸就炸在这里。"""
    facet = sort_facet(FACET_CAPTURED)
    assert facet is not None and facet.group is None

    with pytest.raises(ValueError, match="不能用来分组"):
        build_groups([_ref(tmp_path, "a.jpg")], facet=facet, root=tmp_path)


def test_empty_input_produces_nothing(tmp_path: Path) -> None:
    facet = group_facet(FACET_FOLDER)
    assert facet is not None

    assert build_groups([], facet=facet, root=tmp_path) == []


def test_the_sort_value_is_computed_once_per_photo(tmp_path: Path) -> None:
    """**每张照片的排序值只算一次。**

    ``facet.sort`` 要组装 ``FacetInput``、查映射、取字段（拍摄时间还要
    ``parse_capture_time``）。原本分区循环问一遍「有没有值」、排序的 key 再问一遍
    —— 五万张的库每次重排就多付五万遍，而重排在换排序字段、监听来了一批新照片时
    都会发生。
    """
    photos = [
        PhotoRef(path=tmp_path / f"{index:04d}.jpg", size=1, mtime_ns=index) for index in range(50)
    ]
    values = {
        photo.path: PhotoFacets(captured_at=datetime(2024, 1, 1) + timedelta(days=index))
        for index, photo in enumerate(photos)
    }
    calls = {"n": 0}

    class Counted(SortOrder):
        def value(
            self,
            ref: PhotoRef,
            root: Path,
            values: Mapping[Path, PhotoFacets],  # noqa: ARG002 - 只是转发
        ) -> object:
            calls["n"] += 1
            return super().value(ref, root, values)

    order = Counted(facet=sort_facet(FACET_CAPTURED), descending=False)

    sort_photos(photos, order=order, root=tmp_path, values=values)

    assert calls["n"] == len(photos), f"每张该算一次，实际算了 {calls['n']} 次"
