"""后台进度在横幅上占的那个槽 —— 一份记账，四个调用点。

横幅只有一个，而进度有好几条。每一族原先各写一对 ``_show_x_notice`` / ``_clear_x_notice``，
做的事只有三件：**抢之前先看一眼**、写上去之后**记住自己写的是哪一句**、收尾时**只收自己
那一句**。三件各写一遍共九遍，改判据就得改九处，而漏一处的症状极难归因。

这一份只管记账，**不管说什么** —— 横幅本体在 :mod:`gallery.ui.banner_overlay`。
"""

from __future__ import annotations

from typing import Any

from gallery.ui.banner_overlay import BannerOverlay


class NoticeSlot:
    """「这一族后台消息此刻在横幅上占着哪一句」的记账。

    :param owner: 窗口。槽要读写它的 ``_show_banner`` 与 ``_banner``。
    :param text_attr: **自己写上去的那句话**存在窗口上的哪个属性（``str | None``）。
    :param flag_attr: 另有一个「此刻占着没有」的布尔属性时给出它的名字。

    属性名是**传进来**的而不是写死：``main_window.__init__``、测试与 ``write_ops`` 直接读写
    那几个名字，搬进这里之后它们仍得是窗口上那几个名字。
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

        **``defer=False`` 只给「读元数据」那一族用**：它连着好几条，退让的判据交给
        ``_show_banner`` 里的 ``BANNER_STICKY_SECONDS`` 就够 —— 这道判据读的是「屏幕上现在
        有没有字」，用户上一次动作早结束时它会一直挡着，整条进度一句都上不去。
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
        用户刚做完的动作同理 —— 那时进度没抢上去，就更不该由收尾把它一起收走。
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
