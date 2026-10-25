"""中文：源语言。

这里的值就是界面原先那句话，迁移时**标点、空格、「」、⌘ 一个字都不改**（ADR 0106 决策 2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import DomainMessages, Message, merge
from gallery.core.locales.zh_CN import (
    app,
    banner,
    browser,
    calendar,
    dialog,
    event,
    external,
    flow,
    library,
    media,
    menu,
    metadata,
    settings,
    viewer,
    widget,
    window,
)

DOMAINS: Final[Mapping[str, DomainMessages]] = {
    "banner": banner.MESSAGES,
    "dialog": dialog.MESSAGES,
    "menu": menu.MESSAGES,
    "settings": settings.MESSAGES,
    "window": window.MESSAGES,
    "viewer": viewer.MESSAGES,
    "calendar": calendar.MESSAGES,
    "browser": browser.MESSAGES,
    "widget": widget.MESSAGES,
    "metadata": metadata.MESSAGES,
    "library": library.MESSAGES,
    "event": event.MESSAGES,
    "flow": flow.MESSAGES,
    "external": external.MESSAGES,
    "media": media.MESSAGES,
    "app": app.MESSAGES,
}
MESSAGES: Final[Mapping[str, Message]] = merge(DOMAINS)

__all__ = ["DOMAINS", "MESSAGES"]
