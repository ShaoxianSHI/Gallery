"""孤儿绑定：指向已经不存在的照片的那些绑定（ADR 0043 决策 9）。

**全部重量都在「凭什么断定它没了」上。** 绑定按路径记，而照片常在云盘、外接盘、网络共享上，
「这个路径不存在」有两种成因：照片真被删了（该清），或**那个盘没挂上 / 没权限 / 网络共享临时
不可用**（清了就是抹掉用户填的数据）。所以这里**不是一次 ``exists()``**，而是让 ``os.scandir``
报出它拿到的是权威清单还是一个错误（``None`` 表示「读不了，不知道」）。只读，不碰用户文件。
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gallery.core.events.store import EventStore, Orphan

#: 扫描期间每处理这么多条就问一次要不要停。开库时用户随时可能切走或关窗。
CANCEL_EVERY: int = 200


@dataclass(frozen=True, slots=True)
class DirListing:
    """读一个目录的结果。**三档，不是「有 / 没有」两档。**

    :attr:`names` 是那些名字；``None`` 表示**读不了** —— 那不是「这里什么都没有」，是「不知道」，
    两者在删用户数据这件事上差着天壤之别。:attr:`gone` 只在**目录确实不存在**（``ENOENT``）时为真：
    **挂在未挂载的外接盘上的相册报的也是它**。
    """

    names: frozenset[str] | None
    gone: bool = False

    @property
    def readable(self) -> bool:
        return self.names is not None


@dataclass(frozen=True, slots=True)
class OrphanScan:
    """一次检查的结果。两个集合互斥，**且都不含「不知道」的那些**。

    ``missing`` 是**目录可读**的权威结论：那个相册就在那儿，清单里没有这张照片 —— 这才是可以放心
    清掉的那一批。``vanished`` 是**父目录整个不在**：相册被删、盘没挂上都是这种，分不出来，所以
    单独一档、默认不删。**被刻意排除的**：目录存在但 ``scandir`` 报了别的错（没权限、EIO、ESTALE）。
    """

    missing: tuple[Orphan, ...] = ()
    vanished: tuple[Orphan, ...] = ()

    @property
    def found(self) -> bool:
        """有没有要报给用户的。**空的就该彻底静默** —— 不弹框、不挂横幅、不记日志。"""
        return bool(self.missing or self.vanished)

    def total(self, *, include_vanished: bool = True) -> int:
        return len(self.missing) + (len(self.vanished) if include_vanished else 0)

    def paths(self, *, include_vanished: bool = True) -> tuple[Path, ...]:
        return tuple(orphan.path for orphan in self.chosen(include_vanished=include_vanished))

    def chosen(self, *, include_vanished: bool) -> tuple[Orphan, ...]:
        return self.missing + (self.vanished if include_vanished else ())

    def event_titles(self, *, include_vanished: bool = True, only: str = "") -> tuple[str, ...]:
        """涉及哪些事件，按首次出现顺序去重 —— 提示框里列给人看。

        ``only`` 收窄到某一档（``"missing"`` / ``"vanished"``）—— **两个档常常是两批不同的
        事件**，混着列会让人以为同一个事件的照片既有没了的、又有找不到相册的。
        """
        if only == "missing":
            source: tuple[Orphan, ...] = self.missing
        elif only == "vanished":
            source = self.vanished
        else:
            source = self.chosen(include_vanished=include_vanished)
        seen: dict[str, None] = {}
        for orphan in source:
            seen.setdefault(orphan.event_title or "未命名", None)
        return tuple(seen)


def find_orphans(
    events: EventStore | None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    read: Callable[[Path], DirListing] | None = None,
) -> OrphanScan:
    """哪些绑定指向了已经不存在的照片。
    :param events: 事件库。``None`` 或不可用时**什么都不查**。
    :param should_cancel: 每 :data:`CANCEL_EVERY` 条问一次；**查完之前不该有任何可见动作**。
    :param read: 读一个目录。默认 :func:`_read_directory`；**测试注入它**才能造出「没权限」。
    **库根不可读就整个不查**；**一条绑定都没有就一个目录都不问**。
    """
    if events is None or not events.available:
        return OrphanScan()
    bindings = events.all_bindings()
    if not bindings:
        return OrphanScan()
    reader = read if read is not None else _read_directory
    # **先问库根**：读不了就整个不查 —— 那说明这个库整体有问题，此刻报出来的每一条都可能是假的。
    root_listing = reader(events.root)
    if not root_listing.readable:
        return OrphanScan()

    missing: list[Orphan] = []
    vanished: list[Orphan] = []
    #: 父目录 → 那一组绑定。**父目录每条只算一次**（``Path.parent`` 不是属性访问，是每次现造
    #: 一个 ``Path`` 对象，写在循环里就是每行造一遍）。
    grouped: dict[Path, list[Orphan]] = {}
    for index, orphan in enumerate(bindings):
        if (
            index % CANCEL_EVERY == CANCEL_EVERY - 1
            and should_cancel is not None
            and should_cancel()
        ):
            return OrphanScan()
        grouped.setdefault(orphan.path.parent, []).append(orphan)

    for directory, group in grouped.items():
        # 库根刚问过，**别再问一次**。
        listing = root_listing if directory == events.root else reader(directory)
        names = listing.names
        if names is None:
            if not listing.gone:
                continue  # 读不了：不知道，不报（空集与 None 的分别就在这一句）
            vanished.extend(group)  # 目录整个不在 —— 单独一档，默认不删
            continue
        missing.extend(orphan for orphan in group if orphan.path.name not in names)
    return OrphanScan(missing=tuple(missing), vanished=tuple(vanished))


def forget_orphans(events: EventStore | None, scan: OrphanScan, *, include_vanished: bool) -> int:
    """把用户点头要清的那些绑定删掉，返回删了几条。

    **按 path 删就够，不必按 ``(path, event_id)``**：照片没了，它身上每一条绑定都是孤儿。
    **不上撤销栈**（ADR 0043 决策 5）：这里**没有有意义的反向动作** —— 把绑定恢复到一张
    不存在的照片上，用户用不上。
    """
    if events is None or not events.available:
        return 0
    return events.forget_bindings(scan.paths(include_vanished=include_vanished))


def _read_directory(directory: Path) -> DirListing:
    """读一个目录。**``ENOENT`` 与其它错误码必须分开**：

    ``ENOENT`` 说的是「那儿没有这个东西」，那是个**结论**；``EACCES`` / ``EIO`` / ``ESTALE``
    说的是「我不知道」，那只是缺信息。把后两者当成前者，就是在用户没权限或盘没挂上的时候
    把他自己填的数据清掉 —— 这是这个模块存在的全部理由。
    """
    try:
        with os.scandir(directory) as entries:
            return DirListing(names=frozenset(entry.name for entry in entries))
    except OSError as error:
        return DirListing(names=None, gone=error.errno == errno.ENOENT)


__all__ = [
    "CANCEL_EVERY",
    "DirListing",
    "OrphanScan",
    "find_orphans",
    "forget_orphans",
]
