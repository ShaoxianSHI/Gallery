"""系统那份「最近项目」（Dock 右键菜单里那一块）的登记次序与开关。

**不碰本机那份真列表**：真实现要 `NSBundle.mainBundle()` 就是 Gallery.app 才肯动，而测试进程
是裸解释器（``test_the_test_process_is_not_the_gallery_bundle`` 钉着这一条），所以这里既能拿
真 AppKit 问一遍，又能用假实现把次序量出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gallery.core.external import recent_documents


class RecordingRecents:
    """把 ``clear`` 与每一次 ``note`` 按顺序记下来。"""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def clear(self) -> None:
        self.calls.append("clear")

    def note(self, root: Path) -> None:
        self.calls.append(root)


@pytest.fixture
def three_folders(tmp_path: Path) -> tuple[Path, Path, Path]:
    """三个真实存在的目录，按「最新 → 最旧」排：``new`` / ``mid`` / ``old``。"""
    folders = tuple(tmp_path / name for name in ("new", "mid", "old"))
    for folder in folders:
        folder.mkdir()
    return folders  # type: ignore[return-value]


def test_publish_rebuilds_the_whole_list_oldest_first(
    three_folders: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """先清空、再**最旧的先登记**：系统那份只增不减，而菜单从图标往上弹。

    次序本身为什么是这样，见 ``_register_order``。
    """
    new, mid, old = three_folders
    recents = RecordingRecents()
    monkeypatch.setattr(recent_documents, "_system_recents", lambda: recents)

    recent_documents.publish((new, mid, old))  # 与 ``session.recent_roots`` 一样：新的在前

    assert recents.calls == ["clear", old, mid, new]


def test_publish_leaves_out_folders_that_are_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删掉的库不登记：系统那份会一直挂着它，点下去才报错。"""
    kept, gone = tmp_path / "在", tmp_path / "不在"
    kept.mkdir()
    recents = RecordingRecents()
    monkeypatch.setattr(recent_documents, "_system_recents", lambda: recents)

    recent_documents.publish((gone, kept))

    assert recents.calls == ["clear", kept]


def test_publish_is_a_no_op_without_pyobjc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """没装 pyobjc（非 macOS）时静默什么都不做 —— 而不是把打开库这条路弄挂。"""
    monkeypatch.setattr(recent_documents, "_appkit", lambda: None)
    root = tmp_path / "库"
    root.mkdir()

    recent_documents.publish((root,))  # 不抛就算过


def test_publish_swallows_a_failing_system_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统调用失败只该让 Dock 里少几项，不该挡着打开库。"""

    class Exploding(RecordingRecents):
        def clear(self) -> None:
            raise OSError("sharedfilelistd 不理我")

    monkeypatch.setattr(recent_documents, "_system_recents", Exploding)
    root = tmp_path / "库"
    root.mkdir()

    recent_documents.publish((root,))  # 不抛就算过


def test_the_test_process_is_not_the_gallery_bundle() -> None:
    """**测试进程登记不进去**，本机那份最近项目因此不会被测试弄脏。

    裸解释器跑起来时 ``NSBundle.mainBundle()`` 是解释器自己那个包而不是 Gallery.app，
    只有真正的 ``Gallery.app`` 才认得出 ``BUNDLE_IDENTIFIER``（ADR 0062）。
    """
    if sys.platform != "darwin":
        pytest.skip("只有 macOS 有 NSBundle")

    assert recent_documents._system_recents() is None
