"""``guard_within_root`` 的拒绝原因 → 每个写入口自己那句话。

这六个入口都会动用户的文件（不删除、不移动用户照片），所以「为什么不行」必须说得准 ——
而它们**按原因分支，不按消息文本**。这一份就是那条契约的回归测试：

- 每个入口的映射表必须是**全的**（``GuardReason`` 有几个成员就有几条）：少一条是
  ``KeyError``，不是一句含糊的通用话；
- 每条消息都要**点名那件事**（不能落到「反正就是不行」那种通用说法上）。

两条都验过会红：删掉一张表里的一条 → 缺项那条红；把某一条换成通用话 → 泛化那条红。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.metadata import album, paste, rename, transfer, trash, writeback

#: 六个入口 → 它们的「原因 → 那句话」映射表。
GUARDS = {
    "rename": rename._GUARD_MESSAGES,
    "trash": trash._GUARD_MESSAGES,
    "transfer": transfer._GUARD_MESSAGES,
    "paste": paste._GUARD_MESSAGES,
    "album": album._GUARD_MESSAGES,
    "writeback": writeback._GUARD_MESSAGES,
}

#: 六个 ``_reason`` 函数。
REASONS = {
    "rename": rename._rename_reason,
    "trash": trash._trash_reason,
    "transfer": transfer._transfer_reason,
    "paste": paste._paste_reason,
    "album": album._album_reason,
    "writeback": writeback._writeback_reason,
}

#: 六个会动用户文件的入口模块（不删除、不移动用户照片）。它们的 ``_guard`` 都必须走共用那个检查。
GUARD_MODULES = {
    "rename": rename,
    "trash": trash,
    "transfer": transfer,
    "paste": paste,
    "album": album,
    "writeback": writeback,
}


@pytest.mark.parametrize("name", sorted(GUARD_MODULES))
def test_every_write_guard_goes_through_the_shared_gate(name: str) -> None:
    """**六个写入口都走同一个共用检查**，没有谁自己重写一遍。

    曾经 ``writeback`` 是例外：它自己写着 ``paths.is_within`` 与
    ``path == paths.config_dir(root)``（裸比较，不是共用检查那种解析过的比较），而它的
    文档字符串说「与改名的守卫同源」。那种复制品**不会跟着共用检查一起变** —— 共用检查加一条
    判据、或把某条判据收紧时，它不会知道。

    判据是源码里**真的调了** ``guard_within_root``：这条不靠「读起来像同源」。
    """
    source = inspect.getsource(GUARD_MODULES[name]._guard)

    assert "guard_within_root(" in source, (
        f"{name}._guard 没走共用那道闸，它可能又自己重写了一份判据"
    )


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_every_guard_maps_every_reason(name: str) -> None:
    """**映射表是全的** —— 缺口是当场炸，不是静默退成通用话。

    ``GuardReason`` 加一个成员时，这五张表都得补上：不补的话用户会拿到一句与真正
    发生的事对不上的提示，而那条路径是「不删除、不移动用户照片」的硬性约束里的写操作。
    """
    table = GUARDS[name]

    assert set(table) == set(paths.GuardReason), (
        f"{name} 的映射表缺了 {sorted(r.name for r in set(paths.GuardReason) - set(table))}"
    )


@pytest.mark.parametrize("name", sorted(REASONS))
def test_every_reason_gets_a_specific_sentence(name: str) -> None:
    """每一句都要**点名那件事**，不能是「反正就是不行」。

    判据：通用那一句总会把**路径**缀进句子（「……：/某个/路径。」），而具体的那几句
    说的是原因本身。所以「句子里带着路径」就等于「这一条没说原因」。
    """
    reason_fn = REASONS[name]
    path = Path("/库里/某个相册")

    for reason in paths.GuardReason:
        message = reason_fn(path, paths.GuardError(reason, "闸门原文"))

        if reason is paths.GuardReason.OUTSIDE_ROOT:
            # 这一条本来就要把路径说出来 —— 用户得知道是哪一个路径不行。
            assert str(path) in message, f"{name} 的库外那一句没带上路径：{message}"
            continue

        assert str(path) not in message, (
            f"{name} 没为 {reason.name} 说具体原因，落到了通用那一句上：{message}"
        )
