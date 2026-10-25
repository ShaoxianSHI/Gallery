"""macOS 自动塞进菜单栏的系统项：AutoFill（编辑）与 Full Screen（显示）。"""

from __future__ import annotations

import sys

import pytest

from gallery.ui.app import (
    _drop_autofill_and_below,
    _drop_full_screen,
    _hide_autofill_and_below,
    _hide_full_screen,
    _observe_additions,
)

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit 只在 macOS 上")


def _item(title: str, action: str = "qt_itemFired:") -> object:
    from AppKit import NSMenuItem
    from Foundation import NSSelectorFromString

    return NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, NSSelectorFromString(action), ""
    )


def _menu(*items: object) -> object:
    from AppKit import NSMenu

    menu = NSMenu.alloc().init()
    for item in items:
        menu.addItem_(item)
    return menu


def _titles(menu: object) -> list[str]:
    return [item.title() for item in menu.itemArray()]  # type: ignore[attr-defined]


def test_autofill_and_everything_below_it_is_gone() -> None:
    from AppKit import NSMenuItem

    menu = _menu(
        _item("全选"),
        _item("新增事件", "submenuAction:"),
        _item("撤销"),
        NSMenuItem.separatorItem(),
        _item("AutoFill", "submenuAction:"),
        _item("Passwords"),
    )

    _drop_autofill_and_below(menu)

    assert _titles(menu) == ["全选", "新增事件", "撤销"]


def test_items_added_after_startup_are_hidden_immediately() -> None:
    """**系统项是在菜单展开时加的**：先隐藏，再等 AppKit 插完才删（不能当轮 remove）。"""
    from AppKit import NSMenuItem

    menu = _menu(_item("撤销"))
    token = _observe_additions(menu, _drop_autofill_and_below, _hide_autofill_and_below)

    menu.addItem_(NSMenuItem.separatorItem())
    menu.addItem_(_item("AutoFill", "submenuAction:"))
    menu.addItem_(_item("Passwords"))

    tail = menu.itemArray()[1:]
    assert tail and all(item.isHidden() for item in tail)
    _drop_autofill_and_below(menu)
    assert _titles(menu) == ["撤销"]
    assert token is not None


def test_full_screen_is_removed_from_the_view_menu() -> None:
    menu = _menu(
        _item("Larger Thumbnails"),
        _item("Thumbnail Size", "submenuAction:"),
        _item("Enter Full Screen", "toggleFullScreen:"),
    )

    _drop_full_screen(menu)

    assert _titles(menu) == ["Larger Thumbnails", "Thumbnail Size"]


def test_a_late_localized_full_screen_is_hidden_by_selector() -> None:
    menu = _menu(_item("刷新"))
    token = _observe_additions(menu, _drop_full_screen, _hide_full_screen)

    menu.addItem_(_item("进入全屏幕", "toggleFullScreen:"))

    added = menu.itemArray()[-1]
    assert added.isHidden()
    _drop_full_screen(menu)
    assert _titles(menu) == ["刷新"]
    assert token is not None


def test_our_own_submenus_are_left_alone() -> None:
    menu = _menu(
        _item("新增事件", "submenuAction:"),
        _item("编辑事件", "submenuAction:"),
        _item("撤销"),
    )

    _drop_autofill_and_below(menu)
    _drop_full_screen(menu)

    assert _titles(menu) == ["新增事件", "编辑事件", "撤销"]


def test_menu_action_images_are_disabled_for_this_process() -> None:
    """系统给菜单项自动加图标；关掉它只影响本进程，不写全局 defaults。"""
    from Foundation import NSUserDefaults

    from gallery.ui.app import disable_menu_action_images

    disable_menu_action_images()

    assert NSUserDefaults.standardUserDefaults().boolForKey_("NSMenuEnableActionImages") is False
