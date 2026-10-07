"""``gallery.core.library.search`` 与它那条 SQL 的测试：输入怎么解析、SQL 怎么生成、
真库上查得对不对。最后那组最要紧：检索真正的失败方式是「SQL 生成对了但查出来的东西不对」。
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import pytest

from gallery.core import paths
from gallery.core.errors import IndexUnavailableError
from gallery.core.events.store import SCHEMA_NAME
from gallery.core.library.facade import Library
from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.library.search import (
    DEFAULT_FIELD,
    HALF_WIDTH_RANGE_SEPARATOR,
    SEARCH_FIELDS,
    SearchCondition,
    SearchField,
    albums_kept,
    build_count_query,
    build_match_query,
    build_query,
    make_condition,
    match_field,
    parse_input,
    partial_field,
    relative_folder_path,
    rows_to_refs,
    suggestions,
    unusable_reason,
)
from gallery.core.metadata.facets import (
    FACET_CAMERA,
    FACET_FOLDER,
    FACET_FORMAT,
    FACET_NAME,
    PhotoFacets,
)


def test_no_hash_means_the_file_name() -> None:
    """不带 ``#`` 时默认搜文件名 —— 用户最常搜的就是它。"""
    parsed = parse_input("海边")

    assert parsed is not None
    assert parsed.selecting is False
    assert parsed.field is not None and parsed.field.id == FACET_NAME
    assert parsed.value == "海边"


@pytest.mark.parametrize("raw", ["", "   ", "#"])
def test_nothing_to_do(raw: str) -> None:
    assert make_condition(parse_input(raw)) is None


def test_a_lone_hash_asks_for_the_field_list() -> None:
    parsed = parse_input("#")

    assert parsed is not None and parsed.selecting is True
    assert parsed.field is None  # 还没挑：整个列表都要给


def test_a_field_name_alone_is_still_choosing() -> None:
    parsed = parse_input("#相机")

    assert parsed is not None and parsed.selecting is True
    assert parsed.field is not None and parsed.field.id == FACET_CAMERA
    assert make_condition(parsed) is None


def test_field_and_value_makes_a_condition() -> None:
    condition = make_condition(parse_input("#相机 Canon EOS R5"))

    assert condition is not None
    assert condition.text == "Canon EOS R5"
    assert condition.chip_text == "相机：Canon EOS R5"


def test_an_unknown_field_falls_back_to_the_file_name() -> None:
    """``#海边`` 多半是想找一张名字带「海边」的照片，而不是一个语法错误。

    认不出字段就报错，说不出任何用户没打算做的事。
    """
    parsed = parse_input("#海边")

    assert parsed is not None and parsed.selecting is False
    assert parsed.field is not None and parsed.field.id == FACET_NAME
    assert parsed.value == "海边"


def test_the_default_field_is_the_first_one_offered() -> None:
    assert SEARCH_FIELDS[0] is DEFAULT_FIELD


def test_a_field_matches_both_its_id_and_its_chinese_name() -> None:
    """用户一定会打中文；``#camera`` 与 ``#相机`` 必须是同一个字段。"""
    assert match_field("相机") is not None
    assert match_field("camera") is not None
    assert match_field("相机") is match_field("camera")


def test_spaces_inside_a_field_name_do_not_matter() -> None:
    assert match_field("省/州") == match_field("省 / 州")


def test_suggestions_filter_as_the_user_types() -> None:
    """边打边筛：敲「文件格」就该看见「文件格式」，而不是整个列表。"""
    assert len(suggestions("")) == len(SEARCH_FIELDS)
    assert [field.id for field in suggestions("文件格")] == [FACET_FORMAT]
    assert [field.id for field in suggestions("cam")] == [FACET_CAMERA]
    assert suggestions("压根没有这个字段") == []


def test_suggestions_keep_the_declared_order() -> None:
    """命中就在原位，不做重排：「相册」在第二位，它就该在第二位。"""
    listed = [field.id for field in suggestions("")]
    assert listed[:2] == [FACET_NAME, FACET_FOLDER]


def test_partial_field_is_what_follows_the_hash() -> None:
    assert partial_field("#相机") == "相机"
    assert partial_field("#相机 Canon") == ""  # 已经进入「值」那一步
    assert partial_field("海边") == ""


def _condition(text: str) -> SearchCondition | None:
    return make_condition(parse_input(text))


def test_numbers_accept_comparators() -> None:
    condition = _condition("#ISO >800")

    assert condition is not None and condition.comparator == ">"
    assert condition.value == "800"


def test_a_number_field_rejects_a_word() -> None:
    """解析不了就是提交不了 —— 界面据此说一句人话，而不是收下一条永远查不到的条件。"""
    assert _condition("#ISO abc") is None


def test_sizes_accept_units() -> None:
    condition = _condition("#文件大小 5MB")

    assert condition is not None and condition.value == "5000000"


def test_dates_become_a_range() -> None:
    """不带比较符时是「那一段之内」：``#拍摄时间 2024`` 是 2024 年，不是「晚于 2024」。"""
    condition = _condition("#拍摄时间 2024-05")

    assert condition is not None and condition.comparator == "between"
    low, _, high = condition.value.partition("|")
    assert float(low) < float(high)


def _bounds(condition: SearchCondition) -> tuple[float, float]:
    """一条 ``between`` 条件 → 那两个 epoch 秒。"""
    assert condition.comparator == "between"
    low, _, high = condition.value.partition("|")
    return float(low), float(high)


def _epoch(year: int, month: int = 1, day: int = 1) -> float:
    return datetime.datetime(year, month, day).timestamp()  # noqa: DTZ001 - 与库里的算法一致


def test_a_range_takes_both_ends_at_their_own_granularity() -> None:
    """``2023～2024`` 是「2023 与 2024 两年」，不是「2023 年 1 月 1 日到 2024 年 1 月 1 日」。

    两端各含自己那一整段，与裸值是同一套语义 —— 于是「去年夏天」写成
    ``2023-06 ～ 2023-08``，右边含的是 8 月 31 日那一整天。
    """
    years = _condition("#拍摄时间 2023～2024")
    months = _condition("#拍摄时间 2023-06 ～ 2023-08")
    half_width = _condition("#拍摄时间 2023-06~2023-08")

    assert years is not None and _bounds(years) == (_epoch(2023, 1, 1), _epoch(2025, 1, 1))
    assert months is not None and _bounds(months) == (_epoch(2023, 6, 1), _epoch(2023, 9, 1))
    # 半角与全角落在同一段上，不是两套各自为政的语义。
    assert half_width is not None and _bounds(half_width) == _bounds(months)


@pytest.mark.parametrize(
    "typed",
    [
        "#拍摄时间 2023～2024",
        "#拍摄时间 2023 ～ 2024",
        "#拍摄时间  2023～2024 ",
        "#拍摄时间 2023 ～2024",
        # **半角也认**：英文键盘敲出来的就是它。日期语法里根本不会出现波浪号，
        # 多认一种不产生歧义，少认一种只是把用户挡在门外。
        "#拍摄时间 2023~2024",
        "#拍摄时间 2023 ~ 2024",
    ],
)
def test_spaces_and_width_around_the_separator_do_not_matter(typed: str) -> None:
    """分隔符两侧有没有空格、是全角还是半角，都是用户手指的事，不是语义的事。

    两种写法必须落在**同一个区间**上：分开认就意味着有两套「什么算对」，迟早对不上。
    """
    condition = _condition(typed)

    assert condition is not None, typed
    assert _bounds(condition) == (_epoch(2023, 1, 1), _epoch(2025, 1, 1))


def test_a_range_reaches_the_modified_time_too() -> None:
    """``#修改时间`` 走同一个解析器，只是落库时把秒换成纳秒 —— 那一步值得单独钉一次。"""
    condition = _condition("#修改时间 2023～2024")

    assert condition is not None and condition.comparator == "between"
    sql, values = build_query([condition])
    assert "p.mtime_ns" in sql
    # 纳秒 = 秒 × 1e9，否则「2023 年改过的照片」会一条都查不到。
    assert values == [_epoch(2023, 1, 1) * 1e9, _epoch(2025, 1, 1) * 1e9]


def test_a_range_can_be_put_back_into_the_box() -> None:
    """胶囊放回搜索框再敲一遍，得到的是**同一条** —— 两端的空格也算进原文。"""
    condition = _condition("#拍摄时间 2023 ～ 2024")

    assert condition is not None
    again = _condition(condition.as_input)
    assert again is not None
    assert (again.field.id, again.value) == (condition.field.id, condition.value)


@pytest.mark.parametrize(
    "typed",
    [
        "#拍摄时间 2024～2023",  # 写反了
        "#拍摄时间 2024～2024",  # 两端同一段：那不是一段区间
        "#拍摄时间 2023～",  # 缺一端
        "#拍摄时间 ～2023",
        "#拍摄时间 >2023～2024",  # 区间里不认比较符
        "#拍摄时间 2023～2024～2025",  # 三个分隔符
        "#拍摄时间 2023~2024~2025",  # 半角也一样数
        "#拍摄时间 2023~",  # 缺一端（半角）
    ],
)
def test_a_range_is_refused_rather_than_half_honoured(typed: str) -> None:
    """宁可提交不了，也不要收下一条意思不明的条件。

    收下了会怎样：``2024～2023`` 生成的 SQL 是 ``>= 大的 AND < 小的``，于是**安静地
    返回 0 条** —— 与「条件太严」长得一模一样，正是最该避免的那种假答案。
    """
    assert _condition(typed) is None


def test_a_day_that_does_not_exist_is_refused_rather_than_raised() -> None:
    """``2023-02-30`` 光看「日 ≤ 31」是过得去的，真去造日期才会炸。

    炸出去的地方是界面上的回车，于是用户只看到「按了回车什么也没发生」。
    解析不了就是解析不了，不该是一段栈。
    """
    for typed in ("#拍摄时间 2023-02-30", "#拍摄时间 2023-02-29", "#拍摄时间 2023-04-31"):
        assert _condition(typed) is None, typed
    # 平年的 2 月 29 日不存在，闰年的那一张存在 —— 别把这个判反了。
    assert _condition("#拍摄时间 2024-02-29") is not None


def test_a_reversed_range_says_which_end_is_wrong() -> None:
    """横幅是用户唯一能看见的反馈，「要一个值」对两种毛病说的是同一句话。"""
    field = match_field("拍摄时间")
    assert field is not None

    assert "起始要写在前面" in unusable_reason(field, "2024～2023")
    # 半角写法走的是同一条路 —— 横幅那句话也得是同一句，不能一半认一半不认。
    assert "起始要写在前面" in unusable_reason(field, "2024~2023")
    assert "起始要写在前面" not in unusable_reason(field, "2024-13")


def test_the_date_hint_says_that_the_half_width_separator_works_too() -> None:
    """当初挡住人的正是这件事：报错只说「要一个日期」，半个字没提是哪一种写法不对。

    于是横幅里明说两种分隔符都认 —— 这句话本身就是这次改动的全部意义。
    """
    field = match_field("拍摄时间")
    assert field is not None

    assert HALF_WIDTH_RANGE_SEPARATOR in unusable_reason(field, "瞎写")


def test_the_date_hint_names_the_range_syntax() -> None:
    """日期那一族做不出更细的理由，于是至少把能写什么说出来（其余各栏照旧用 hint）。"""
    field = match_field("拍摄时间")
    iso = match_field("ISO")
    assert field is not None and iso is not None

    assert "2023～2024" in unusable_reason(field, "瞎写")
    assert "1600" in unusable_reason(iso, "abc")


def test_an_album_is_normalised() -> None:
    """两边多余的斜杠不参与匹配，否则用户敲「旅行/」就查不到「旅行」。"""
    condition = _condition("#相册 旅行/")

    assert condition is not None and condition.value == "旅行"


def test_a_format_is_normalised_to_a_suffix() -> None:
    for text in ("#文件格式 jpg", "#文件格式 JPEG", "#文件格式 .png"):
        condition = _condition(text)
        assert condition is not None, text
        assert condition.value.startswith("."), text


@pytest.mark.parametrize(
    ("typed", "restored"),
    [
        ("海边", "海边"),
        ("#相机 Canon", "#相机 Canon"),
        ("#ISO >800", "#ISO >800"),
        ("#相册 旅行/", "#相册 旅行"),
        ("#省/州 浙江", "#省/州 浙江"),
    ],
)
def test_a_condition_can_be_put_back_into_the_box(typed: str, restored: str) -> None:
    """胶囊放回搜索框时要**带字段前缀**。

    只放值的话用户看到的是「旅行」，不知道这一条是按哪个字段筛的 —— 而改条件多半正是
    要换字段。顺带钉住字段名**不带空格**这条：搜索框按第一个空格分开，「#省 / 州 浙江」
    会把「省」当成字段名而认不出，于是悄悄变成按文件名搜。
    """
    condition = _condition(typed)

    assert condition is not None
    assert condition.as_input == restored
    # 放回去再敲一遍，得到的是**同一条**条件（改不动的那部分真的没变）。
    again = _condition(restored)
    assert again is not None
    assert (again.field.id, again.value) == (condition.field.id, condition.value)


def test_conditions_are_combined_with_and() -> None:
    sql, values = build_query([_condition("海边"), _condition("#相机 Canon")])  # type: ignore[list-item]

    assert sql.count(" AND ") == 1
    assert values == ["%海边%", "%Canon%"]


def test_like_wildcards_in_the_input_are_escaped() -> None:
    """用户搜 ``IMG_001`` 时 ``_`` 不该去顶替任何字符 —— 否则会多出一堆 IMGX001。"""
    _, values = build_query([_condition("IMG_001")])  # type: ignore[list-item]

    assert values == ["%IMG\\_001%"]


def test_count_query_shares_the_conditions() -> None:
    """总数与结果必须是同一次口径下的两句话，否则「共 N 张」会对不上屏幕上的格子。"""
    count_sql, _ = build_count_query([_condition("海边")])  # type: ignore[list-item]
    result_sql, _ = build_query([_condition("海边")])  # type: ignore[list-item]

    assert count_sql.startswith("SELECT COUNT(*)")
    assert "name LIKE ?" in count_sql and "name LIKE ?" in result_sql


def test_rows_become_refs_without_touching_the_disk() -> None:
    refs = rows_to_refs([("/库/海边.jpg", 2048, 99), ("坏行", None, 1)])

    assert [ref.path.name for ref in refs] == ["海边.jpg"]
    assert (refs[0].size, refs[0].mtime_ns) == (2048, 99)


def _library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    """现造一个小库：根下两张、一棵 ``旅行/2023`` 与 ``旅行/2024``、一张 ``家庭``。"""
    root = tmp_path / "库"
    root.mkdir(parents=True, exist_ok=True)
    for directory, names in (
        (root, ("海边-01.jpg", "海边-02.jpg")),
        (root / "旅行" / "2024", ("海边-03.jpg",)),
        (root / "旅行" / "2023", ("山.jpg",)),
    ):
        for name in names:
            make_photo(name, directory=directory)
    return Library.open(root)


def _names(refs: list[PhotoRef]) -> list[str]:
    return sorted(ref.name for ref in refs)


def test_the_album_path_matches_the_grouping_one(tmp_path: Path) -> None:
    """索引里存的相册字符串与分组用的是**同一个函数**算出来的。

    两处各写一份迟早算出不一样的写法，于是「#相册 旅行」一条都查不到。
    """
    from gallery.core.metadata.facets import relative_folder

    photo = tmp_path / "旅行" / "2024" / "a.jpg"
    assert relative_folder_path(tmp_path, photo) == relative_folder(photo, tmp_path) == "旅行/2024"


def test_the_photo_table_covers_files_with_no_exif(tmp_path: Path, make_photo) -> None:
    """``facets`` 只有读过 EXIF 的照片才有行；文件名与相册必须能搜到所有照片。"""
    library = _library(tmp_path, make_photo)

    assert library.indexed_photos() == 0
    assert library.index_photos() == 4
    assert library.indexed_photos() == 4

    found = library.search_photos([_condition("海边")])  # type: ignore[list-item]
    assert _names(found.refs) == ["海边-01.jpg", "海边-02.jpg", "海边-03.jpg"]


def test_the_second_pass_writes_nothing(tmp_path: Path, make_photo) -> None:
    """已经建好的索引不该被整表重写一遍 —— 白花几百毫秒。"""
    library = _library(tmp_path, make_photo)
    library.index_photos()

    assert library.index_photos() == 0


def test_a_changed_file_gets_a_fresh_row(tmp_path: Path, make_photo) -> None:
    library = _library(tmp_path, make_photo)
    library.index_photos()
    photo = library.root / "海边-01.jpg"
    photo.write_bytes(photo.read_bytes() + b"\x00")

    changed = [PhotoRef.from_path(photo)]

    assert library.remember_photos(changed) == 1
    assert library.index_photos() == 0


def test_album_search_includes_sub_albums(tmp_path: Path, make_photo) -> None:
    library = _library(tmp_path, make_photo)
    library.index_photos()

    found = library.search_photos([_condition("#相册 旅行")])  # type: ignore[list-item]

    assert _names(found.refs) == ["山.jpg", "海边-03.jpg"]


def test_metadata_conditions_use_the_facet_rows(tmp_path: Path, make_photo) -> None:
    """元数据那一族走 ``facets``；没有读过的照片自然查不到（那是「我们知不知道」）。"""
    library = _library(tmp_path, make_photo)
    library.index_photos()
    # **按名字取，不按序号**：``iter_photos`` 的顺序是目录遍历的顺序。
    by_name = {ref.name: ref for ref in library.iter_photos()}
    library.facet_index.store(
        [
            (by_name["海边-01.jpg"], PhotoFacets(camera="Canon EOS R5", city="杭州", iso=800)),
            (by_name["山.jpg"], PhotoFacets(camera="Sony A7", city="波尔多", iso=100)),
        ]
    )

    assert _names(library.search_photos([_condition("#相机 Canon")]).refs) == ["海边-01.jpg"]  # type: ignore[list-item]
    # 条件值大小写不敏感（SQLite 的 LIKE 对 ASCII 如此），用户不用记机身名的写法。
    assert _names(library.search_photos([_condition("#相机 sony")]).refs) == ["山.jpg"]  # type: ignore[list-item]
    assert _names(library.search_photos([_condition("#城市 杭州")]).refs) == ["海边-01.jpg"]  # type: ignore[list-item]
    assert _names(library.search_photos([_condition("#ISO >400")]).refs) == ["海边-01.jpg"]  # type: ignore[list-item]


def _taken_in(year: int, month: int, day: int) -> PhotoFacets:
    """一张在某年某月某日中午拍的照片的 facet 快照。

    时间取**中午**而不是零点：零点正好压在区间的边界上，``>=`` 与 ``<`` 差一秒就会少算
    它一张 —— 那种失败与真正的缺陷长得一模一样。
    """
    moment = datetime.datetime(year, month, day, 12, 0)  # noqa: DTZ001 - 与库里的算法一致
    return PhotoFacets(captured_at=moment.timestamp(), year=year, month=month, day=day)


def test_a_capture_date_finds_the_photos_taken_then(tmp_path: Path, make_photo) -> None:
    """``#拍摄时间`` 要在真库上查得对 —— 只测解析与生成 SQL 那两层的话，列名写错不会红
    （``PhotoFacets`` 上的**属性名**与列名不是一回事）。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    by_name = {ref.name: ref for ref in library.iter_photos()}
    library.facet_index.store(
        [
            (by_name["海边-01.jpg"], _taken_in(2023, 5, 16)),
            (by_name["山.jpg"], _taken_in(2024, 5, 16)),
        ]
    )

    assert _names(library.search_photos([_condition("#拍摄时间 2023")]).refs) == [  # type: ignore[list-item]
        "海边-01.jpg"
    ]
    assert _names(library.search_photos([_condition("#拍摄时间 2023-05-16")]).refs) == [  # type: ignore[list-item]
        "海边-01.jpg"
    ]
    # 那天没有照片 —— 区间要真的是一段，不是一个「大概在那前后」。
    assert _names(library.search_photos([_condition("#拍摄时间 2023-05-17")]).refs) == []  # type: ignore[list-item]
    # 带比较符时取的是**那一段之外**：`>2023` 是「晚于 2023 年年底」，这条钉的是比较符那一支。
    assert _names(library.search_photos([_condition("#拍摄时间 >2023")]).refs) == ["山.jpg"]  # type: ignore[list-item]


def test_a_capture_range_spans_both_ends_of_the_years_it_names(tmp_path: Path, make_photo) -> None:
    """``2023～2024`` 在真库上要真的把那两年都圈进来 —— 两端各含自己那一整段。"""
    library = _library(tmp_path, make_photo)
    library.index_photos()
    by_name = {ref.name: ref for ref in library.iter_photos()}
    library.facet_index.store(
        [
            (by_name["海边-01.jpg"], _taken_in(2022, 12, 31)),
            (by_name["海边-02.jpg"], _taken_in(2023, 1, 1)),
            (by_name["海边-03.jpg"], _taken_in(2024, 12, 31)),
            (by_name["山.jpg"], _taken_in(2025, 1, 1)),
        ]
    )

    # 头一年元旦与末一年年底都**在**区间里，紧挨着的那两天不在。
    assert _names(library.search_photos([_condition("#拍摄时间 2023～2024")]).refs) == [  # type: ignore[list-item]
        "海边-02.jpg",
        "海边-03.jpg",
    ]
    # 半角写法在真库上要查到**同一批**照片 —— 两种写法不是两套语义。
    assert _names(library.search_photos([_condition("#拍摄时间 2023~2024")]).refs) == [  # type: ignore[list-item]
        "海边-02.jpg",
        "海边-03.jpg",
    ]
    # 缩到月，两头仍各含整月。
    assert _names(library.search_photos([_condition("#拍摄时间 2024-11 ～ 2024-12")]).refs) == [  # type: ignore[list-item]
        "海边-03.jpg"
    ]


#: 每种 ``kind`` 取一个**能解析成功**的样例值：值写坏了就只会得到「解析不出来」，
#: 那个 ``None`` 断的是另一件事。
_SAMPLE_VALUE: Final[dict[str, str]] = {
    "name": "海边",
    "album": "旅行",
    "format": "jpg",
    "text": "Canon",
    "size": "5MB",
    "number": "1",
    "datetime": "2024",
    "event": "京都",
}


def _sample(field: SearchField) -> str:
    """这个字段拿什么值去试。

    **事件那一族要按列的类型取**，不能按 ``kind`` 取：同一个 ``kind="event"`` 里既有文字
    （``#旅行·目的地 京都``）也有数值（``#旅行·花费 1``），给数值字段一个文字就只会得到
    「解析不出来」——而那个 ``None`` 断的是另一件事（见 :data:`_SAMPLE_VALUE`）。
    """
    if field.is_event:
        return "1" if field.sql_type in ("INTEGER", "REAL") else _SAMPLE_VALUE["event"]
    return _SAMPLE_VALUE[field.kind]


@contextmanager
def _connected(database: Path, root: Path) -> Iterator[sqlite3.Connection]:
    """连上索引并把事件库挂上去 —— **和 :class:`FacetIndex` 做法一致**。

    事件那一族的字段靠 ``EXISTS`` 子查询去读挂在同一条连接上的事件库（ADR 0043 决策 6），
    所以这两条守护要一个挂着事件库的连接才测得到它们；不开挂载的话它们会报
    ``no such table: ev.events``，而那不是列名写错。
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute(f"ATTACH DATABASE ? AS {SCHEMA_NAME}", (str(paths.events_file(root)),))
        yield connection
    finally:
        connection.close()


def test_every_searchable_field_survives_the_real_schema(tmp_path: Path, make_photo) -> None:
    """每个可搜字段都拿真 schema 执行一遍 —— **守护**。

    ``SEARCH_FIELDS`` 里那些列名是手写的，抄成 ``PhotoFacets`` 上的属性名没有人看得出来，
    而错列名要等 SQLite 执行才说 ``no such column``。所以这里不问「查得对不对」，
    只问「这句 SQL 能不能跑」。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    database = library.facet_index.path
    assert database is not None

    with _connected(database, library.root) as connection:
        for field in SEARCH_FIELDS:
            condition = _condition(f"#{field.label} {_sample(field)}")
            assert condition is not None, field.label
            sql, values = build_query([condition])
            # 列名不对时这里抛的正是 `no such column: …`，读得懂。
            connection.execute(sql, values).fetchall()


def test_conditions_narrow_each_other(tmp_path: Path, make_photo) -> None:
    library = _library(tmp_path, make_photo)
    library.index_photos()

    both = library.search_photos([_condition("海边"), _condition("#相册 旅行")])  # type: ignore[list-item]

    assert _names(both.refs) == ["海边-03.jpg"]


# ---- 当前层筛选（ADR 0040）：命中路径集合与相册卡片判据 --------------------------------


def test_the_match_query_survives_the_real_schema(tmp_path: Path, make_photo) -> None:
    """筛选那条查询里**每一个可搜字段**都要拿真 schema 执行一遍 —— 守护。

    与 :func:`test_every_searchable_field_survives_the_real_schema` 同源，但走的是
    :func:`build_match_query`：那条 SQL 有自己的一行 ``SELECT``，列名写错同样要到
    SQLite 执行才报 ``no such column``，而那一眼看上去就是「这一栏没得筛」。

    **别因为两条 SQL 共用同一个 ``WHERE`` 就当这条多余**：共用的只有条件那部分，取哪几列是
    各写各的（ADR 0040 决策 2）。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    database = library.facet_index.path
    assert database is not None

    with _connected(database, library.root) as connection:
        for field in SEARCH_FIELDS:
            condition = _condition(f"#{field.label} {_sample(field)}")
            assert condition is not None, field.label
            sql, values = build_match_query([condition])
            connection.execute(sql, values).fetchall()


def test_the_match_query_is_never_truncated(tmp_path: Path, make_photo) -> None:
    """那条 SQL **不许有 LIMIT** —— 守护。

    截断在这里等于静默丢命中：命中八千张而当前层的那几张排在第 5001 名之后时，界面上
    是「一条都没有」，而那不是答案，是查询自己偷偷少给了 —— 没有任何线索指向它。
    整库检索可以截断（结果本来就是给人浏览的前几百张），相交不行：
    **交出来的每一张都得是对的**。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    condition = _condition("jpg")
    assert condition is not None

    sql, _values = build_match_query([condition])

    assert "LIMIT" not in sql.upper()


def test_matching_paths_finds_the_same_photos_as_the_search(tmp_path: Path, make_photo) -> None:
    """两份查询对同一条条件要给出**同一批路径** —— 它们各自返回不同的列，却共用同一个 ``WHERE``。

    不一致时长成「筛选比搜索少一张」，而那极难分辨：既可能是真的少了命中，也可能是这一层
    本来就没有那张照片。所以钉在**纯核心**这一层，不掺界面。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    condition = _condition("海边")
    assert condition is not None

    found = library.matching_paths([condition])
    searched = library.search_photos([condition]).refs

    assert {ref.path for ref in searched} == found


def test_matching_paths_says_so_when_the_index_is_not_there(tmp_path: Path) -> None:
    """索引建不出来时**抛异常而不是返回空集**。

    返回空集的话，界面把整层都滤空了并说「这个视图里没有符合这些条件的照片」—— 那是在拿一件
    根本没做成的事回答用户。降级可以，假装没降级不行。

    **与检索说同一句话**：两个功能坏在同一个原因上（那个卷不许写缓存），用户不该被两个说法
    绕晕。
    """
    index = FacetIndex(None)
    ref = PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=1)
    index.store([(ref, PhotoFacets(camera="Canon"))])
    condition = _condition("海边")
    assert condition is not None

    with pytest.raises(IndexUnavailableError) as excinfo:
        index.match_paths([condition])

    assert "检索索引" in excinfo.value.message


def _albums(*paths: Path) -> list[FolderRef]:
    return [FolderRef(path=path, mtime_ns=0) for path in paths]


def test_an_album_card_survives_when_its_own_subtree_has_a_hit(tmp_path: Path) -> None:
    """子树里有命中，卡片就留 —— 卡片不是照片，但它代表的那棵子树里有。"""
    root = tmp_path / "库"
    albums = _albums(root / "旅行", root / "家庭")

    kept = albums_kept(albums, [root / "旅行" / "2024" / "山.jpg"], root=root)

    assert kept == [albums[0]]


def test_an_album_card_dies_when_only_a_sibling_matched(tmp_path: Path) -> None:
    """命中在**兄弟目录**里时这张卡片不该留 —— 那不是它的子树。

    前缀比较少一个分隔符就会把 ``旅行`` 与 ``旅行者`` 认成同一家，而界面上那是两个相册。
    """
    root = tmp_path / "库"
    albums = _albums(root / "旅行")

    kept = albums_kept(albums, [root / "旅行者" / "山.jpg"], root=root)

    assert kept == []


def test_a_hit_in_the_album_itself_does_not_rescue_its_sibling_card(tmp_path: Path) -> None:
    """命中的照片就在**这一层**上时，它不归属任何子相册卡片。

    卡片判据问的是「这张卡片代表的子树」，而根下的照片不属于任何一张卡片 —— 于是根下有命中时，
    该留的是照片本身，**所有**子相册卡片都照常按各自的子树判。
    """
    root = tmp_path / "库"
    albums = _albums(root / "旅行")

    kept = albums_kept(albums, [root / "海边.jpg"], root=root)

    assert kept == []


def test_an_album_at_the_library_root_is_matched_by_its_name(tmp_path: Path) -> None:
    """根下的子相册也要能被命中 —— 它的相对位置就是它自己的名字（空串那一档只留给照片）。"""
    root = tmp_path / "库"
    albums = _albums(root / "旅行")

    kept = albums_kept(albums, [root / "旅行" / "2024" / "山.jpg"], root=root)

    assert kept == [albums[0]]


def test_no_albums_means_nothing_to_decide(tmp_path: Path) -> None:
    """相册模式之外压根没有卡片 —— 平铺与日历那两层不经过这个判据。"""
    assert albums_kept([], [tmp_path / "海边.jpg"], root=tmp_path) == []


def test_no_hits_means_no_cards(tmp_path: Path) -> None:
    """一条都没命中时整层只剩（或也不剩）照片 —— 摆着一排点进去全是空的相册卡片更糟。"""
    root = tmp_path / "库"

    assert albums_kept(_albums(root / "旅行"), [], root=root) == []


def test_deleted_files_are_not_counted(tmp_path: Path, make_photo) -> None:
    """索引里可能留着文件早已被删掉的那些行 —— 它们既不该出现，也不该算进总数。"""
    library = _library(tmp_path, make_photo)
    library.index_photos()
    (library.root / "海边-01.jpg").unlink()

    found = library.search_photos([_condition("海边")])  # type: ignore[list-item]

    assert _names(found.refs) == ["海边-02.jpg", "海边-03.jpg"]
    assert found.total == 2


def test_forgetting_a_path_clears_both_tables(tmp_path: Path, make_photo) -> None:
    """少清一张表就是给检索留一条假命中：搜出来的却是一张打不开的缩略图。"""
    library = _library(tmp_path, make_photo)
    library.index_photos()
    ref = next(library.iter_photos())

    library.forget_paths([ref.path])

    assert library.indexed_photos() == 3
    assert library.search_photos([_condition(ref.name)]).refs == []  # type: ignore[list-item]


def test_pruning_clears_both_tables(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    root = tmp_path
    refs = [
        PhotoRef(path=root / "a.jpg", size=1, mtime_ns=1),
        PhotoRef(path=root / "b.jpg", size=1, mtime_ns=1),
    ]
    index.store_photos(refs, root=root)

    assert index.prune([refs[0].path]) == 1
    assert index.count_photos() == 1
    index.close()


def test_clearing_empties_both_tables(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    root = tmp_path
    index.store_photos([PhotoRef(path=root / "a.jpg", size=1, mtime_ns=1)], root=root)

    index.clear()

    assert index.count_photos() == 0
    index.close()


def test_search_needs_an_index_on_disk(tmp_path: Path) -> None:
    """库所在卷只读时检索做不了 —— 但那是「这一件做不了」，不是「整个软件坏了」：
    分组与排序把读到的值揣在内存里算，照常可用。
    """
    index = FacetIndex(None)
    ref = PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=1)
    index.store([(ref, PhotoFacets(camera="Canon"))])

    with pytest.raises(IndexUnavailableError) as excinfo:
        index.search([_condition("海边")])  # type: ignore[list-item]

    assert "检索索引" in excinfo.value.message
