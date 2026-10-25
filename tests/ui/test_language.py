"""英文目录真的能被用上：新进程、先定语言、再导入界面模块（ADR 0106）。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from gallery.core.i18n import current_language, set_language
from gallery.core.library.session import Preferences, SessionState, save_session
from gallery.ui.app import activate_language


@pytest.fixture(autouse=True)
def _restore_language() -> Iterator[None]:
    before = current_language()
    yield
    set_language(before)


def test_a_fresh_process_reads_english_before_importing_ui_modules() -> None:
    """模块级文案常量在 import 时求值，所以顺序必须是「先定语言，再导入」。"""
    code = (
        "from gallery.core.i18n import set_language; "
        "set_language('en'); "
        "from gallery.ui.thumb_sizes import describe; "
        "from gallery.core.events.kinds import TRIP; "
        "print(describe(1)); "
        "print(TRIP.label)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert completed.stdout.splitlines() == ["Thumbnails: Medium, target 176 px", "Trip"]


def test_activate_language_reads_the_session_preference(tmp_path: Path) -> None:
    save_session(
        SessionState(preferences=Preferences(language="en")),
        base=tmp_path,
    )

    assert activate_language(tmp_path) == "en"
    assert current_language() == "en"


def test_a_broken_language_preference_falls_back_to_the_system(tmp_path: Path) -> None:
    save_session(
        SessionState(preferences=Preferences(language="klingon")),
        base=tmp_path,
    )

    assert activate_language(tmp_path) in {"zh_CN", "en"}
