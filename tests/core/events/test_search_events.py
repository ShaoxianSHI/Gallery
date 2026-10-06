"""按事件筛选（ADR 0043 决策 6）：``#事件类型`` / ``#事件`` / ``#目的地`` ……

事件那一族的条件走 ``EXISTS`` 子查询去读**挂在索引那条连接上**的事件库，所以它同时验了三件事：
字段能解析、生成的 SQL 能在真 schema 上跑、以及命中的照片确实是对的那几张。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.events.store import EventDraft
from gallery.core.library import Library
from gallery.core.library.search import SearchCondition, match_field, suggestions


@pytest.fixture
def library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True, exist_ok=True)
    for directory, names in (
        (root / "2026", ("京都-01.jpg", "京都-02.jpg")),
        (root / "2025", ("大阪-01.jpg",)),
        (root, ("山.jpg",)),
    ):
        for name in names:
            make_photo(name, directory=directory)
    return Library.open(root)


def _condition(text: str) -> SearchCondition:
    from gallery.core.library.search import make_condition, parse_input

    condition = make_condition(parse_input(text))
    assert condition is not None, text
    return condition


def _names(library: Library, text: str) -> list[str]:
    return sorted(ref.name for ref in library.search_photos([_condition(text)]).refs)


def _with_trips(library: Library) -> tuple[int, int]:
    """两个旅行：京都（两张照片、3280.5）与大阪（一张、900）。返回两个 id。"""
    kyoto = library.create_event(
        "trip",
        EventDraft(
            values={
                "title": "京都五日",
                "started_on": "2026-05-16",
                "destination": "京都、奈良",
                "cost": "3280.50",
            }
        ),
    ).event
    osaka = library.create_event(
        "trip", EventDraft(values={"title": "大阪一日", "destination": "大阪", "cost": "900"})
    ).event
    library.bind_photos(
        [library.root / "2026" / "京都-01.jpg", library.root / "2026" / "京都-02.jpg"], kyoto.id
    )
    library.bind_photos([library.root / "2025" / "大阪-01.jpg"], osaka.id)
    library.index_photos()
    return kyoto.id, osaka.id


def test_the_candidate_list_offers_the_event_fields() -> None:
    """字段表由事件类型定义生成 —— 加一类事件、给它加一个字段，这里就多一项。"""
    labels = [field.label for field in suggestions("")]

    assert "事件类型" in labels
    assert "事件" in labels
    assert "目的地" in labels
    assert "花费" in labels


def test_an_event_field_is_recognised_by_its_label_and_id() -> None:
    assert match_field("目的地").id == "event_destination"
    assert match_field("event_kind").label == "事件类型"
    assert match_field("没有这个字段") is None


def test_an_event_field_does_not_need_exiftool() -> None:
    """事件不来自照片 —— 所以没装 exiftool 时它照样出现在候选里。"""
    assert match_field("目的地").needs_metadata is False
    assert match_field("城市").needs_metadata is True


def test_filtering_by_kind_finds_the_photos_of_that_event(library: Library) -> None:
    _with_trips(library)

    assert _names(library, "#事件类型 旅行") == ["京都-01.jpg", "京都-02.jpg", "大阪-01.jpg"]
    assert _names(library, "#目的地 大阪") == ["大阪-01.jpg"]


def test_the_kind_field_takes_the_chinese_label_as_well_as_the_id(
    library: Library,
) -> None:
    """库里存的是 ``trip``，用户敲的是「旅行」—— 两种都得认，否则这一栏看着能用却永远筛不出。"""
    _with_trips(library)

    assert _names(library, "#事件类型 trip") == _names(library, "#事件类型 旅行")


def test_filtering_by_a_per_kind_field(library: Library) -> None:
    _with_trips(library)

    assert _names(library, "#目的地 奈良") == ["京都-01.jpg", "京都-02.jpg"]
    assert _names(library, "#目的地 名古屋") == []


def test_filtering_by_the_event_title(library: Library) -> None:
    _with_trips(library)

    assert _names(library, "#事件 京都五日") == ["京都-01.jpg", "京都-02.jpg"]


def test_a_numeric_event_field_is_compared_not_matched(library: Library) -> None:
    """花费存的是 ``REAL``，所以 ``#花费 >3000`` 要按数字比而不是按字符串。"""
    _with_trips(library)

    assert _names(library, "#花费 >3000") == ["京都-01.jpg", "京都-02.jpg"]
    assert _names(library, "#花费 <1000") == ["大阪-01.jpg"]


def test_a_photo_in_no_event_is_not_found(library: Library) -> None:
    """没绑定过的事件与绑定过的事件一样都算「这一栏没有」，不报错。"""
    _with_trips(library)

    assert "山.jpg" not in _names(library, "#事件类型 旅行")


def test_event_conditions_narrow_each_other(library: Library) -> None:
    """两个事件条件是「与」的关系 —— 于是「2026 那次旅行里去了奈良的照片」筛得出来。"""
    _with_trips(library)

    both = library.search_photos([_condition("#事件类型 旅行"), _condition("#目的地 奈良")])

    assert sorted(ref.name for ref in both.refs) == ["京都-01.jpg", "京都-02.jpg"]


def test_a_photo_in_two_events_is_listed_once(library: Library) -> None:
    """**走 EXISTS 而不是 ``JOIN`` 的理由**：一张照片同时属于两次旅行，而 JOIN 会让它出现两次。"""
    first, second = _with_trips(library)
    shared = library.root / "2026" / "京都-02.jpg"
    library.bind_photos([shared], second)

    assert _names(library, "#事件类型 旅行").count("京都-02.jpg") == 1


def test_the_filtering_query_survives_the_real_schema(library: Library) -> None:
    """走的是 ``build_match_query`` 那一条（筛选当前视图）—— 它有自己的一行 SELECT，
    子查询照旧要能在真 schema 上跑。"""

    _with_trips(library)

    paths = library.matching_paths([_condition("#目的地 京都")])

    assert sorted(path.name for path in paths) == ["京都-01.jpg", "京都-02.jpg"]


def test_a_kind_nobody_has_is_simply_empty(library: Library) -> None:
    """还没加「演出」这一类时按它筛只是没有结果，不是报错。"""
    _with_trips(library)

    assert _names(library, "#事件类型 演出") == []
