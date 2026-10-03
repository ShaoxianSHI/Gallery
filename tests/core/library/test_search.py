"""``gallery.core.library.search`` 与它那条 SQL 的测试。

三组：**输入怎么解析**（用户敲的那一串到底是什么意思）、**SQL 怎么生成**（哪个字段落到
哪一列、怎么匹配）、**真库上查得对不对**（``tmp_path`` 里现造一个库）。

最后那组最要紧：前面两组都只是「断言我们自己写的字符串」，而检索真正的失败方式
是「SQL 生成对了但查出来的东西不对」—— 于是每一条条件都在真库上跑一遍。
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from gallery.core.errors import IndexUnavailableError
from gallery.core.library.facade import Library
from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import PhotoRef
from gallery.core.library.search import (
    DEFAULT_FIELD,
    SEARCH_FIELDS,
    SearchCondition,
    build_count_query,
    build_query,
    make_condition,
    match_field,
    parse_input,
    partial_field,
    relative_folder_path,
    rows_to_refs,
    suggestions,
)
from gallery.core.metadata.facets import (
    FACET_CAMERA,
    FACET_FOLDER,
    FACET_FORMAT,
    FACET_NAME,
    PhotoFacets,
)

# ---- 解析输入 ---------------------------------------------------------------------


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


# ---- 字段名 ------------------------------------------------------------------------


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


# ---- 值怎么解析 -------------------------------------------------------------------


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


def test_an_album_is_normalised() -> None:
    """两边多余的斜杠不参与匹配，否则用户敲「旅行/」就查不到「旅行」。"""
    condition = _condition("#相册 旅行/")

    assert condition is not None and condition.value == "旅行"


def test_a_format_is_normalised_to_a_suffix() -> None:
    for text in ("#文件格式 jpg", "#文件格式 JPEG", "#文件格式 .png"):
        condition = _condition(text)
        assert condition is not None, text
        assert condition.value.startswith("."), text


# ---- SQL ---------------------------------------------------------------------------


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
    要换字段，那才是这个交互的主要用法。

    顺带钉住字段名**不带空格**这条：搜索框按第一个空格把字段名与值分开，「#省 / 州 浙江」
    会把「省」当成字段名而认不出，于是那一条悄悄变成按文件名搜（候选列表里补全出来的
    是没有空格的那个写法）。
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


# ---- 索引 ---------------------------------------------------------------------------


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
    # **按名字取，不按序号**：``iter_photos`` 的顺序是目录遍历的顺序，
    # 拿 ``refs[3]`` 当「山.jpg」只在某一次目录顺序下成立。
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

    时间取**中午**而不是零点：零点正好压在区间的边界上，``>=`` 与 ``<`` 差一秒就会
    少算它一张 —— 那种失败与真正的缺陷长得一模一样，排查起来白费时间。
    """
    moment = datetime.datetime(year, month, day, 12, 0)  # noqa: DTZ001 - 与库里的算法一致
    return PhotoFacets(captured_at=moment.timestamp(), year=year, month=month, day=day)


def test_a_capture_date_finds_the_photos_taken_then(tmp_path: Path, make_photo) -> None:
    """``#拍摄时间`` 要在真库上查得对 —— 这一族从来没被真库执行过，列名写错也不会红。

    曾经的故障：字段表里把这一项的列写成了 ``f.captured_at``（那是 ``PhotoFacets`` 上的
    **属性名**，不是列名），于是每一种写法都在执行时报 ``no such column``，界面表现成
    「一条都搜不出来」。解析与生成 SQL 那两层照样全绿。
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
    # 带比较符时取的是**那一段之外**：`>2023` 是「晚于 2023 年年底」，于是落在 2024 年的
    # 那张被选中，2023 年那张不是 —— 这条钉住的是比较符那一支，不是区间那一支。
    assert _names(library.search_photos([_condition("#拍摄时间 >2023")]).refs) == ["山.jpg"]  # type: ignore[list-item]


#: 每种 ``kind`` 取一个**能解析成功**的样例值：守护测试要的是把每个字段都真的查一遍，
#: 而值写坏了就只会得到「解析不出来」，那个 ``None`` 断的是另一件事。
_SAMPLE_VALUE: Final[dict[str, str]] = {
    "name": "海边",
    "album": "旅行",
    "format": "jpg",
    "text": "Canon",
    "size": "5MB",
    "number": "1",
    "datetime": "2024",
}


def test_every_searchable_field_survives_the_real_schema(tmp_path: Path, make_photo) -> None:
    """每个可搜字段都拿真 schema 执行一遍 —— **守护**。

    ``SEARCH_FIELDS`` 里那些列名是手写的，抄成 ``PhotoFacets`` 上的属性名不会有任何人
    看出来（只有拍摄时间这一项两者还不一样），而错列名要等 SQLite 执行才说
    ``no such column``。所以这里不问「查得对不对」，只问「这句 SQL 能不能跑」。
    """
    library = _library(tmp_path, make_photo)
    library.index_photos()
    database = library.facet_index.path
    assert database is not None

    with sqlite3.connect(database) as connection:
        for field in SEARCH_FIELDS:
            condition = _condition(f"#{field.label} {_SAMPLE_VALUE[field.kind]}")
            assert condition is not None, field.label
            sql, values = build_query([condition])
            # 列名不对时这里抛的正是 `no such column: …`，读得懂。
            connection.execute(sql, values).fetchall()


def test_conditions_narrow_each_other(tmp_path: Path, make_photo) -> None:
    library = _library(tmp_path, make_photo)
    library.index_photos()

    both = library.search_photos([_condition("海边"), _condition("#相册 旅行")])  # type: ignore[list-item]

    assert _names(both.refs) == ["海边-03.jpg"]


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
    """库所在卷只读时检索做不了 —— 但那是「这一件做不了」，不是「整个软件坏了」。

    分组与排序在同样情况下照常可用：它们把读到的值揣在内存里算，不需要索引。
    """
    index = FacetIndex(None)
    ref = PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=1)
    index.store([(ref, PhotoFacets(camera="Canon"))])

    with pytest.raises(IndexUnavailableError) as excinfo:
        index.search([_condition("海边")])  # type: ignore[list-item]

    assert "检索索引" in excinfo.value.message
