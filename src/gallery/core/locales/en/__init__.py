"""English。

以中文为准翻译（ADR 0106 决策 2）：术语跟 README 的英文表，结果词用 ``could not`` /
``cannot`` / ``not yet``，一条横幅仍然是「结果 + 能否撤销」。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import DomainMessages, Message, merge
from gallery.core.locales.en import (
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
