"""事件类型与字段的定义。"""

from __future__ import annotations

import pytest

from gallery.core.events import kinds


def test_every_declared_field_has_a_column_and_a_label() -> None:
    """面板与建表语句都由这份定义生成，所以每个字段都得说得出自己的列名与中文名。

    **字段名里不许有空格**：``#旅行·目的地 京都`` 那种输入是拿标签名去敲的，有空格就永远认不回来
    （与 ``core.library.search`` 里省/州那一项同一条理由）。
    """
    for kind in kinds.EVENT_KINDS:
        for field in kinds.fields_of(kind):
            assert field.column and field.column.isidentifier(), field
            assert field.label and " " not in field.label, field


def test_the_field_ids_of_one_kind_are_unique() -> None:
    """两类事件可以有同名的列，但**同一类里**不能有两个同名的字段。"""
    for kind in kinds.EVENT_KINDS:
        ids = [field.id for field in kinds.fields_of(kind)]
        assert len(ids) == len(set(ids)), kind.id


def test_the_common_fields_come_first() -> None:
    """公共列在前：面板第一格永远是名称，用户不必先滚过一堆类型特有的东西。"""
    fields = kinds.fields_of(kinds.TRIP)

    assert fields[0].id == "title"
    assert kinds.COMMON_FIELDS[0].column == "title"


def test_the_cover_is_a_common_field_and_no_kind_repeats_it() -> None:
    """封面是**公共**字段，而它不属于任何一类（ADR 0048 决策 2）。

    两半都要断：进 ``COMMON_FIELDS`` 才有一份列清单（建表、journal、撤销、恢复全由它生成），
    而它一旦混进某一类的 ``fields``，那一类的扩展表就会多出一列，而面板会多画一个输入框 ——
    用户可以在那里手敲一个路径，而敲错的路径画不出图。
    """
    assert kinds.COVER in kinds.COMMON_FIELDS
    assert kinds.COVER.control == "cover"
    for kind in kinds.EVENT_KINDS:
        assert kinds.COVER not in kind.fields
        assert not {f.column for f in kind.fields} & {kinds.COVER.column}


def test_the_cover_is_not_a_search_field() -> None:
    """**候选列表里不许出现「封面」** —— 它的值是一条绝对路径，没有人会敲（ADR 0048 决策 8）。

    今天它进不去，是因为 ``search._event_fields`` 只把**每类独有的字段**生成成搜索字段，
    公共那两条是手写的（事件类型 / 事件）。将来有人把公共字段也生成一遍，这条就会红 ——
    而症状是候选里多一项没人会用的条件，不是报错。
    """
    from gallery.core.library.search import SEARCH_FIELDS

    labels = {field.label for field in SEARCH_FIELDS}

    assert not any("封面" in label for label in labels)


def test_a_trip_keeps_its_own_fields_in_its_own_table() -> None:
    """类型独有的字段不混进公共列 —— 它们住在自己那张表里，于是公共问题不用 UNION。"""
    common_columns = {field.column for field in kinds.COMMON_FIELDS}
    trip_columns = {field.column for field in kinds.TRIP.fields}

    assert not common_columns & trip_columns
    assert {"destinations", "companions", "transport", "lodging", "cost", "rating"} == trip_columns


def test_a_show_keeps_its_own_fields_in_its_own_table() -> None:
    """演出这一类独占一张表，列名与公共列一个都不撞（``rating`` 与旅行同名但在两张表里）。"""
    common_columns = {field.column for field in kinds.COMMON_FIELDS}
    show_columns = {field.column for field in kinds.SHOW.fields}

    assert not common_columns & show_columns
    assert {
        "artist",
        "guests",
        "program",
        "start_time",
        "city",
        "venue",
        "ticket_price",
        "paid",
        "seat",
        "rating",
        "show_type",
        "channel",
        "thoughts",
    } == show_columns


def test_a_show_takes_its_place_next_to_the_trip() -> None:
    """菜单顺序即 :data:`EVENT_KINDS` 的顺序：旅行在前、演出其后、影片在最后。"""
    assert kinds.EVENT_KINDS == (kinds.TRIP, kinds.SHOW, kinds.MOVIE)


def test_the_movie_kind_lists_its_own_columns() -> None:
    """「影片」这一类独有的字段（ADR 0063 / 0068）—— 来源、编号、时长、作者、标签等。"""
    assert kinds.MOVIE.table == "event_movie"
    assert {
        "source",
        "code",
        "duration",
        "creator",
        "label",
        "series",
        "director",
        "actors",
        "tags",
        "rating",
    } == {field.column for field in kinds.MOVIE.fields}


def test_the_movie_source_is_a_closed_set() -> None:
    """「来源」画成一排按钮，值域就是那八个 —— 它同时是**判重与打开影片网站的键**
    （ADR 0068 决策 1），所以多一个少一个都不只是多一栏的事。"""
    source = next(field for field in kinds.MOVIE.fields if field.id == "source")

    assert (
        source.choices
        == kinds.MOVIE_SOURCES
        == (
            "爱奇艺",
            "腾讯视频",
            "优酷",
            "芒果TV",
            "Bilibili",
            "YouTube",
            "JavDB",
            "Pornhub",
        )
    )


def test_an_unknown_kind_is_none_rather_than_a_key_error() -> None:
    """会话文件与库里的旧值都可能给出认不出的 id，静默当「没有」而不是崩。"""
    assert kinds.kind_by_id("trip") is kinds.TRIP
    assert kinds.kind_by_id("show") is kinds.SHOW
    assert kinds.kind_by_id("movie") is kinds.MOVIE
    assert kinds.kind_by_id("concert") is None
    assert kinds.kind_by_id(None) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("京都", ("京都",)),
        ("京都, 奈良", ("京都", "奈良")),
        ("京都，奈良", ("京都", "奈良")),
        ("京都、奈良", ("京都", "奈良")),
        ("京都, 奈良,, ", ("京都", "奈良")),
        ("", ()),
    ],
)
def test_the_separators_a_user_types_all_split(text: str, expected: tuple[str, ...]) -> None:
    """中英文逗号与顿号都认 —— 用户手上没有一条会被统一的口径。"""
    assert kinds.split_multi(text) == expected


def test_several_places_travel_stored_as_one_cell() -> None:
    """多值字段在库里只占一格（拆开要另开一张表，而筛选只需要子串匹配）。"""
    assert kinds.join_multi(("京都", "奈良")) == f"京都{kinds.MULTI_SEPARATOR}奈良"


def test_the_suggested_title_names_the_day_and_the_key_field() -> None:
    """新建时给一个能认出来的名字，省一次决定。日期在前，关键字段在后。"""
    assert (
        kinds.suggested_title(kinds.TRIP, {"destination": "京都, 奈良", "started_on": "2026-05-16"})
        == "2026-05-16 · 京都、奈良"
    )


def test_the_suggested_title_falls_back_to_the_kind() -> None:
    """什么都没填时也得有一个名字 —— 名称那一栏是必填的。"""
    assert kinds.suggested_title(kinds.TRIP, {}) == "新的旅行"


def test_the_suggested_title_keeps_whichever_half_was_filled() -> None:
    """只记得月份、或者只记得去了哪，都不该凑出一个假的另一半。"""
    assert kinds.suggested_title(kinds.TRIP, {"started_on": "2026-05-16"}) == "2026-05-16"
    assert kinds.suggested_title(kinds.TRIP, {"destination": "京都"}) == "京都"


def test_a_show_title_prefers_the_program_over_the_artist() -> None:
    """演出有剧目就用剧目，那是它更具体的名字。"""
    values = {"started_on": "2026-05-16", "artist": "某乐队", "program": "某音乐剧"}

    assert kinds.suggested_title(kinds.SHOW, values) == "2026-05-16 · 某音乐剧"


def test_a_show_title_falls_back_to_the_artist_without_a_program() -> None:
    """没有剧目（演唱会多半没有）就退回艺人。"""
    values = {"started_on": "2026-05-16", "artist": "某乐队", "program": ""}

    assert kinds.suggested_title(kinds.SHOW, values) == "2026-05-16 · 某乐队"


def test_only_a_missing_name_is_a_problem() -> None:
    assert kinds.title_problem(kinds.TRIP, "京都五日") is None
    assert kinds.title_problem(kinds.TRIP, "  ") is not None


@pytest.mark.parametrize("text", ["2026-05-16", "2026-05", "2026-5-6", "2026-05-16 "])
def test_a_day_or_a_month_alone_is_understood(text: str) -> None:
    """只知道「五月去的」也是常态，逼用户补一个具体日子只会让人随便挑一个。"""
    assert kinds.started_on_problem(text) is None


def test_an_empty_day_is_not_a_problem() -> None:
    """日期可以完全不记 —— 不是每一趟旅行都记得日子。"""
    assert kinds.started_on_problem("") is None
    assert kinds.ended_on_problem("2026-05-16", "") is None


@pytest.mark.parametrize("text", ["去年夏天", "2026/05/16", "2026-13-01", "2026-02-31"])
def test_a_day_nobody_can_read_is_reported_with_the_chinese_reason(text: str) -> None:
    """二月三十一号要挡下来：手写「一个月最多 31 天」会把二月糊弄过去。"""
    assert kinds.started_on_problem(text) is not None


def test_an_end_before_the_start_is_reported() -> None:
    problem = kinds.ended_on_problem("2026-05-16", "2026-05-10")

    assert problem is not None
    assert "2026-05-10" in problem


def test_an_end_before_the_start_is_fine_when_there_is_no_start() -> None:
    """只填了结束日期时不报 —— 拿它跟「不知道」比是没有意义的。"""
    assert kinds.ended_on_problem("", "2026-05-10") is None


def test_a_day_is_stored_normalized() -> None:
    """``2026-5-6`` 与 ``2026-05-06`` 存成同一串，否则按日期范围筛选会漏掉一半。"""
    assert kinds.normalize_day("2026-5-6") == "2026-05-06"
    assert kinds.normalize_day("2026-05") == "2026-05-01"
    assert kinds.normalize_day("") == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3280", 3280.0), ("3280.50", 3280.5), ("3,280", 3280.0), (" 3280 ", 3280.0), ("", None)],
)
def test_a_cost_reads_through_a_thousands_separator(text: str, expected: float | None) -> None:
    assert kinds.parse_cost(text) == expected


def test_a_cost_nobody_can_read_says_so_in_chinese() -> None:
    problem = kinds.cost_problem("三千多")

    assert problem is not None
    assert "不是一个数字" in problem


def test_a_negative_cost_is_reported_and_an_empty_one_is_not() -> None:
    assert kinds.cost_problem("-100") is not None
    assert kinds.cost_problem("") is None
    assert kinds.cost_problem("0") is None


@pytest.mark.parametrize("kind", [kinds.TRIP, kinds.SHOW, kinds.MOVIE])
def test_the_rating_choices_run_from_one_to_five(kind: kinds.EventKind) -> None:
    """评分画成一排按钮，值域就是这一个闭区间 —— 不给 0 与半星；三类事件的评分是同一档。"""
    rating = next(field for field in kind.fields if field.id == "rating")

    assert rating.choices == ("1", "2", "3", "4", "5")
    assert rating.choices[0] == str(kinds.RATING_MIN)
    assert rating.choices[-1] == str(kinds.RATING_MAX)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (0.0, ""),
        (4.2, "4"),
        (4.5, "5"),
        (4.59, "5"),
        (5.0, "5"),
        (5.5, ""),
    ],
)
def test_a_source_rating_is_rounded_into_the_five_buttons(
    value: float | None, expected: str
) -> None:
    """来源站给的是小数（JavDB 的 4.59），面板只有五个按钮 —— 四舍五入，收不进去就当没评分。"""
    assert kinds.rating_text(value) == expected
