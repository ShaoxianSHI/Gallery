"""横幅那**一个槽**的记账：谁占着它、用户刚做过什么、扫描有没有出过问题（ADR 0054）。

横幅只有一个而后台进度有三条、错误提示有好几条，所以「谁占着它」必须记账 ——
而**漏记的症状一律是静默的**：漏了「这一层是空的」那个记号它就再也说不出来，
漏了扫描出问题的那个门闩，进度会去抢那句「哪些内容没读到」。**纯 Python，不碰 Qt**。
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["BannerText", "NoticeSlot"]


class NoticeSlot:
    """这一族后台消息此刻在横幅上占着哪一句。# guard: allow-long-comment（三件事各有一份判据）

    :param show: ``(text) -> bool`` —— 作为**后台消息**写上去，返回它是否真的显示了。
    :param current: 横幅上此刻写着什么（「抢之前先看一眼」要问它）。
    :param hide: 收掉一条（只在上面写着的正是自己那一句时才收）。
    :param allowed: 准不准说话（默认准）。**判据放进槽而不是调用点** —— 那条判据
        （扫描出过问题时进度不许抢）有三四个调用点，漏一处是静默的。
    """

    __slots__ = ("_allowed", "_current", "_hide", "_show", "text")

    def __init__(
        self,
        show: Callable[[str], bool],
        current: Callable[[], str],
        hide: Callable[[], None],
        *,
        allowed: Callable[[], bool] | None = None,
    ) -> None:
        self._show = show
        self._current = current
        self._hide = hide
        self._allowed = allowed
        #: 自己写上去的那句话；``None`` = 当前没占着横幅。**「占着没有」就是它本身**。
        self.text: str | None = None

    @property
    def is_ours(self) -> bool:
        """此刻占着横幅的是不是自己这一族。"""
        return self.text is not None

    def show(self, text: str, *, defer: bool = True) -> None:
        """把 ``text`` 写到横幅上，并记住自己写了什么。

        :param defer: 横幅上已经写着**别人**的话时退开等它说完（默认）。
        **``defer=False`` 只给「读元数据」那一族用**：它连着好几条，退让判据交给
        :meth:`BannerText.claim_sticky` 就够 —— 后者读的是「屏幕上现在有没有字」，
        用户上一次动作早结束时它会一直挡着，整条进度一句都上不去。
        """
        if defer:
            shown = self._current()
            if shown and shown != self.text:
                return  # 上面有更要紧的话（「哪些内容没读到」「已把 3 项移到废纸篓」…）
        if self._allowed is not None and not self._allowed():
            return  # 这一族此刻不许说话（扫描出过问题时进度让位，见 :attr:`allowed`）
        if not self._show(text):
            return
        self.text = text

    def clear(self) -> None:
        """收掉自己那一条 —— **而且只在自己还占着的时候收**。

        两种情形必须留着别动：扫描出问题时那句横幅比进度重要得多（那时它压根没显示）；
        用户刚做完的动作同理 —— 那时进度没抢上去，就更不该由收尾把它一起收走。
        """
        if not self.is_ours:
            return
        text, self.text = self.text, None
        if text is not None and self._current() == text:
            self._hide()


class BannerText:
    """横幅这一个槽 —— 四个族的记账、用户动作的让位期、以及「进度不许抢」那个门闩。

    :param show: ``(text, *, auto_hide) -> bool``，把一句话写到横幅上并返回它显示了没有。
    :param current: 横幅上此刻写着什么。 :param hide: 收掉横幅。
    :param sticky_seconds: 用户触发一条之后后台消息让位多久。
    :param monotonic: 时钟，注入是为了能测（让位期是拿秒比的一段窗口）。
    """

    def __init__(
        self,
        show: Callable[..., bool],
        current: Callable[[], str],
        hide: Callable[[], None],
        *,
        sticky_seconds: float,
        monotonic: Callable[[], float],
    ) -> None:
        self._show = show
        self._current = current
        self._hide = hide
        self._sticky_seconds = sticky_seconds
        self._monotonic = monotonic
        self._sticky_until = 0.0
        #: 扫描本身出过问题（跳过的目录 / 读不了的文件）。**它比「这里是空的」更该说**，
        #: 而进度不许在它占着的时候抢 —— 三个「进度」入口都要先问这一句。
        self.scan_issue = False
        #: 「这一层是空的」那句话是不是我们说的。**它只记一句是谁说的，不记原文** ——
        #: 那一句每次都不一样（有内容 / 在分组 / 在相册），而收它的时候是整条横幅一起收。
        self.level_notice = False
        #: 这一轮解码失败了几张（「有 3 张照片无法显示缩略图」那个数字）。**换库归零**。
        self._thumbnail_failures = 0
        self.progress = NoticeSlot(self._show_background, self._current, self._hide)
        # **地点与索引这两族受扫描门闩管**（「哪些内容没读到」比它们更要紧），而「读元数据」
        # 不受 —— 那一句说的是用户正在等的东西（ADR 0030）。
        self.place = NoticeSlot(
            self._show_background, self._current, self._hide, allowed=self._may_talk
        )
        self.index = NoticeSlot(
            self._show_background, self._current, self._hide, allowed=self._may_talk
        )

    def _show_background(self, text: str) -> bool:
        """槽要显示一句话时走这里 —— **一律算后台消息**（用户动作那几条不经槽）。"""
        return self._show(text, auto_hide=False)

    def _may_talk(self) -> bool:
        return not self.scan_issue

    def note_thumbnail_failure(self) -> int:
        """记一笔「这张照片的缩略图读不出来」，返回**累计**张数（那句话里要写它）。

        它是一条横幅文案的数据源而不是横幅的占用者 —— 所以它住在这里而**不占槽**：
        用户刚触发的那条横幅不该被它顶掉，而它自己也不该去顶别人的。
        """
        self._thumbnail_failures += 1
        return self._thumbnail_failures

    def say_level_is_empty(self, text: str) -> bool:
        """说「这一层是空的」—— **记下这一句是我们说的**，于是换层时收得掉它。

        那一句每次都不一样（有内容 / 在分组 / 在相册视图），所以只记「是谁说的」；
        :meth:`clear_all` 收它的时候是连整条横幅一起收（换层本来就该清屏）。
        **扫描出过问题时不显示**：那句「哪些内容没读到」更该说，而它也不许被收掉。
        """
        if not self._may_talk() or not self._show(text, auto_hide=False):
            return False
        self.level_notice = True
        return True

    # ---- 谁占着它 ---------------------------------------------------------

    @property
    def may_show_progress(self) -> bool:
        """进度消息此刻能不能说话 —— **扫描出过问题就不许**。

        「哪些内容没读到」比「正在读取…」更要紧，抢了它用户就不知道照片少了。
        """
        return not self.scan_issue

    @property
    def thumbnail_failures(self) -> int:
        """这一轮解码失败了几张（测试与诊断要读它；写只走 :meth:`note_thumbnail_failure`）。"""
        return self._thumbnail_failures

    # ---- 用户动作的让位期 -------------------------------------------------

    def claim_sticky(self, *, background: bool) -> bool:
        """一句话要不要让位给用户刚触发的那条。

        :param background: 这是后台消息（``True``）还是用户动作的结果（``False``）。
        :return: ``True`` = 可以显示；后台消息在让位期内返回 ``False``。
        """
        now = self._monotonic()
        if background and now < self._sticky_until:
            return False
        if not background:
            self._sticky_until = now + self._sticky_seconds
        return True

    # ---- 清 ---------------------------------------------------------------

    def clear_all(self) -> None:
        """**清屏**：三个槽的账 + 让位期一起归零。

        只 ``hide()`` 不清账的话，下一条进度会以为「上面已经写着我的字」而继续让着，
        「这一层是空的」还会去收掉一条它并不拥有的横幅。**翻照片与退层走这里** ——
        用户刚做完的动作不该因为翻了一张照片就失去让位期。
        """
        self._reset_slots()
        self._sticky_until = 0.0

    def reset_level(self) -> None:
        """**换层 / 换库**：三个槽的账 + 扫描门闩归零，**让位期留着**。

        让位期是**时间**而与层无关：用户在检索结果页刚删掉三张照片，随即退了一层，
        那句确认背后的两秒仍然该有效。**门闩要插回去**：新的一层重新扫，
        扫完之前不知道有没有问题 —— 带着上一层的门闩的话进度会一直不吭声。
        """
        self._reset_slots()
        self.scan_issue = False

    def _reset_slots(self) -> None:
        for slot in self._slots():
            slot.clear()
        self.level_notice = False

    def note_scan_issue(self) -> None:
        """扫描本身出过问题 —— 从此进度不许抢横幅。"""
        self.scan_issue = True

    def clear_scan_issue(self) -> None:
        """换层 / 换库：那个门闩插回去（新的一层重新扫，扫完之前不知道有没有问题）。"""
        self.scan_issue = False

    def reset_thumbnail_failures(self) -> None:
        """换库：失败张数归零（新库的第一张读不出来不该报「第 6 张」）。"""
        self._thumbnail_failures = 0

    def _slots(self) -> tuple[NoticeSlot, ...]:
        return (self.progress, self.place, self.index)
