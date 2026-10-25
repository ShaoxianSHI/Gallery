"""``gallery.core.metadata.failures``：**一份失败记录、一套错误说法**。

它存在的理由就是「同一件事以前五处各写一份」，所以这里测的是**统一本身**。"""

from __future__ import annotations

import errno
import shutil
from pathlib import Path

import pytest

from gallery.core.i18n import t
from gallery.core.metadata.failures import (
    SOURCE_GONE,
    Failure,
    os_reason,
    same_directory,
)
from gallery.core.metadata.transfer import Failure as TransferFailure
from gallery.core.metadata.trash import Failure as TrashFailure


def test_there_is_exactly_one_failure_type() -> None:
    """**三个模块报回来的必须是同一个类**，不是「形状相同」：否则界面要按形状分开认，
    而类型注解能骗过检查器，用起来才发现少一个字段。"""
    assert TrashFailure is Failure
    assert TransferFailure is Failure


def test_the_failure_record_always_has_a_name_and_sometimes_a_path() -> None:
    """``name`` 一定在（横幅上点名靠它）；``source`` 只在它真是磁盘上的东西时才有。"""
    at = Failure.at(Path("/库/京都/一张.jpg"), "磁盘已满，请释放空间后重试。")
    assert at.name == "一张.jpg"
    assert at.source == Path("/库/京都/一张.jpg")
    assert at.message == "磁盘已满，请释放空间后重试。"

    # 分组卡片没有路径 —— 那是事实，不该为了凑字段编一个假的。
    group = Failure(name="2024 年 5 月", message="分组不是磁盘上的东西。")
    assert group.name == "2024 年 5 月"
    assert group.source is None


@pytest.mark.parametrize(
    ("errno_value", "fragment"),
    [
        (errno.ENOSPC, "磁盘已满"),
        (errno.ENOENT, "不在原位置"),
        (errno.EACCES, "没有权限"),
        (errno.ENAMETOOLONG, "文件名过长"),
    ],
)
def test_the_common_failures_say_one_thing_each(errno_value: int, fragment: str) -> None:
    """**磁盘满了 / 东西没了 / 没权限 / 名字太长** —— 全仓库各只有一种说法。

    这四样用户控制不了，措辞不该取决于撞上哪个操作：两处各写一句，用户会以为是两个问题。"""
    assert fragment in os_reason(OSError(errno_value, "whatever"))


def test_the_permission_hint_depends_on_whether_it_is_a_folder() -> None:
    """一个只读的文件与一个读不了的文件夹，下一步要做的事不一样 —— 提示就得分开。"""
    folder = os_reason(OSError(errno.EACCES, "Permission denied"), is_dir=True)
    single = os_reason(OSError(errno.EACCES, "Permission denied"), is_dir=False)

    assert "系统设置" in folder, "文件夹读不了，通常是 macOS 的授权没给"
    assert "只读" in single
    assert folder != single


def test_the_prefix_only_goes_on_the_vague_branch() -> None:
    """操作名（「复制」「移动」）**只加在说不清原因的那一条上** —— 具体原因前加它只是啰嗦。"""
    assert os_reason(OSError(errno.ENOSPC, ""), action="复制") == os_reason(
        OSError(errno.ENOSPC, "")
    )
    vague = os_reason(OSError(errno.EXDEV, "Invalid cross-device link"), action="移动")
    assert vague.startswith("移动失败：")


def test_the_two_call_sites_of_the_gone_wording_agree() -> None:
    """``SOURCE_GONE`` 有两处用：``os_reason`` 的 ``ENOENT`` 分支，和 ``transfer`` 在
    动手前发现源压根不存在时（那时没有 ``OSError`` 可译）。**不许各写一份。**"""
    assert os_reason(OSError(errno.ENOENT, "")) == t(SOURCE_GONE)


def test_same_file_is_named_before_anything_else() -> None:
    """``shutil.SameFileError`` 是程序自己判断出来的，不是系统拒绝的 —— 单独一条。"""
    assert "同一个" in os_reason(shutil.SameFileError("same file"))


def test_same_directory_follows_symlinks(tmp_path: Path) -> None:
    """**按磁盘上的真实位置比，不比字面写法**（两个模块各有一份这个判断）。"""
    root = tmp_path
    (root / "库").mkdir()
    link = root / "链接"
    link.symlink_to(root / "库")

    assert same_directory(link, root / "库")
    assert not same_directory(link, root)
