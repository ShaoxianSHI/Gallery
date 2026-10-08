"""「我现在在哪一层」—— 一条栈，而不是五个各自为政的字段（ADR 0041 决策 1）。

**这一层模型一句话**：「我在哪」是一条栈，栈深 > 1 就退得回去，退一层就是弹一个帧；加一种新
层级不用改任何判据。**栈底那一帧决定浏览方式**（不是独立字段 ``_browse_mode``）：全部照片只有
根帧，相册视图在根帧之上压若干「相册」，日历视图压「日历格」。这两条链**互斥** —— 日历格下面
不可能是相册，不是靠 ``if`` 判掉的，是类型上就排除了。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from gallery.core.library.photos import GroupRef, PhotoRef

__all__ = [
    "FRAME_KINDS",
    "AlbumFrame",
    "CalendarFrame",
    "Frame",
    "GroupFrame",
    "LevelStack",
    "PageRole",
    "PhotoFrame",
    "RootKind",
    "SearchFrame",
    "browse_mode_of",
    "label_of",
    "page_role_of",
    "root_for",
]


class RootKind(Enum):
    """栈底那一帧 —— **浏览方式由它决定**，不是独立字段。"""

    FLAT = "flat"
    ALBUMS = "albums"
    CALENDAR = "calendar"


def root_for(browse_mode: str) -> RootKind:
    """浏览方式那三个字符串常量 → 栈底。

    **双向映射写在这一个模块里**，因为「浏览方式」是 ``core`` 的概念而「栈底」是界面的概念，
    两边对同一件事各说一遍就会差一档（那正是 ``_browse_mode`` 那个属性的由来 —— 它有 25 处读）。
    """
    from gallery.core.library.config import (
        BROWSE_MODE_ALBUMS,
        BROWSE_MODE_CALENDAR,
        BROWSE_MODE_FLAT,
    )

    return {
        BROWSE_MODE_FLAT: RootKind.FLAT,
        BROWSE_MODE_ALBUMS: RootKind.ALBUMS,
        BROWSE_MODE_CALENDAR: RootKind.CALENDAR,
    }.get(browse_mode, RootKind.FLAT)


def browse_mode_of(root: RootKind) -> str:
    """栈底 → 浏览方式那三个字符串常量（:func:`root_for` 的反向）。"""
    from gallery.core.library.config import (
        BROWSE_MODE_ALBUMS,
        BROWSE_MODE_CALENDAR,
        BROWSE_MODE_FLAT,
    )

    return {
        RootKind.FLAT: BROWSE_MODE_FLAT,
        RootKind.ALBUMS: BROWSE_MODE_ALBUMS,
        RootKind.CALENDAR: BROWSE_MODE_CALENDAR,
    }[root]


@dataclass(frozen=True, slots=True)
class AlbumFrame:
    """一个相册（磁盘上的子目录）。"""

    path: Path


@dataclass(frozen=True, slots=True)
class GroupFrame:
    """一个分组（按元数据字段归的一堆照片，**不在磁盘上**）。"""

    group: GroupRef


@dataclass(frozen=True, slots=True)
class CalendarFrame:
    """日历上点开的那一格。**照片一起存**：那一格展开的内容就是它，``photos`` 为空表示这一格一张
    照片也没有（点开是合法动作，只是没东西可看）。

    :param expanded: 这一格**在屏幕上展开成网格**了吗。多张的那一格有（它自己就是一层，退回来停在
        它上面）；只有一张的没有，那一次是「日历格 + 照片」一起进、一起出的一对，屏幕上从没出现过
        网格。**界面画哪一页与 ``Esc`` 退到哪一层都从这一位看**。"""

    photos: tuple[PhotoRef, ...]
    label: str
    expanded: bool = True


@dataclass(frozen=True, slots=True)
class SearchFrame:
    """搜索结果 —— **整库检索的结果就是一层**，与相册 / 分组同一性质（ADR 0026 决策 7）。

    筛选（当前层收窄）**不是这种帧**，它是某一帧自己的属性，见 :attr:`LevelStack.narrowing`。
    """

    conditions: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class PhotoFrame:
    """大图页上正看着的那一张。"""

    path: Path


#: 栈上的帧一共这五种（不含根帧）。写成元组而不是 ``set``：要的是**顺序稳定**，而它
#: 会被用来生成界面文案。
FRAME_KINDS: Final[tuple[type, ...]] = (
    AlbumFrame,
    GroupFrame,
    CalendarFrame,
    SearchFrame,
    PhotoFrame,
)

Frame = AlbumFrame | GroupFrame | CalendarFrame | SearchFrame | PhotoFrame


@dataclass(frozen=True, slots=True)
class LevelStack:
    """整条栈。**不可变**：每一处改动都返回新的一份，所以「我改了哪一帧」一眼看得出来。

    :param root: 栈底那一种浏览方式。:param frames: 根帧**之上**的帧，由底到顶。
    :param narrowing: 各帧各自那份**筛选条件**（键是帧在 ``frames`` 里的下标）。

    **筛选随帧存而不是全局一份**（ADR 0041 决策 3）：收窄出来的条件属于那一帧，弹回它时恢复。"""

    root: RootKind = RootKind.FLAT
    frames: tuple[Frame, ...] = ()
    narrowing: tuple[tuple[object, ...], ...] = ()

    def __post_init__(self) -> None:
        # narrowing 与 frames 一一对应。**在这里断**而不是靠每个调用点记得传对 —— 少传一个，
        # 筛选就会在弹掉某一帧之后莫名其妙留在别的帧上。
        if len(self.narrowing) != len(self.frames):
            raise ValueError(
                f"narrowing 有 {len(self.narrowing)} 项而 frames 有 {len(self.frames)} 帧，"
                "两者必须一一对应"
            )

    # ---- 读 ----------------------------------------------------------------

    @property
    def top(self) -> Frame | None:
        """栈顶那一帧（只有根帧时是 ``None``）。"""
        return self.frames[-1] if self.frames else None

    @property
    def depth(self) -> int:
        """栈深。**> 1 就是退得回去** —— ``_can_go_up`` 归它。"""
        return len(self.frames)

    def can_pop(self) -> bool:
        """现在退不退得回去。**一处判据**，加新层级不用改。"""
        return bool(self.frames)

    def narrowing_at(self, index: int) -> tuple[object, ...]:
        """第 ``index`` 帧自己那份筛选条件。"""
        return self.narrowing[index]

    @property
    def narrowing_top(self) -> tuple[object, ...]:
        """栈顶那一帧的筛选条件（栈底时为空）。"""
        return self.narrowing[-1] if self.narrowing else ()

    # ---- 写（每一处都是返回新的一份） ----------------------------------------

    def push(self, frame: Frame, narrowing: tuple[object, ...] = ()) -> LevelStack:
        """压一帧。**替换**栈顶同一类的那一帧 —— 见 :meth:`replace_top`。

        压照片帧走的是 :meth:`replace_top` 而不是这里：在同一帧里翻上下一张**不是**往深里走。
        """
        return LevelStack(
            root=self.root,
            frames=(*self.frames, frame),
            narrowing=(*self.narrowing, narrowing),
        )

    def replace_top(self, frame: Frame, narrowing: tuple[object, ...] = ()) -> LevelStack:
        """**换掉**栈顶那一帧（不加深）。

        给两类动作用：在同一层里翻上下一张照片（往深里走会累积成一长串栈，``Esc`` 要按几十次才
        退得出去）；以及把「照片」帧换掉（开大图时若已经在看另一张，就地换而不是叠）。
        """
        if not self.frames:
            return self.push(frame, narrowing)
        return LevelStack(
            root=self.root,
            frames=(*self.frames[:-1], frame),
            narrowing=(*self.narrowing[:-1], narrowing),
        )

    def pop(self) -> LevelStack:
        """弹掉栈顶一帧。**只有根帧时原样返回** —— 退不动就安静地不动（ADR 0040 决策 11）。

        **弹掉的那一帧不再出现在结果里**，它的筛选条件也随之消失 —— 这就是「筛选随帧走」：
        从搜索结果弹回相册时，那一帧上的筛选恢复成压入之前的样子。
        """
        if not self.frames:
            return self
        return LevelStack(root=self.root, frames=self.frames[:-1], narrowing=self.narrowing[:-1])

    def pop_until(self, predicate: object) -> LevelStack:
        """连续弹到 ``predicate(stack)`` 为真为止（弹到根帧就停）。

        给「退到相册视图的根」这类动作：日历格 + 分组 + 照片三帧要一次退干净。
        """
        stack = self
        while stack.can_pop() and not predicate(stack):  # type: ignore[operator]
            stack = stack.pop()
        return stack

    def without_frame(self, index: int) -> LevelStack:
        """**摘掉第 ``index`` 帧**（不在栈顶的那一帧），其余原样保留 —— 筛选条件跟着各自走。

        与 :meth:`pop` / :meth:`pop_until` 的区别在此：那些只能从栈顶弹，摘不掉被压在下面的帧。
        要它的是「日历格 + 其上的搜索帧」：检索不该让日历格继续算作「上一层」，但搜索帧不能动
        （动它就留下「条件还在、栈上却没有搜索帧」的矛盾）。``index`` 是 ``frames`` 里的下标，
        越界抛 ``IndexError``（与 :meth:`with_narrowing` 一致，不静默当成「没找到」）。"""
        if not 0 <= index < len(self.frames):
            raise IndexError(f"没有第 {index} 帧（栈里有 {len(self.frames)} 帧）")
        return LevelStack(
            root=self.root,
            frames=(*self.frames[:index], *self.frames[index + 1 :]),
            narrowing=(*self.narrowing[:index], *self.narrowing[index + 1 :]),
        )

    def with_narrowing(self, index: int, conditions: tuple[object, ...]) -> LevelStack:
        """换掉第 ``index`` 帧那份筛选条件。"""
        if not 0 <= index < len(self.narrowing):
            raise IndexError(f"没有第 {index} 帧（栈里有 {len(self.frames)} 帧）")
        return LevelStack(
            root=self.root,
            frames=self.frames,
            narrowing=(*self.narrowing[:index], conditions, *self.narrowing[index + 1 :]),
        )

    def with_root(self, root: RootKind) -> LevelStack:
        """换栈底 —— **顺带清空上面的一切**（换浏览方式就是「清栈换根」）。

        清空是有意的：用户在一个浏览方式里待了很久，切走再切回来并不见得想回到刚才那个位置
        （他这趟去另一个浏览方式干的事，多半与刚才那个位置无关）。落在各自底部、不带任何残留
        才是可预期的 —— 留着相册链就等于让它在栈里悬空。
        """
        return LevelStack(root=root)


class PageRole(Enum):
    """这条栈该被画成哪一页 —— **页面的唯一判据**。

    各条路径不再各自挑一页（那样「页面」与「我在哪」是两份记账，迟早对不上）：先改栈，
    再由 :func:`page_role_of` 算出该画哪一页。四个值对应窗口里那四个内容页。
    """

    WELCOME = "welcome"
    GRID = "grid"
    CALENDAR = "calendar"
    DETAIL = "detail"


def page_role_of(stack: LevelStack, *, has_library: bool) -> PageRole:
    """这一条栈此刻该画成哪一页（纯函数，不碰 Qt）。

    - **没有库** → 欢迎页：一个库都没打开时没有「这一层」可言。
    - **栈顶是照片帧** → 大图页。**栈底是日历、顶上没有展开的层** → 日历页（``expanded=False``
      的那一格也算「没有」，它从没在屏幕上展开成网格）。
    - **其余**（相册 / 分组 / 展开的日历格 / 搜索结果）→ 网格页。"""
    if not has_library:
        return PageRole.WELCOME
    top = stack.top
    if isinstance(top, PhotoFrame):
        return PageRole.DETAIL
    if stack.root is RootKind.CALENDAR and (
        top is None or (isinstance(top, CalendarFrame) and not top.expanded)
    ):
        return PageRole.CALENDAR
    return PageRole.GRID


def label_of(frame: Frame | None) -> str:
    """这一帧在界面上叫什么（面包屑 / 标题用）。

    **只有相册与分组有自己的名字**，日历格有 ``label`` 而搜索结果与照片拿的是上下文：
    搜索结果那一段写着「搜索结果（N）」，照片写着文件名 —— 那些文案不在这里（它们要带数量 /
    要读元数据），所以这里只回一个**短标签**，由调用处决定怎么写进整句话。
    """
    if isinstance(frame, AlbumFrame):
        return frame.path.name
    if isinstance(frame, GroupFrame):
        return frame.group.label
    if isinstance(frame, CalendarFrame):
        return frame.label
    if isinstance(frame, PhotoFrame):
        return frame.path.name
    return ""
