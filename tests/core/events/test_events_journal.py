"""事件的 journal 与撤销（ADR 0043 决策 5）。"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.events import store as store_module
from gallery.core.events.store import EventDraft, EventStore
from gallery.core.metadata.journal import (
    ACTION_EVENT,
    ACTION_EVENT_CREATE,
    ACTION_EVENT_DELETE,
    ACTION_UNDO_EVENT,
    WriteRecord,
    read_records,
)
from gallery.core.metadata.undo import (
    UndoResult,
    next_undo_label,
    pending_undo_runs,
    undo_last_write,
)

_TITLE = "京都五日"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    attached = store_module.attach(connection, threading.Lock(), root=tmp_path)
    assert attached is not None
    try:
        yield attached
    finally:
        connection.close()


def _create(store: EventStore, **values: str) -> int:
    return store.create("trip", EventDraft(values={"title": _TITLE, **values})).event.id


def _undo(store: EventStore, root: Path) -> UndoResult:
    result = undo_last_write(root, events=store)

    assert result is not None
    return result


# ---- journal -----------------------------------------------------------------


def test_a_new_event_is_journaled_but_never_undoable(store: EventStore, tmp_path: Path) -> None:
    """新建留一条记录（可追溯），但**不进撤销栈**：撤销它就是删，而刚建的那一刻里面还没有东西。"""
    _create(store)

    records = read_records(tmp_path)

    assert [record.action for record in records] == [ACTION_EVENT_CREATE]
    assert pending_undo_runs(tmp_path) == []


def test_an_edit_and_a_delete_are_journaled_with_their_old_values(
    store: EventStore, tmp_path: Path
) -> None:
    """撤销靠的就是原值，所以「原样是什么」必须真的记在 journal 里，而不是只记「改过」。"""
    event_id = _create(store, destination="京都", cost="3280.50")
    original = store.event(event_id)
    assert original is not None
    store.update(event_id, EventDraft(values={"title": _TITLE, "destination": "大阪"}))
    store.delete(event_id)

    edit, removal = read_records(tmp_path)[1:]

    assert edit.action == ACTION_EVENT
    edited = {change.tag: (change.before, change.after) for change in edit.changes}
    assert edited["destination"] == ("京都", "大阪")
    # **原值是按文字记的**（`FieldChange` 只存字符串），回来时过一遍与新建同一条归一 ——
    # 于是「3,280.50 存成 3280.5」这条归一只有一处。
    assert edited["cost"] == ("3280.5", None)

    removed = {change.tag: change.before for change in removal.changes}
    assert removal.action == ACTION_EVENT_DELETE
    # 删除记的是**删之前**的样子 —— 那次改动把扩展表整行覆盖过，于是 cost 那时已经是空的了。
    assert removed["destination"] == "大阪"
    assert removed["cost"] is None
    assert removed["title"] == _TITLE


def test_a_deleted_event_carries_its_bindings_into_the_journal(
    store: EventStore, tmp_path: Path
) -> None:
    """删除的前像必须含绑定清单 —— 只恢复事件本体的话照片关联就静默没了（ADR 0043 决策 5）。"""
    event_id = _create(store)
    photos = [Path("/库/a.jpg"), Path("/库/b.jpg")]
    store.bind(photos, event_id)

    store.delete(event_id)

    removal = read_records(tmp_path)[-1]
    assert removal.payload is not None
    assert sorted(json.loads(removal.payload)["bindings"]) == sorted(str(p) for p in photos)


def test_deleting_takes_the_extension_row_with_it(store: EventStore) -> None:
    """扩展表那一行**不能留成孤儿**：它会让撤销撞上 ``event_id`` 的主键约束。

    （同一条理由适用于绑定：删除的三张表各删各的，正确性不指望 ``ON DELETE CASCADE`` ——
    SQLite 的 ``foreign_keys`` 默认是关的。）
    """
    event_id = _create(store, destination="京都")
    store.delete(event_id)

    leftover = (
        store._require()
        .execute(
            f"SELECT COUNT(*) FROM {store_module.SCHEMA_NAME}.event_trip WHERE event_id = ?",
            (event_id,),
        )
        .fetchone()[0]
    )

    assert leftover == 0


def test_a_failure_to_journal_does_not_fail_the_write(
    store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户填好的东西不能因为记不上账就被说成没改成（与其余写操作同一条规矩）。"""
    monkeypatch.setattr(store_module, "append_record", lambda *_args: False)

    outcome = store.create("trip", EventDraft(values={"title": _TITLE}))

    assert outcome.journaled is False
    assert store.event(outcome.event.id) is not None


# ---- 撤销 --------------------------------------------------------------------


def test_an_edit_can_be_undone_field_by_field(store: EventStore, tmp_path: Path) -> None:
    """改与删都能撤：写回原值，而新的那些值也回到改之前。"""
    event_id = _create(store, destination="京都", rating="5")
    store.update(
        event_id,
        EventDraft(values={"title": "改了名字", "destination": "大阪", "cost": "100"}),
    )

    result = _undo(store, tmp_path)

    assert result.action == ACTION_EVENT
    restored = store.event(event_id)
    assert restored is not None
    assert restored.title == _TITLE
    assert restored.text("destination") == "京都"
    assert restored.text("rating") == "5"
    assert restored.text("cost") == ""


def test_a_deleted_event_comes_back_with_its_bindings(store: EventStore, tmp_path: Path) -> None:
    """撤销删除：事件、类型独有的字段、绑定三样都得回来。"""
    event_id = _create(store, destination="京都", started_on="2026-05-16")
    photos = [Path("/库/a.jpg"), Path("/库/b.jpg")]
    store.bind(photos, event_id)
    store.delete(event_id)

    _undo(store, tmp_path)

    restored = store.event(event_id)
    assert restored is not None
    assert restored.id == event_id
    assert restored.text("destination") == "京都"
    assert restored.started_on == "2026-05-16"
    assert set(store.photos_of_event(event_id)) == set(photos)


def test_the_id_is_reserved_while_the_event_is_deleted(store: EventStore, tmp_path: Path) -> None:
    """删掉之后新建的那一条不会抢走旧 id —— 否则 ⌘Z 会把刚建的那个覆盖掉。

    ``AUTOINCREMENT`` 保证这一点；这条用例把它钉在行为上（光有建表语句里的关键字不够，
    将来谁把它改成 ``INTEGER PRIMARY KEY`` 得有测试变红）。
    """
    first = _create(store, title="先建的")
    store.delete(first)
    second = _create(store, title="后建的")

    _undo(store, tmp_path)

    assert store.event(first) is not None
    assert store.event(first).title == "先建的"  # type: ignore[union-attr]
    assert store.event(second).title == "后建的"  # type: ignore[union-attr]


def test_one_edit_is_worth_exactly_one_undo(store: EventStore, tmp_path: Path) -> None:
    """一次编辑（公共列 + 扩展表）只值一次 ⌘Z —— 分两次记的话用户要按两下。"""
    event_id = _create(store)
    store.update(event_id, EventDraft(values={"title": _TITLE, "destination": "大阪"}))

    assert next_undo_label(tmp_path) == "修改事件"
    _undo(store, tmp_path)

    # 栈上只剩「新建」那一条，而它不入栈 —— 于是没什么可撤的了。
    assert next_undo_label(tmp_path) is None


def test_the_undo_itself_is_journaled(store: EventStore, tmp_path: Path) -> None:
    """撤销也要留一条（弹栈靠它）—— 否则 ⌘Z 会一直撤同一次改动。"""
    event_id = _create(store)
    store.update(event_id, EventDraft(values={"title": "改了"}))

    _undo(store, tmp_path)

    assert read_records(tmp_path)[-1].action == ACTION_UNDO_EVENT


def test_the_menu_says_which_kind_of_event_change_is_on_top(
    store: EventStore, tmp_path: Path
) -> None:
    """⌘Z 的菜单文案要说清撤的是「删除事件」还是「修改事件」。"""
    event_id = _create(store)
    store.delete(event_id)

    assert next_undo_label(tmp_path) == "删除事件"


def test_a_record_nobody_can_read_back_says_so(tmp_path: Path) -> None:
    """日志被截断 / 手改坏时如实说一句，而不是当作「没什么可撤的」。"""
    store = EventStore(tmp_path)
    record = WriteRecord(
        action=ACTION_EVENT,
        source=paths.events_file(tmp_path),
        target=paths.events_file(tmp_path),
        result="ok",
        payload="{ 这不是 JSON",
    )

    outcome = store.undo_record(record)

    assert outcome.message is not None
    assert "读不出来" in outcome.message


def test_an_event_record_never_falls_through_to_the_trash(
    store: EventStore, tmp_path: Path
) -> None:
    """**落到回退那一条就是拿 ``.gallery/events.db`` 去系统废纸篓里捞东西。**

    回退分支的 ``_undo_delete`` 只认「这是删了一个文件」，而事件记录不是。所以
    ``undo_last_write`` 必须显式分派它 —— 这条用例在事件库不可用时也断同一件事：
    报出来的是「打不开事件库」，而不是「废纸篓里已经没有它了」。
    """
    event_id = _create(store)
    store.delete(event_id)

    result = undo_last_write(tmp_path, events=EventStore(tmp_path))

    assert result is not None
    assert result.failures
    assert "事件库" in result.failures[0].message
    assert "废纸篓" not in result.failures[0].message
