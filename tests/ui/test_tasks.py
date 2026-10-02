"""``gallery.ui.tasks`` 的线程数与取消令牌（ADR 0015 第 4 节）。

线程数这一条值得钉住，是因为它曾经被拍成 4 —— 而 Pillow 解码会释放 GIL，
4 线程在 8 核以上的机器上就是白白闲置了一半核心（实测 384 → 493 张/秒）。
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from gallery.ui.tasks import MAX_THREADS, CancellationToken, default_thread_count  # noqa: E402


def test_leaves_a_core_for_the_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 10)

    assert default_thread_count() == 9


def test_uses_more_than_four_threads_on_a_big_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """这条钉的是「别再退回 4」—— 那是在没有实测数据时定的。"""
    monkeypatch.setattr(os, "cpu_count", lambda: 10)

    assert default_thread_count() > 4


def test_never_exceeds_the_physical_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 2)

    assert default_thread_count() == 1


def test_survives_a_machine_that_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)

    assert default_thread_count() >= 1


def test_an_absurd_core_count_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 4096)

    assert default_thread_count() == MAX_THREADS


def test_a_cancelled_token_reads_as_cancelled() -> None:
    token = CancellationToken()
    assert token.is_cancelled is False

    token.cancel()

    assert token.is_cancelled is True
