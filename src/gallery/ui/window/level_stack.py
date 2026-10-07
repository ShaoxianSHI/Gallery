"""「我现在在哪一层」—— 一条栈，而不是五个各自为政的字段。

这一整个模块是 ADR 0041 决策 1 的落地。原来「我在
哪」由五个独立可变字段表达，而它们之间有一堆**没人声明的互斥关系**（有分组就不该有日历那一格、
有条件时浏览方式的含义会变……），于是每加一条路径都要靠人记住「这五个我该一起清哪些」；漏一个的
后果是「按 Esc 没反应」，而代码里那处 ``return`` 看上去完全正确：

- 搜索结果里按 Esc 退不出去 —— 它既不是文件夹也不是分组，``go_up`` 那串 ``if`` 里压根没有它；
- 大图页上搜一下会被人弹回网格页，而照片帧凭空消失，之后退不出来。

**这一层模型一句话**：「我在哪」是一条栈，栈深 > 1 就退得回去，退一层就是弹一个帧。加一种新层级
不用改任何一处判据 —— 而原来每加一种都要记得同时改 ``go_up`` 与 ``_can_go_up``。

**栈底那一帧决定浏览方式**，所以浏览方式不是独立字段（``_browse_mode``）：

- 全部照片：只有根帧，平铺整库。
- 相册视图：根帧之上可压若干「相册」。
- 日历视图：根帧之上压「日历格」。

这两条链**互斥** —— 日历格下面不可能是相册，那不是靠 ``if`` 判掉的，是类型上就排除了。
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
    "PhotoFrame",
    "RootKind",
    "SearchFrame",
    "browse_mode_of",
    "label_of",
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
    """日历上点开的那一格。

    **照片一起存**：那一格摊开的内容就是它，``photos`` 为空表示这一格一张照片也没有
    （点开是合法动作，只是没东西可看）。

    :param expanded: 这一格**在屏幕上摊成网格**了吗。多张的那一格有：它自己就是一层，
        退回来停在它上面。只有一张的那一格没有 —— 那一次是「日历格 + 照片」**一起进、一起出**
        的一对（点开直接进大图，``Esc`` 连它一起收掉回日历），屏幕上从没出现过网格。
        **界面画哪一页与 ``Esc`` 退到哪一层都从这一位看**，不再靠「进来时是几张」各自记一份。
    """

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
    """整条栈。**不可变** —— 每一处改动都是返回新的一份，所以「我改了哪一帧」一眼看得出来。

    :param root: 栈底那一种浏览方式。
    :param frames: 根帧**之上**的帧，由底到顶。
    :param narrowing: 各帧各自的那份**筛选条件**（键是帧在 ``frames`` 里的下标）。

    **筛选随帧存而不是全局一份**（ADR 0041 决策 3）：在某一帧上收窄出来的条件属于那一帧，
    弹回它时恢复、压上别的帧时不动。反过来做（全局一份、进出层时自己记着恢复）就是
    「清栈的时候忘了清筛选」那一类 bug 的来源。
    """

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

        给两类动作用：在同一层里翻上下一张照片（往深里走会攒出一长串栈，``Esc`` 要按几十次才
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

        与 :meth:`pop` / :meth:`pop_until` 的区别就是这一条：**那些只能从栈顶弹**，所以摘不掉
        被别的帧压在下面的那一帧。

        要它的是「日历格 + 其上的搜索帧」这个形状：整库检索压在日历格之上，而检索不该让那一格
        继续算作「上一层」（条件撤光之后 Esc 会退到用户这一趟没去过的一格）。可搜索帧不能动 ——
        动它就留下一副「条件还在、栈上却没有搜索帧」的矛盾状态。用「从栈顶连续弹」去表达
        「摘中间那一帧」会正好犯这个错。

        **索引是 ``frames`` 里的下标**，越界抛 ``IndexError``（与 :meth:`with_narrowing` 一致，
        不静默当成「没找到」—— 那会让调用方以为摘掉了）。
        """
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
