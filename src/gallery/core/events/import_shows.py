"""从「演迹」导出的 ``.xlsx`` 里读出演出，翻成一批 :class:`EventDraft`（ADR 0047）。

**纯解析**：谁去建事件由 :meth:`~gallery.core.library.facade.Library.import_shows` 决定 ——
这一层不碰事件库、不碰 Qt，能直接单测。xlsx 用标准库的 ``zipfile`` + ``xml.etree`` 读，
不为这件小事引第三方依赖。**少一列不算错**，但连 ``演出名称`` 都没有就说明不是这个格式。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree

from gallery.core.errors import ShowImportError
from gallery.core.events.kinds import RATING_MAX, RATING_MIN, SHOW, normalize_day, suggested_title
from gallery.core.events.models import EventDraft

#: xlsx 里每张 XML 都挂的名字空间。
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: 工作表文件的形状（只有一个工作表，取编号最小的那个）。
_SHEET = re.compile(r"xl/worksheets/sheet(\d+)\.xml\Z")

#: Excel 的日期原点。串号 1 是 1900-01-01，而 Excel 把 1900 当闰年，于是往回收两天。
_EPOCH = date(1899, 12, 30)

#: 表头 → 事件字段 id。**「标签」那一列故意不在里面**：演出这一类没有放它的字段。
_COLUMNS: dict[str, str] = {
    "演出名称": "title",
    "艺人": "artist",
    "嘉宾": "guests",
    "主题/剧目": "program",
    "日期": "started_on",
    "城市": "city",
    "场馆": "venue",
    "票价": "ticket_price",
    "实付": "paid",
    "座位": "seat",
    "评分": "rating",
    "演出类型": "show_type",
    "购买渠道": "channel",
    "感想": "thoughts",
    "备注": "note",
}

#: 没有这一列就不是「演迹」的演出列表。
_REQUIRED_HEADERS: tuple[str, ...] = ("演出名称",)

#: 「活动」那几列：一行只有它们时，那是别的东西，不是演出。
_ACTIVITY_HEADERS: frozenset[str] = frozenset({"活动名称", "活动地点", "活动时间"})

#: 判断「这一行到底算不算一场演出」的那几栏，全是空就跳过。
_IDENTITY: tuple[str, ...] = ("title", "program", "artist")


@dataclass(frozen=True, slots=True)
class ShowImport:
    """一张表格读出来的东西。

    ``skipped`` 是**一行一句、可以直接念给用户听的**跳过说明（含行号与原因）；空行不进这里
    —— 那是排版留下的，不是用户漏掉的一场演出。
    """

    drafts: tuple[EventDraft, ...]
    skipped: tuple[str, ...] = ()


def show_identity(title: str, started_on: str, venue: str) -> tuple[str, str, str]:
    """判重键：**名称 + 日期 + 场馆**（各自去掉首尾空白）。

    导出的行里没有一个稳定唯一的 id，判重只能靠内容。这三样一起相同基本就是同一场
    （同名同日同场馆的两场演出几乎不存在），代价是极端情况下真有两场会被当成一场 ——
    所以界面会在写库之前把这件事问一句（ADR 0047）。
    """
    return (title.strip(), started_on.strip(), venue.strip())


def read_show_export(path: Path) -> ShowImport:
    """读一张「演迹」导出的 ``.xlsx``，返回里面那批演出草稿。

    :raises ShowImportError: 文件不在、打不开、不是 ``.xlsx``，或者看起来不是演出列表。
    """
    path = Path(path)
    grid = _cell_rows(path)
    header = _find_header(grid)
    if header is None:
        raise ShowImportError(
            "这个表格里找不到「演出名称」那一列，看起来不是「演迹」导出的演出列表。",
            path=path,
        )
    index, head = header
    letter_for: dict[str, str] = {}
    for letter, name in head.items():
        field = _COLUMNS.get(name.strip())
        if field is not None and field not in letter_for:
            letter_for[field] = letter
    activity_letters = {
        letter for letter, name in head.items() if name.strip() in _ACTIVITY_HEADERS
    }

    drafts: list[EventDraft] = []
    skipped: list[str] = []
    for number, cells in grid[index + 1 :]:
        row = {field: cells.get(letter, "").strip() for field, letter in letter_for.items()}
        draft = _draft_of(row)
        if draft is not None:
            drafts.append(draft)
            continue
        if not any(row.values()) and not _has_activity(cells, activity_letters):
            continue
        skipped.append(_skip_note(number, cells, activity_letters))
    return ShowImport(tuple(drafts), tuple(skipped))


def _draft_of(row: dict[str, str]) -> EventDraft | None:
    """一行数据 → 一份演出草稿；没有演出身份的那几栏就给 ``None``。"""
    if not any(row.get(field) for field in _IDENTITY):
        return None
    values = dict(row)
    values["started_on"] = _excel_day(row.get("started_on", ""))
    values["rating"] = _rating(row.get("rating", ""))
    if not values.get("title"):
        values["title"] = suggested_title(SHOW, values)
    return EventDraft(values=values)


def _skip_note(number: int, cells: dict[str, str], activity_letters: set[str]) -> str:
    """这一行为什么没被当成演出 —— 分「是活动」与「什么都没有」两种说法。"""
    if _has_activity(cells, activity_letters):
        return f"第 {number} 行看起来是活动，不是演出，已经跳过。"
    return f"第 {number} 行没有演出名称，已经跳过。"


def _has_activity(cells: dict[str, str], activity_letters: set[str]) -> bool:
    """这一行填的是「活动」那几列吗。"""
    return any(cells.get(letter, "").strip() for letter in activity_letters)


def _excel_day(text: str) -> str:
    """日期那一格：Excel 把它存成从 1900 年起的串号，也可能直接是日期文字。

    串号的小数部分是导出里那个 UTC 时刻，**本层刻意只取整天**：表格自己显示的就是日期，
    而把时刻还原成本地时间要先假定一个时区 —— 猜错的时刻比留空更糟（ADR 0047）。
    """
    text = text.strip()
    if not text:
        return ""
    try:
        serial = float(text)
    except ValueError:
        return normalize_day(text)
    return (_EPOCH + timedelta(days=int(serial))).isoformat()


def _rating(text: str) -> str:
    """评分那一格：只认 1–5，别的一律当没填（``0`` 常用来表示没评分）。"""
    try:
        score = int(float(text.strip()))
    except ValueError:
        return ""
    return str(score) if RATING_MIN <= score <= RATING_MAX else ""


def _cell_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    """整张工作表的 ``(行号, {列字母: 文字})``。

    读不出来的三种情形（文件不在、不是 zip、里面没有工作表）在这里各给一句中文。
    """
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            sheet = _sheet_xml(archive)
    except FileNotFoundError as exc:
        raise ShowImportError(f"找不到这个文件：{path.name}。", path=path, detail=str(exc)) from exc
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ShowImportError(
            f"「{path.name}」不是「演迹」导出的 .xlsx 表格。", path=path, detail=str(exc)
        ) from exc

    rows: list[tuple[int, dict[str, str]]] = []
    root = ElementTree.fromstring(sheet)
    for index, row in enumerate(root.iter(f"{_MAIN}row"), start=1):
        number = int(row.get("r") or index)
        cells: dict[str, str] = {}
        for cell in row.iter(f"{_MAIN}c"):
            letter = (cell.get("r") or "").rstrip("0123456789")
            if letter:
                cells[letter] = _cell_text(cell, shared)
        rows.append((number, cells))
    return rows


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    """共享字符串表。没有它（全内联的写法）就是空的，不是错。"""
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return ()
    root = ElementTree.fromstring(raw)
    return tuple(
        "".join(node.text or "" for node in item.iter(f"{_MAIN}t"))
        for item in root.iter(f"{_MAIN}si")
    )


def _sheet_xml(archive: zipfile.ZipFile) -> bytes:
    """编号最小的那张工作表 —— 导出的工作簿只有一张。"""
    sheets = sorted(
        (int(match.group(1)), name)
        for name in archive.namelist()
        if (match := _SHEET.fullmatch(name)) is not None
    )
    if not sheets:
        raise KeyError("工作簿里没有工作表")
    return archive.read(sheets[0][1])


def _cell_text(cell: ElementTree.Element, shared: tuple[str, ...]) -> str:
    """一个格子的文字：共享字符串取下标，内联字符串直接拼，其余读 ``<v>``。"""
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{_MAIN}t"))
    value = cell.find(f"{_MAIN}v")
    text = "" if value is None or value.text is None else value.text
    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    return text


def _find_header(grid: list[tuple[int, dict[str, str]]]) -> tuple[int, dict[str, str]] | None:
    """表头那一行：**从上往下找第一个认得出的**，返回它的下标与「列字母 → 表头」。"""
    for index, (_, cells) in enumerate(grid):
        names = {value.strip() for value in cells.values()}
        if all(required in names for required in _REQUIRED_HEADERS):
            return index, cells
    return None


__all__ = ["ShowImport", "read_show_export", "show_identity"]
