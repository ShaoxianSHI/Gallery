"""应用日志：从访达启动的 ``.app`` 没有终端，stderr 等于丢进虚空（ADR 0064）。

所以「详情已写入日志」这句话必须真的落在一个文件里，而且**不许因为写不下去就把应用拖住**。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.logs import configure_logging

#: 处理器上的记号（与 ``logs._HANDLER_MARK`` 同一处约定：认出自家的那两个）。
_MARK = "gallery-log"


def _drop_our_handlers() -> None:
    """把自家装的处理器摘掉：它们挂在**全局**的根 logger 上，不摘干净用例之间会互相串。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, None) is not None:
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def clean_handlers() -> Iterator[None]:
    _drop_our_handlers()
    yield
    _drop_our_handlers()


def test_a_log_line_lands_in_the_app_log_file() -> None:
    target = configure_logging()

    assert target == paths.app_log_file()
    logging.getLogger("gallery.test").error("写一句看看")

    assert target is not None
    assert "写一句看看" in target.read_text(encoding="utf-8")


def test_the_traceback_of_a_failure_lands_in_the_file() -> None:
    """界面那句「详情写进了日志文件」承诺的是**带堆栈的详情**，不是一行消息。"""
    target = configure_logging()

    try:
        raise ValueError("出事了")
    except ValueError:
        logging.getLogger("gallery.test").exception("后台任务抛出未预期异常")

    assert target is not None
    text = target.read_text(encoding="utf-8")
    assert "后台任务抛出未预期异常" in text
    assert "ValueError: 出事了" in text


def test_configuring_twice_does_not_stack_handlers() -> None:
    """重复调用只补缺的那一个 —— 叠上两个处理器，每一行日志都会写两遍。"""
    configure_logging()
    once = len(logging.getLogger().handlers)

    configure_logging()

    assert len(logging.getLogger().handlers) == once


def test_an_unwritable_log_file_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """装不上文件处理器时返回 ``None``，**不抛也不报** —— 记不成日志是小事，起不来是大事。"""
    blocker = tmp_path / "这不是目录"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        paths, "app_log_file", lambda **_: blocker / "logs" / "gallery.log"
    )

    assert configure_logging() is None
