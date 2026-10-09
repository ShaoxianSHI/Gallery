"""横幅那**一个槽**的记账钉在这一份（ADR 0054 的实施）。

横幅只有一个而后台进度有三条、错误提示有好几条，所以「谁占着它」必须记账 —— 而**漏记的
症状一律是静默的**：漏了「这一层是空的」那个记号，它就再也说不出来；漏了扫描出问题的
那个门闩，进度会去抢那句「哪些内容没读到」。

所以这里钉三件事：**收尾只收自己那一句**、**用户刚做完的动作不被后台抢**、
以及**换层与翻照片清的不是同一批东西**（让位期是时间，与层无关）。

这一族原先是窗口上七个字段，而 `NoticeSlot` 读写的属性名是以字符串传进去的 ——
写错一个要等到运行时才发现。这一份整个不需要造窗口就能跑，时钟也是注入的。
"""

from __future__ import annotations

from gallery.ui.window.banner_text import BannerText

#: 让位期：一段假的时钟（秒），第一份从 100.0 起。
NOW = 100.0


class FakeBanner:
    """只记「屏幕上现在写着什么」的那一层 —— 横幅本体是 Qt 的，这里替掉它。"""

    def __init__(self) -> None:
        self.text: str | None = None
        self.shown: list[str] = []
        self.hidden = 0

    def show(self, text: str) -> None:
        self.text = text
        self.shown.append(text)

    def hide(self) -> None:
        self.hidden += 1
        self.text = None


class Harness:
    """一个 :class:`BannerText` 加它那三个回调，时钟由本份自己拨。"""

    def __init__(self) -> None:
        self.banner = FakeBanner()
        self.now = NOW
        self.text = BannerText(
            self._show,
            lambda: self.banner.text or "",
            self.banner.hide,
            sticky_seconds=2.0,
            monotonic=lambda: self.now,
        )

    def _show(self, text: str, *, auto_hide: bool) -> bool:
        if not self.text.claim_sticky(background=True):
            return False
        self.banner.show(text)
        return True

    def user_says(self, text: str) -> bool:
        """用户刚做完一件事 —— 走 ``_show_banner`` 那一支（非后台）。"""
        if not self.text.claim_sticky(background=False):
            return False
        self.banner.show(text)
        return True

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_slot_remembers_what_it_wrote_and_clears_only_that() -> None:
    """**收尾只收自己那一句** —— 别人后来写的那一句不许被收掉。

    这是 :class:`NoticeSlot` 存在的全部理由：地点解析跑完了要把「正在解析…」收掉，
    而那期间用户可能刚看到一句「已把 3 项移到废纸篓」，收掉它用户就再也看不到自己做了什么。
    """
    h = Harness()
    h.text.place.show("正在解析照片的地点…")
    h.user_says("已把 3 项移到废纸篓")

    h.text.place.clear()

    assert h.banner.text == "已把 3 项移到废纸篓", "收尾把别人的话收走了"


def test_a_slot_stays_quiet_while_someone_else_is_talking() -> None:
    """**上面有别人的话就让着** —— 后台进度不抢用户刚触发的那条。"""
    h = Harness()
    h.user_says("已把 3 项移到废纸篓")

    h.text.place.show("正在解析照片的地点…")

    assert h.banner.text == "已把 3 项移到废纸篓", "后台进度抢了用户的话"
    assert h.text.place.is_ours is False, "让位的那一次不该记成「我占着」"


def test_a_slot_repeats_its_own_line_without_deferring_to_itself() -> None:
    """**自己那句可以连着更新** —— 「已处理 12 张」→「已处理 48 张」不该让着自己。"""
    h = Harness()
    h.text.place.show("正在解析照片的地点…已处理 12 张")
    h.text.place.show("正在解析照片的地点…已处理 48 张")

    assert h.banner.text == "正在解析照片的地点…已处理 48 张"
    assert h.text.place.is_ours is True


def test_the_metadata_family_ignores_whose_line_is_on_screen() -> None:
    """``defer=False`` 只给「读元数据」那一族：它**只让位于让位期，不看谁的字在屏上**。

    它连着好几条（总数是已知的），每一条都比一遍「上面是谁的」就会互相让着，一整轮
    一条都上不去 —— 而让位期已经足够了（它要挡的只是用户刚做完的那一件事）。
    """
    h = Harness()
    h.user_says("坐标已按『杭州』更新")
    h.advance(3.0)  # 让位期过了，下面比的是「谁的字在屏上」而不是时间
    h.text.place.show("正在解析照片的地点…")

    h.text.progress.show("正在读取元数据…1 / 40", defer=False)

    assert h.banner.text == "正在读取元数据…1 / 40", "它在跟自己的另一族抢"


def test_even_the_metadata_family_waits_out_the_grace_period() -> None:
    """``defer=False` **不是「谁都不让」** —— 让位期仍然挡它（那才是它要挡的那一件事）。"""
    h = Harness()
    h.user_says("坐标已按『杭州』更新")

    h.text.progress.show("正在读取元数据…1 / 40", defer=False)

    assert h.banner.text == "坐标已按『杭州』更新"


def test_a_background_message_waits_out_the_users_grace_period() -> None:
    """用户刚做完一件事，后台消息**让位两秒**（横幅只有一个槽）。"""
    h = Harness()
    h.user_says("已把 3 项移到废纸篓")

    assert h.text.claim_sticky(background=True) is False, "让位期内后台消息抢上去了"
    h.advance(1.9)
    assert h.text.claim_sticky(background=True) is False, "让位期提前结束了"
    h.advance(0.2)
    assert h.text.claim_sticky(background=True) is True


def test_a_background_message_does_not_extend_the_grace_period() -> None:
    """后台消息**不延长**让位期 —— 否则连续进度消息能让用户那句话永远上不了台。"""
    h = Harness()
    h.user_says("已把 3 项移到废纸篓")
    h.advance(1.0)

    assert h.text.claim_sticky(background=True) is False
    h.advance(1.1)

    assert h.text.claim_sticky(background=True) is True, "让位期被后台消息续上了"


def test_progress_stays_quiet_once_the_scan_reported_a_problem() -> None:
    """**扫描出过问题之后进度一律不许说话** —— 那句「哪些内容没读到」更要紧。

    三个进度入口（地点 / 索引 / 读元数据）都要问这一句，而判据只有这一个。
    """
    h = Harness()
    assert h.text.may_show_progress is True
    h.text.note_scan_issue()

    assert h.text.may_show_progress is False
    h.text.place.show("正在解析照片的地点…")
    assert h.banner.text is None, "进度抢了「哪些内容没读到」"


def test_a_new_level_lets_the_scan_problem_flag_go() -> None:
    """换层 / 换库：门闩跟着 ``reset_level`` 一起插回去。

    新的一层重新扫，扫完之前不知道有没有问题 —— 带着上一层的门闩的话，
    「正在解析照片的地点…」会一直不吭声，而照片其实读得好好的。
    """
    h = Harness()
    h.text.note_scan_issue()
    h.text.reset_level()

    assert h.text.may_show_progress is True


def test_clearing_the_screen_takes_the_grace_period_with_it() -> None:
    """``clear_all``：三个槽的账 + 让位期一起归零（翻照片、退层走这条）。"""
    h = Harness()
    h.user_says("已把 3 项移到废纸篓")
    h.text.place.show("正在解析照片的地点…")

    h.text.clear_all()

    assert h.text.place.is_ours is False
    assert h.text.level_notice is False
    assert h.text.claim_sticky(background=True) is True, "让位期没被清掉"


def test_changing_the_level_keeps_the_grace_period() -> None:
    """``reset_level`` **不动让位期** —— 那是时间，与层无关。

    用户在检索结果页刚删掉三张照片，随即退了一层，那句确认背后的两秒仍然该有效。
    这是它与 ``clear_all`` 唯一的差别，所以两条都要有用例。
    """
    h = Harness()
    h.user_says("已把 3 项移到废纸篓")
    h.text.place.show("正在解析照片的地点…")

    h.text.reset_level()

    assert h.text.place.is_ours is False, "换层该把槽的账清掉"
    assert h.text.claim_sticky(background=True) is False, "换层不该把让位期也清了"


def test_the_empty_level_line_is_marked_as_ours_so_a_reset_can_take_it() -> None:
    """空层那一句经轴去说，于是**「这一句是我们说的」只有一个地方会置**。

    原来那个布尔字段在 `_update_level_notice` 与换层那六处各写一遍，漏掉一处时
    「这一层是空的」就再也说不出来了 —— 而那句话是用户唯一知道自己这一层出了什么的线索。
    """
    h = Harness()
    h.text.say_level_is_empty("没有找到可显示的照片。")
    assert h.text.level_notice is True

    h.text.reset_level()

    assert h.text.level_notice is False


def test_the_empty_level_line_gives_way_to_a_newer_one() -> None:
    """空层那句话**不该盖掉用户刚看到的** —— 扫描门闩插着时它压根不显示。"""
    h = Harness()
    h.text.note_scan_issue()

    assert h.text.say_level_is_empty("没有找到可显示的照片。") is False
    assert h.banner.text is None


def test_the_failure_counter_counts_up_and_is_zeroed_on_the_next_library() -> None:
    """失败张数**只增不减，换库归零**（新库的第一张读不出来不该报「第 6 张」）。"""
    h = Harness()
    assert h.text.note_thumbnail_failure() == 1
    assert h.text.note_thumbnail_failure() == 2
    assert h.text.thumbnail_failures == 2

    h.text.reset_thumbnail_failures()

    assert h.text.thumbnail_failures == 0
