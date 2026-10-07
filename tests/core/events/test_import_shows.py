"""从「演迹」导出的 .xlsx 里读演出（ADR 0047）。

表格**现场造**：一个最小的 xlsx 就是两个 XML 打进一个 zip，够这一层读了。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from gallery.core.errors import ShowImportError
from gallery.core.events.import_shows import read_show_export

#: 「演迹」导出的列，顺序与真实文件一致。
HEADER = (
    "演出名称",
    "艺人",
    "嘉宾",
    "主题/剧目",
    "日期",
    "城市",
    "场馆",
    "票价",
    "实付",
    "座位",
    "标签",
    "评分",
    "演出类型",
    "购买渠道",
    "感想",
    "备注",
    "活动名称",
    "活动时间",
    "花费",
    "活动地点",
    "活动备注",
)


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_xlsx(path: Path, header: tuple[str, ...], rows: list[list[object]]) -> Path:
    """把一张表写成一个最小可读的 .xlsx（共享字符串 + 一个工作表）。"""
    shared: list[str] = []
    seen: dict[str, int] = {}

    def string_id(text: str) -> int:
        if text not in seen:
            seen[text] = len(shared)
            shared.append(text)
        return seen[text]

    def cell(reference: str, value: object) -> str:
        if value is None or value == "":
            return ""
        if isinstance(value, (int, float)):
            return f'<c r="{reference}"><v>{value}</v></c>'
        return f'<c r="{reference}" t="s"><v>{string_id(str(value))}</v></c>'

    body = []
    for number, row in enumerate([header, *rows], start=1):
        cells = "".join(cell(f"{_column(index)}{number}", value) for index, value in enumerate(row))
        body.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    strings = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{escape(text)}</t></si>" for text in shared)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", strings)
    return path


def _row(**cells: object) -> list[object]:
    """按 :data:`HEADER` 的顺序摆一行，只写关心的那几格。"""
    if "主题" in cells:
        cells["主题/剧目"] = cells.pop("主题")
    return [cells.get(name, "") for name in HEADER]


# ---- 逐列映射 -----------------------------------------------------------------


def test_it_reads_every_column_into_its_field(tmp_path: Path) -> None:
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [
            _row(
                演出名称="某演唱会",
                艺人="某乐队",
                嘉宾="某嘉宾",
                主题="某剧目",
                日期=46158.479166,
                城市="西安",
                场馆="某场馆",
                票价="280",
                实付="240",
                座位="A区1排",
                标签="摇滚",
                评分=5,
                演出类型="演唱会",
                购买渠道="大麦",
                感想="很好看",
                备注="记得带望远镜",
            )
        ],
    )

    values = read_show_export(path).drafts[0].values

    assert values["title"] == "某演唱会"
    assert values["artist"] == "某乐队"
    assert values["guests"] == "某嘉宾"
    assert values["program"] == "某剧目"
    assert values["started_on"] == "2026-05-16"
    assert values["city"] == "西安"
    assert values["venue"] == "某场馆"
    assert values["ticket_price"] == "280"
    assert values["paid"] == "240"
    assert values["seat"] == "A区1排"
    assert values["rating"] == "5"
    assert values["show_type"] == "演唱会"
    assert values["channel"] == "大麦"
    assert values["thoughts"] == "很好看"
    assert values["note"] == "记得带望远镜"
    # 「标签」那一列没有对应字段，不该漏进草稿里。
    assert "tags" not in values


def test_a_row_without_a_name_grows_one_from_program_and_date(tmp_path: Path) -> None:
    """名称栏空着时用与面板同一条自动标题：日期 + 剧目。"""
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [_row(主题="某音乐剧", 艺人="某演员", 日期=46158.479166)],
    )

    values = read_show_export(path).drafts[0].values

    assert values["title"] == "2026-05-16 · 某音乐剧"


# ---- 跳过与容错 ---------------------------------------------------------------


def test_blank_rows_are_ignored_silently(tmp_path: Path) -> None:
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [_row(), _row(演出名称="某演唱会", 日期=46158.479166), _row()],
    )

    result = read_show_export(path)

    assert [draft.values["title"] for draft in result.drafts] == ["某演唱会"]
    assert result.skipped == ()


def test_an_activity_row_is_skipped_with_a_reason(tmp_path: Path) -> None:
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [_row(活动名称="看展", 活动地点="美术馆"), _row(演出名称="某演唱会")],
    )

    result = read_show_export(path)

    assert [draft.values["title"] for draft in result.drafts] == ["某演唱会"]
    assert len(result.skipped) == 1
    assert "活动" in result.skipped[0]
    assert "2" in result.skipped[0]


def test_ratings_outside_one_to_five_are_dropped(tmp_path: Path) -> None:
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [_row(演出名称="甲", 评分=0), _row(演出名称="乙", 评分=9), _row(演出名称="丙", 评分=3)],
    )

    ratings = [draft.values["rating"] for draft in read_show_export(path).drafts]

    assert ratings == ["", "", "3"]


def test_a_date_that_is_already_text_is_kept(tmp_path: Path) -> None:
    path = _write_xlsx(tmp_path / "演出.xlsx", HEADER, [_row(演出名称="甲", 日期="2024-02-24")])

    assert read_show_export(path).drafts[0].values["started_on"] == "2024-02-24"


def test_the_header_can_sit_below_a_title_row(tmp_path: Path) -> None:
    """表头不一定是第一行 —— 往下找到第一个认得的那一行。"""
    path = _write_xlsx(
        tmp_path / "演出.xlsx",
        HEADER,
        [_row(演出名称="某演唱会", 日期=46158.479166)],
    )
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b'<row r="1">', b'<row r="1"><c r="A1" t="s"><v>99</v></c></row><row r="2">'
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)

    assert read_show_export(path).drafts[0].values["title"] == "某演唱会"


# ---- 认不出这个格式时 ---------------------------------------------------------


def test_a_missing_name_column_is_an_error(tmp_path: Path) -> None:
    path = _write_xlsx(tmp_path / "别的.xlsx", ("甲", "乙"), [["一", "二"]])

    with pytest.raises(ShowImportError):
        read_show_export(path)


def test_a_file_that_is_not_a_zip_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "不是表格.xlsx"
    path.write_text("这不是一个 zip", encoding="utf-8")

    with pytest.raises(ShowImportError):
        read_show_export(path)


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ShowImportError):
        read_show_export(tmp_path / "没有这个文件.xlsx")
