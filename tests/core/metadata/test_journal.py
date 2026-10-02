"""``gallery.core.metadata.journal``：每一次会动用户文件的操作都要在这里留一笔。

它是红线 4 的落点 —— 「写不进去也不能让操作失败，但必须如实说撤销不了」。
此前这个模块**没有任何专属测试**：它只是被五个写操作的测试间接用到，而那几个测试
又从不停下来问它「你写不进去时会怎样」。这里把那几条单独钉住。

还要守住的是日志本身的两条性质：**追加写中途崩溃只丢最后一行**，以及
**跨天按时间顺序读回**（撤销栈完全靠重放它）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.metadata.journal import (
    ACTION_MOVE,
    ACTION_PASTE,
    ACTION_RENAME,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
    journal_file,
    read_records,
)


def _record(**overrides: object) -> WriteRecord:
    base: dict[str, object] = {
        "action": ACTION_RENAME,
        "source": Path("/库/一张.jpg"),
        "target": Path("/库/两张.jpg"),
        "result": RESULT_OK,
    }
    base.update(overrides)
    return WriteRecord(**base)  # type: ignore[arg-type]


# ---- 写与读 ---------------------------------------------------------------


def test_a_record_survives_a_round_trip(library: Path) -> None:
    assert append_record(library, _record()) is True

    (read,) = read_records(library)
    assert read.action == ACTION_RENAME
    assert read.source == Path("/库/一张.jpg")
    assert read.target == Path("/库/两张.jpg")
    assert read.result == RESULT_OK
    assert read.succeeded is True


def test_the_record_is_a_json_line_in_the_days_file(library: Path) -> None:
    """一行一条、落在当天的文件里 —— 崩在中途只会丢掉最后一行，已写完的仍然完好。"""
    append_record(library, _record())
    append_record(library, _record(action=ACTION_MOVE, result=RESULT_FAILED, message="磁盘满了"))

    today = journal_file(library)
    assert today.parent == paths.journal_dir(library)
    lines = today.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert all(line.startswith("{") and line.endswith("}") for line in lines)
    assert read_records(library)[1].message == "磁盘满了"


def test_chinese_paths_and_messages_round_trip(library: Path) -> None:
    """中文路径与中文提示是这行的常态（``ensure_ascii=False`` 就是为此）。"""
    append_record(
        library,
        _record(source=Path("/库/京都/一张.jpg"), message="这个名字已经被占用了。"),
    )

    (read,) = read_records(library)
    assert read.source == Path("/库/京都/一张.jpg")
    assert read.message == "这个名字已经被占用了。"


def test_a_missing_journal_directory_is_created(library: Path) -> None:
    """第一次写操作顺手把目录建起来 —— 用户不需要先跑一次什么。"""
    directory = paths.journal_dir(library)
    assert not directory.exists()

    assert append_record(library, _record()) is True
    assert directory.is_dir()


def test_an_unwritable_journal_reports_failure_instead_of_raising(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**记账失败绝不抛异常**：用户已经改好的文件不能因为记不上账就说成没改成。

    返回 ``False`` 由调用方决定要不要提示（红线 4）。
    """
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths.journal_dir(library):
            raise OSError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse, raising=True)

    assert append_record(library, _record()) is False


def test_broken_lines_are_skipped_not_fatal(library: Path) -> None:
    """日志不该把程序带崩：一行坏了，其余记录照样读得出来。"""
    append_record(library, _record())
    with journal_file(library).open("a", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON\n\n[1, 2, 3]\n")
    append_record(library, _record(action=ACTION_PASTE))

    records = read_records(library)

    assert [record.action for record in records] == [ACTION_RENAME, ACTION_PASTE]


def test_reading_without_any_journal_yields_nothing(tmp_path: Path) -> None:
    """刚打开、什么都没做过的库：读出来是空的，而不是抛异常。"""
    assert read_records(tmp_path) == []


# ---- 跨天 -----------------------------------------------------------------


def test_records_from_different_days_come_back_in_order(library: Path) -> None:
    """撤销栈完全靠重放这些记录，所以**跨天的先后顺序必须对**。

    文件名就是日期（``journal/YYYY-MM-DD.jsonl``），字典序即时间序 —— 这条性质
    没有别处盯着，而它错位一次就是「⌘Z 撤错了东西」。
    """
    today = datetime.now().astimezone()
    earlier, later = today - timedelta(days=2), today - timedelta(days=1)
    for day, action in ((earlier, ACTION_RENAME), (later, ACTION_MOVE), (today, ACTION_PASTE)):
        # 时间戳故意与文件顺序相反：排序靠的是文件名，不是记录里的时间。
        path = journal_file(library, when=day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_record(action=action, timestamp="1999-01-01T00:00:00+00:00").to_json())
            handle.write("\n")

    assert [record.action for record in read_records(library)] == [
        ACTION_RENAME,
        ACTION_MOVE,
        ACTION_PASTE,
    ]


def test_non_jsonl_files_in_the_directory_are_ignored(library: Path) -> None:
    """用户或别的工具在那个目录里放的东西不该让日志读不出来。"""
    directory = paths.journal_dir(library)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "readme.txt").write_text("这个目录是 Gallery 的写操作日志\n")
    append_record(library, _record())

    assert len(read_records(library)) == 1


# ---- 记录本身 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("{ 不是 JSON", "语法坏了"),
        ("[1, 2, 3]", "不是对象"),
        ('{"action": "rename"}', "没有 from / to"),
        ('{"from": 1, "to": 2}', "from / to 不是字符串"),
    ],
)
def test_from_json_rejects_bad_lines(payload: str, reason: str) -> None:
    assert WriteRecord.from_json(payload) is None, reason


def test_from_json_defaults_are_conservative() -> None:
    """缺字段时往「不确定」那边倒：没写结果就当没成功。"""
    record = WriteRecord.from_json('{"from": "/a.jpg", "to": "/b.jpg"}')

    assert record is not None
    assert record.action == ACTION_RENAME
    assert record.result == RESULT_FAILED
    assert record.succeeded is False
    assert record.is_dir is False
    assert record.batch is None


def test_an_empty_batch_is_treated_as_none() -> None:
    """批量号写成空串不该被当成一个真实的批。"""
    assert WriteRecord.from_json('{"from": "/a", "to": "/b", "batch": ""}').batch is None
