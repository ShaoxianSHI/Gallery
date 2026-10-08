"""组卡片的封面：指定的那张，没有就退回组内第一张（ADR 0048 决策 7）。

这一层只有一件事值得单独测：**解析不出指定的那张时不许钉第一张顶上**。
钉上去的话「这一组有封面」与「没有」在数据上分不开，而界面要靠这个区别决定
「清除封面」那一行摆不摆 —— 分不开的症状是那一行永远摆着（按了没反应）或者永远不摆。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.events.store import EventRef
from gallery.core.library.groups import SortOrder, build_groups
from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.core.metadata.facets import FACET_EVENT, FACETS_BY_ID, Facet


def _photo(name: str) -> PhotoRef:
    return PhotoRef(path=Path(f"/库/{name}"), size=1, mtime_ns=0)


@pytest.fixture
def event_facet() -> Facet:
    return FACETS_BY_ID[FACET_EVENT]


def _refs_for(paths: list[Path], cover: Path | None) -> dict[Path, tuple[EventRef, ...]]:
    """每张照片都属于同一个事件（id 1，2026-05-16 起），只有一张带封面。"""
    started = "2026-05-16"
    return {
        path: (
            EventRef(
                id=1,
                kind="trip",
                title="京都五日",
                started_on=started,
                cover_path=str(cover) if cover == path else None,
            ),
        )
        for path in paths
    }


def _build(event_facet: Facet, photos: list[PhotoRef], cover: Path | None) -> list[GroupRef]:
    return build_groups(
        photos,
        facet=event_facet,
        root=Path("/库"),
        order=SortOrder(facet=None),
        event_values=_refs_for([ref.path for ref in photos], cover),
    )


def test_the_chosen_photo_is_the_one_the_card_draws(event_facet: Facet) -> None:
    first, second = _photo("a.jpg"), _photo("b.jpg")

    groups = _build(event_facet, [first, second], second.path)

    assert groups[0].cover is second
    assert groups[0].pinned_cover is second


def test_a_group_without_a_cover_falls_back_to_the_first_photo(event_facet: Facet) -> None:
    """没设封面就是第一张 —— 与加封面之前的行为一模一样。"""
    first, second = _photo("a.jpg"), _photo("b.jpg")

    groups = _build(event_facet, [first, second], None)

    assert groups[0].cover is first
    assert groups[0].pinned_cover is None


def test_a_cover_outside_the_group_is_not_pinned(event_facet: Facet) -> None:
    """指定的照片不在这组里（照片被解绑了、或者这一层压根没包含它）时**不钉**。

    症状会是「这一组明明设过封面却画了另一张」，而界面上「清除封面」那一行还摆着 ——
    用户按了没反应。退回第一张是对的（卡片永远有一张封面），只是别假装它有封面。
    """
    photos = [_photo("a.jpg"), _photo("b.jpg")]

    groups = _build(event_facet, photos, Path("/库/别处.jpg"))

    assert groups[0].pinned_cover is None
    assert groups[0].cover is photos[0]


def test_one_photos_cover_is_claimed_only_by_the_group_it_belongs_to(event_facet: Facet) -> None:
    """一张照片属于两个事件时，两份封面各归各的组 —— 认领条件是**组键对得上**。

    判据是 :attr:`EventRef.group_key` 与组键逐字相等，于是「组键 = 日期#事件 id」
    这条不变式（ADR 0043 决策 7）只有一处说了算，两边不会各自漂。
    """
    photos = [_photo("a.jpg"), _photo("b.jpg")]
    first, second = "2026-05-16", "2026-07-01"
    values = {
        photos[0].path: (
            EventRef(id=1, kind="trip", title="京都五日", started_on=first, cover_path=None),
            EventRef(
                id=2,
                kind="show",
                title="岚山演出",
                started_on=second,
                cover_path=str(photos[0].path),
            ),
        ),
        photos[1].path: (),
    }

    groups = build_groups(
        photos,
        facet=event_facet,
        root=Path("/库"),
        order=SortOrder(facet=None),
        event_values=values,
    )
    by_key = {group.key: group for group in groups}

    # 封面属于第二个事件（岚山演出），所以那一组钉住它，第一组退回第一张。
    assert by_key[f"{second}#2"].pinned_cover is photos[0]
    assert by_key[f"{first}#1"].pinned_cover is None


def test_a_group_with_no_photos_has_no_cover_at_all() -> None:
    """一张都没有的组连第一张都没有 —— ``cover`` 是 ``None``，界面画文件夹图标
    （与相册空卡片同一处置）。"""
    empty = GroupRef(facet=FACET_EVENT, key="2026-05-16#1", label="京都五日", photos=())

    assert empty.cover is None


def test_pinning_does_not_change_the_group_key(event_facet: Facet) -> None:
    """组键是「进入 / 退出这一组」与排序的依据，钉封面不许动它 ——
    否则换一次封面就把用户从组里踢出去了。"""
    photos = [_photo("a.jpg"), _photo("b.jpg")]

    before = _build(event_facet, photos, None)
    after = _build(event_facet, photos, photos[1].path)

    assert [group.key for group in before] == [group.key for group in after]
