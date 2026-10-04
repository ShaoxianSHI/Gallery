"""后台进度在横幅上占的那个槽 —— 一份算法，四个调用点。

横幅**只有一个**，而后台的进度消息有好几条（读元数据、解析地点、建立检索索引），
外加「这一层是空的」这类由界面自己决定要不要让位的提示。原先每一族各写一对
``_show_x_notice`` / ``_clear_x_notice``，它们做的事**只有一件**：

1. **抢之前先看一眼**：横幅上已经写着别人的话（「部分内容没能读取：…」「已把 3 项移到
   废纸篓。」）就别动它 —— 后台进度是全局共享的那个槽里**最没有资格**占着它的一类；
2. 写上去之后**记住自己写的是哪一句**；
3. 收尾时**只收自己那一句**，而且只在自己还占着的时候收。

三条各写一遍、共九遍，于是改判据要改九处 —— 而这类判据改一处漏一处，症状是
「读元数据的进度把扫描出错那句话顶掉了」，极难归因。

这一份只管那套记账，**不管说什么**：说什么仍然是各调用点的事（``正在读取 12 / 40``
与 ``正在解析 128 张照片的地点…`` 是三句话，不该统一）。

**刻意不做成 ``self._banner`` 的替代品**：横幅本体（长什么样、摆哪儿、什么时候自己
收起）在 :mod:`gallery.ui.banner_overlay`，那一层与本文件是两回事。
"""

from __future__ import annotations

from typing import Any

from gallery.ui.banner_overlay import BannerOverlay


class NoticeSlot:
    """「这一族后台消息此刻在横幅上占着哪一句」的记账。

    :param owner: 窗口。槽要读写它的 ``_show_banner`` 与 ``_banner``。
    :param text_attr: **自己写上去的那句话**存在窗口上的哪个属性（``str | None``）。
    :param flag_attr: 另有一个「此刻占着没有」的布尔属性时给出它的名字；没有就
        「``text_attr`` 是 ``None`` 就算没占着」。

    属性名是**传进来**的而不是在这里写死，因为 ``_progress_notice`` /
    ``_index_notice_text`` / ``_place_notice_text`` 三个名字被 ``main_window.__init__``、
    测试与 ``write_ops`` 直接读写 —— 搬进这里之后它们仍得是窗口上那几个名字。
    """

    def __init__(
        self,
        owner: Any,
        *,
        text_attr: str,
        flag_attr: str | None = None,
    ) -> None:
        self._owner = owner
        self._text_attr = text_attr
        self._flag_attr = flag_attr

    # ---- 窗口上那几个属性，薄薄一层包装 --------------------------------------

    @property
    def text(self) -> str | None:
        """自己写上去的那句话；``None`` = 当前没占着横幅。"""
        return getattr(self._owner, self._text_attr)

    @text.setter
    def text(self, value: str | None) -> None:
        setattr(self._owner, self._text_attr, value)

    @property
    def is_ours(self) -> bool:
        """此刻占着横幅的是不是自己这一族。"""
        if self._flag_attr is not None:
            return bool(getattr(self._owner, self._flag_attr))
        return self.text is not None

    # ---- 那套记账 ------------------------------------------------------------

    def show(self, text: str, *, defer: bool = True) -> None:
        """把 ``text`` 写到横幅上，并记住自己写了什么。

        :param defer: 横幅上已经写着**别人**的话时退开等它说完（默认）。

        **``defer=False`` 只给「读元数据」那一族用**：它连着好几条（先「正在加载城市库…」，
        再「正在读取…12 / 40」），退让的判据交给 ``_show_banner`` 里的
        ``BANNER_STICKY_SECONDS`` 去做就够了 —— 那才是「用户刚做完一件事」的判据，
        而这里这道判据读的是「屏幕上现在有没有字」，用户上一次动作早结束时它会一直挡着，
        于是整条进度一句都上不去。
        """
        banner = self._banner
        if defer:
            current = banner.text() if banner.isVisible() else ""
            if current and current != self.text:
                return  # 上面有更要紧的话（「哪些内容没读到」「已把 3 项移到废纸篓」…）
        if not self._owner._show_banner(text, background=True):
            return
        if self._flag_attr is not None:
            setattr(self._owner, self._flag_attr, True)
        self.text = text

    def clear(self) -> None:
        """收掉自己那一条 —— **而且只在自己还占着的时候收**。

        两种情形必须留着别动：扫描出问题时那句横幅比进度重要得多（那时它压根没亮）；
        用户刚做完的动作同理 —— 那时候进度压根没抢上去，就更不该由收尾把它一起收走。
        """
        if not self.is_ours:
            return
        if self._flag_attr is not None:
            setattr(self._owner, self._flag_attr, False)
        text, self.text = self.text, None
        if text is None:
            return
        banner = self._banner
        if banner.isVisible() and banner.text() == text:
            banner.hide()

    @property
    def _banner(self) -> BannerOverlay:
        return self._owner._banner


__all__ = ["NoticeSlot"]
