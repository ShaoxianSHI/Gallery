"""每个 mixin **需要宿主提供哪几十样东西**钉在这一份（ADR 0055）。

那十几条 mixin 之间没有任何接口声明：`LibraryFlowMixin` 要 74 样窗口上的东西、
`ContextMenuFlowMixin` 54 —— 而这个数字原先**没有任何地方写着**。用到了什么、
是 MainWindow 给的还是别的 mixin 给的，只存在于读代码的人的脑子里，于是
「这一间到底和窗口耦合多深」没人能回答，**它只会随着新功能悄悄长**。

所以这里钉两件事：**清单与代码一致**（用到一个新的宿主成员就要更新它，于是那处改动
会被看见）与**总量不许变大**（第二道）。

**这不是类型检查**：它不知道那些成员的**类型**，也不检查方法体。它换来的是
「耦合面可见 + 不可静默增长」，而真正要的类型检查是另一件事 —— ADR 0055 记了它要多少
工作量（`ui/window` 开 `--strict` 是 948 条，其中 863 条是 ``[attr-defined]``：
「这个 mixin 用到了一个别人提供的东西」）以及为什么这一轮没做。
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

import pytest
from source_scan import source_root

#: 每个 mixin 的宿主耦合面 —— **这份表就是那道**。按需求从多到少排：前四间是耦合最深的，
#: 而它们也正是最该定期问一句「这一间还该存在吗」的地方。
#:
#: 想加一个成员请先回答：这样东西能不能不放在窗口上？
HOST_SURFACE: dict[str, frozenset[str]] = {
    "window/banner_flow.py:BannerFlowMixin": frozenset(
        {
            "_banner",
            "_banner_text",
            "_panel",
            "_refresh_thumbnail_window",
        }
    ),
    "window/calendar_flow.py:CalendarFlowMixin": frozenset(
        {
            "_apply_ordering",
            "_arm_focus",
            "_banner",
            "_banner_text",
            "_browse_mode",
            "_calendar_level",
            "_calendar_page",
            "_cancel_condition_edit",
            "_cancel_job",
            "_cancel_tier",
            "_clear_progress_notice",
            "_detail",
            "_finish_job",
            "_focus_state",
            "_generation",
            "_grid",
            "_leave_search_frame",
            "_library",
            "_metadata_cache",
            "_model",
            "_on_facet_progress",
            "_open_detail",
            "_panel",
            "_preview_cache",
            "_push_frame",
            "_refresh_thumbnail_window",
            "_reload_level",
            "_save_ui_state",
            "_scan_job",
            "_search_frame",
            "_set_root",
            "_show_banner",
            "_staging",
            "_start_facet_read",
            "_start_job",
            "_start_scan",
            "_sync_ui",
            "_update_level_notice",
            "_update_mode_actions",
        }
    ),
    "window/context_menu_flow.py:ContextMenuFlowMixin": frozenset(
        {
            "_album_picker",
            "_album_targets",
            "_cache_metadata",
            "_cached_metadata",
            "_can_create_album",
            "_can_paste",
            "_clear_cover_action",
            "_clear_cover_event_action",
            "_close_entry_action",
            "_copy_action",
            "_copy_group_action",
            "_copy_metadata_action",
            "_copy_path_action",
            "_current_row",
            "_duplicate_action",
            "_edit_event_action",
            "_edit_metadata_action",
            "_ensure_event_actions",
            "_finish_job",
            "_focus_row",
            "_generation",
            "_grid",
            "_in_detail_page",
            "_leave_event_action",
            "_library",
            "_metadata_action",
            "_model",
            "_new_album_action",
            "_on_album_chosen_for_copy",
            "_on_album_chosen_for_move",
            "_on_entry_activated",
            "_on_grid_page",
            "_open_default_action",
            "_open_entry_action",
            "_paste_action",
            "_rename_action",
            "_reveal_action",
            "_section_copy_action",
            "_select_all_action",
            "_selected_entries",
            "_selected_paths",
            "_set_cover_action",
            "_show_banner",
            "_size_actions",
            "_start_job",
            "_sync_ui",
            "_trash_action",
            "_up_action",
            "build_group_menu",
            "build_sort_menu",
            "cover_candidate",
            "covered_album",
            "covered_event",
            "edit_photo_event_menu",
            "metadata_edit_candidate",
            "record_as_candidate",
            "section_event_actions",
            "set_event_cover_menu",
        }
    ),
    "window/event_flow.py:EventFlowMixin": frozenset(
        {
            "_clear_cover_event_action",
            "_cover_event_menu",
            "_edit_event_action",
            "_event_leave_menu",
            "_focus_row",
            "_grid",
            "_groups_by_event",
            "_import_movies_menu",
            "_import_shows_action",
            "_import_shows_menu",
            "_leave_event_action",
            "_library",
            "_section_clear_event_cover_action",
            "_section_edit_event_action",
            "_section_leave_event_action",
            "_model",
            "_refresh_panel_events",
            "_refresh_undo_label",
            "_reload_level",
            "_reorder_photos",
            "_selected_entries",
            "_selected_paths",
            "_show_banner",
            "_staging",
            "_start_facet_read",
            "import_movies_from_dialog",
        }
    ),
    "window/filters.py:FiltersMixin": frozenset(
        {
            "_filters",
        }
    ),
    "window/focus.py:FocusMixin": frozenset(
        {
            "_focus_state",
        }
    ),
    "window/jobs.py:JobsMixin": frozenset(
        {
            "_jobs",
            "_runner",
        }
    ),
    "window/level_stack_mixin.py:LevelStackMixin": frozenset(
        {
            "_calendar_page",
            "_detail_page",
            "_grid_page",
            "_level_stack",
            "_welcome",
        }
    ),
    "window/library_flow.py:LibraryFlowMixin": frozenset(
        {
            "_app_session",
            "_apply_thumbnail_size",
            "_arm_focus",
            "_banner",
            "_banner_text",
            "_browse_mode",
            "_calendar_page",
            "_cancel_job",
            "_cancel_tier",
            "_clear_index_notice",
            "_confirm_orphan_cleanup",
            "_current_dir",
            "_current_order",
            "_detail",
            "_drill_photos",
            "_facet_job",
            "_facet_values",
            "_filters",
            "_finish_job",
            "_focus_state",
            "_forget_indexed_root",
            "_generation",
            "_group_facet",
            "_group_layout",
            "_group_view",
            "_is_library_search",
            "_level_stack",
            "_maybe_start_photo_index",
            "_metadata_action",
            "_metadata_cache",
            "_model",
            "_on_calendar_page",
            "_on_thumbnail_failed",
            "_on_thumbnail_ready",
            "_order_now",
            "_panel",
            "_pending_watch",
            "_place_lookup",
            "_place_pass_roots",
            "_preview_cache",
            "_recent_menu",
            "_reenter_group_later",
            "_refresh_thumbnail_window",
            "_refresh_undo_label",
            "_reload_level",
            "_report_error",
            "_runner",
            "_scan_job",
            "_search_scope",
            "_set_root",
            "_show_banner",
            "_show_calendar",
            "_size_index",
            "_slot",
            "_sort_descending",
            "_sort_facet",
            "_staging",
            "_start_calendar_load",
            "_start_facet_read",
            "_start_job",
            "_start_search",
            "_sync_search_scope",
            "_sync_ui",
            "_update_metadata_editable",
            "_update_mode_actions",
            "_watch",
            "_watch_echoes",
            "_welcome",
            "stop_slideshow",
        }
    ),
    "window/metadata_flow.py:MetadataFlowMixin": frozenset(
        {
            "_cache_metadata",
            "_cached_metadata",
            "_cancel_job",
            "_current_row",
            "_detail",
            "_focus_state",
            "_generation",
            "_layout_banner",
            "_library",
            "_load_detail",
            "_metadata_visible",
            "_model",
            "_panel",
            "_photo_frame",
            "_place_lookup",
            "_prefetch_metadata",
            "_push_frame",
            "_runner",
            "_save_ui_state",
            "_start_job",
            "enter_folder",
            "enter_group",
        }
    ),
    "window/navigation_flow.py:NavigationFlowMixin": frozenset(
        {
            "_apply_conditions",
            "_back_to_calendar",
            "_banner",
            "_banner_text",
            "_breadcrumb",
            "_browse_mode",
            "_calendar_page",
            "_cancel_condition_edit",
            "_cancel_tier",
            "_clear_banner",
            "_current_dir",
            "_detail",
            "_drill_photos",
            "_drop_calendar_cell_frame",
            "_drop_search_frames",
            "_editing_condition",
            "_facet_values",
            "_filters",
            "_focus_state",
            "_generation",
            "_grid",
            "_group_view",
            "_group_view_label",
            "_is_library_search",
            "_leave_calendar_cell",
            "_leave_search_frame",
            "_level_stack",
            "_library",
            "_metadata_cache",
            "_model",
            "_on_calendar_page",
            "_panel",
            "_pop_frame",
            "_preview_cache",
            "_push_album_chain",
            "_search_frame",
            "_search_scope",
            "_search_total",
            "_show_calendar",
            "_staging",
            "_start_scan",
            "_sync_calendar_conditions",
            "_sync_search_frame",
            "_sync_ui",
            "leave_detail_page",
            "stop_slideshow",
        }
    ),
    "window/ordering.py:OrderingMixin": frozenset(
        {
            "_banner",
            "_banner_text",
            "_ascending_action",
            "_browse_mode",
            "_cards_layout_action",
            "_descending_action",
            "_feed_calendar",
            "_filters",
            "_finish_job",
            "_flush_pending_watch",
            "_generation",
            "_grid",
            "_group_actions",
            "_group_view",
            "_in_detail_page",
            "_is_filtering",
            "_is_library_search",
            "_library",
            "_maybe_reenter_group",
            "_maybe_start_photo_index",
            "_maybe_start_place_pass",
            "_model",
            "_on_grid_page",
            "_ordering",
            "_pop_frame",
            "_pop_to",
            "_push_frame",
            "_refresh_thumbnail_window",
            "_reload_level",
            "_reorder_albums",
            "_reorder_photos",
            "_sections_layout_action",
            "_restore_focus",
            "_save_ui_state",
            "_show_banner",
            "_sort_actions",
            "_staging",
            "_start_job",
            "_start_level_filter",
            "_sync_ui",
        }
    ),
    "window/search_flow.py:SearchFlowMixin": frozenset(
        {
            "_apply_ordering",
            "_banner_text",
            "_conditions",
            "_editing_condition",
            "_facet_job",
            "_filters",
            "_finish_job",
            "_generation",
            "_has_exiftool",
            "_index_job",
            "_index_roots",
            "_is_filtering",
            "_is_library_search",
            "_level_stack",
            "_library",
            "_model",
            "_on_scan_batch",
            "_on_scan_done",
            "_owns_title_bar",
            "_place_job",
            "_pop_to",
            "_push_frame",
            "_reload_level",
            "_remember_ordering",
            "_search_field",
            "_search_frame",
            "_search_scope",
            "_search_total",
            "_show_banner",
            "_staging",
            "_start_job",
            "_sync_ui",
            "pop_last_condition",
        }
    ),
    "window/settings_flow.py:SettingsFlowMixin": frozenset(
        {
            "_detail",
            "_external_button",
            "_grid",
            "_library",
            "_library_setting_actions",
            "_model",
            "_report_error",
            "_save_session",
            "_session",
            "_settings_actions",
            "_settings_extras",
            "_show_banner",
            "_slideshow_timer",
            "_sync_ui",
        }
    ),
    "window/slideshow_flow.py:SlideshowFlowMixin": frozenset(
        {
            "_clear_banner",
            "_current_row",
            "_focus_state",
            "_in_detail_page",
            "_library",
            "_load_detail",
            "_metadata_visible",
            "_model",
            "_on_calendar_page",
            "_open_detail",
            "_owns_title_bar",
            "_panel",
            "_play_button",
            "_session",
            "_show_banner",
            "_slideshow",
            "_slideshow_timer",
            "step_detail",
        }
    ),
    "window/state_sync.py:StateSyncMixin": frozenset(
        {
            "_refresh_title",
            "_sync_order_actions",
            "_sync_page_to_stack",
            "_sync_settings",
            "_update_action_states",
            "_update_breadcrumb",
        }
    ),
    "window/thumbnail_flow.py:ThumbnailFlowMixin": frozenset(
        {
            "_banner_text",
            "_cache_job",
            "_calendar_page",
            "_finish_job",
            "_generation",
            "_grid",
            "_library",
            "_model",
            "_on_calendar_page",
            "_on_grid_page",
            "_pipeline",
            "_save_ui_state",
            "_show_banner",
            "_size_actions",
            "_start_job",
            "_thumb_size",
            "_was_self_written",
        }
    ),
    "window/title_bar_flow.py:TitleBarFlowMixin": frozenset(
        {
            "_breadcrumb",
            "_current_row",
            "_drill_label",
            "_drill_photos",
            "_external_button",
            "_fill_action",
            "_in_detail_page",
            "_library_title",
            "_model",
            "_owns_title_bar",
            "_play_button",
            "_scope_toggle",
            "_search_field",
            "_stack",
            "_title_bar",
            "_title_bar_drag",
        }
    ),
    "window/viewer_flow.py:ViewerFlowMixin": frozenset(
        {
            "_cancel_job",
            "_clear_banner",
            "_current_row",
            "_detail",
            "_flush_pending_watch",
            "_focus_state",
            "_generation",
            "_grid",
            "_library",
            "_load_metadata",
            "_maybe_lookup_place",
            "_metadata_cache",
            "_model",
            "_on_calendar_page",
            "_panel",
            "_photo_frame",
            "_preview_cache",
            "_refresh_thumbnail_window",
            "_runner",
            "_show_slideshow_delta",
            "_slideshow",
            "_start_job",
            "_sync_page_to_stack",
            "_sync_ui",
            "_title_bar",
            "go_up",
            "set_browse_mode",
            "stop_slideshow",
        }
    ),
    "window/watch_flow.py:WatchFlowMixin": frozenset(
        {
            "_apply_conditions",
            "_browse_mode",
            "_conditions",
            "_current_dir",
            "_current_row",
            "_focus_state",
            "_group_view",
            "_in_detail_page",
            "_is_library_search",
            "_level_is_grouped",
            "_library",
            "_load_detail",
            "_metadata_cache",
            "_model",
            "_on_grid_page",
            "_pending_watch",
            "_pipeline",
            "_pop_to",
            "_preview_cache",
            "_refresh_thumbnail_window",
            "_reload_level",
            "_scan_job",
            "_select_after_removal",
            "_show_banner",
            "_shown_entry_key",
            "_staging",
            "_start_facet_read",
            "_update_level_notice",
            "_watch_echoes",
            "enter_group",
        }
    ),
    "write_ops.py:WriteOpsMixin": frozenset(
        {
            "_after_events_changed",
            "_apply_ordering",
            "_browse_mode",
            "_cache_metadata",
            "_cached_metadata",
            "_current_dir",
            "_current_folder",
            "_current_row",
            "_detail_trashed",
            "_facet_values",
            "_finish_job",
            "_focus_state",
            "_generation",
            "_grid",
            "_group_view",
            "_in_detail_page",
            "_library",
            "_load_detail",
            "_load_metadata",
            "_metadata_cache",
            "_model",
            "_note_self_write",
            "_movie_import_job",
            "_on_grid_page",
            "_open_detail",
            "_panel",
            "_pipeline",
            "_place_lookup",
            "_preview_cache",
            "_refresh_thumbnail_window",
            "_refresh_undo_label",
            "_reload_level",
            "_show_banner",
            "_start_facet_read",
            "_start_job",
            "_sync_ui",
            "_title_bar",
            "_update_level_notice",
            "_write_job",
            "show_grid",
        }
    ),
}


def _qt_api() -> set[str]:
    """Qt 基类自己提供的那些名字 —— **必须真的问 Qt 要一份**。

    手抄一份清单的话，加一个 Qt 方法就会开始误报；而误报的回归测试最后一定是被
    「顺手放宽判据」弄坏的，那等于没有。
    """
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QMainWindow, QWidget

    return (
        set(dir(QMainWindow))
        | set(dir(QWidget))
        | set(dir(QObject))
        # 那几个不是方法而是 Qt 自己的属性，从 ``dir()`` 上摸不到。
        | {"parent", "children", "objectName", "setObjectName", "tr", "deleteLater"}
    )


def _host_need(cls: type) -> set[str]:
    """这个 mixin 用到的**宿主**成员（Qt 基类的不算，自己定义的不算）。

    **只扫这个 mixin 自己的类体**：轴对象（``FilterState`` 等）的 ``self`` 是它自己，
    把它们算进来量到的就完全不是一件事了。
    """
    path = pathlib.Path(sys.modules[cls.__module__].__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls.__name__
    )
    own = {n.name for n in body.body if isinstance(n, ast.FunctionDef)}
    used = {
        n.attr
        for n in ast.walk(body)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    }
    return used - own - _qt_api()


def _main_window_mixins() -> dict[str, type]:
    """``MainWindow`` 真正继承的那些 mixin，键是 ``相对路径:类名``。

    **从 MRO 取而不是扫全树**：``PhotoGrid`` 也有两个 mixin（``anchoring`` / ``transition``），
    而它们的宿主是网格不是窗口 —— 混进来量到的就是另一件事了。
    """
    from gallery.ui.main_window import MainWindow

    root = source_root().resolve()
    found: dict[str, type] = {}
    for cls in MainWindow.__mro__:
        if not cls.__name__.endswith("Mixin"):
            continue
        path = pathlib.Path(sys.modules[cls.__module__].__file__ or "")
        found[f"{path.resolve().relative_to(root).as_posix()}:{cls.__name__}"] = cls
    return found


def _measured() -> dict[str, frozenset[str]]:
    return {key: frozenset(_host_need(cls)) for key, cls in _main_window_mixins().items()}


def test_the_pinned_surface_matches_the_code() -> None:
    """**清单与代码一致** —— 用到一个新的宿主成员，就要在这里加一行。

    漏在这里的症状是静默的：清单没更新时这一条会红（它比对的是两边），
    而红的原因只是「表过期了」—— 真正要人回答的是「多出来的这一条依赖合不合理」。
    """
    measured = _measured()
    missing = sorted(set(measured) - set(HOST_SURFACE))
    assert not missing, f"新出现了清单里没有的 mixin，请把它要用的宿主成员列进来：{missing}"
    stale = sorted(set(HOST_SURFACE) - set(measured))
    assert not stale, f"清单里的这些 mixin 已经不在 ``MainWindow`` 的基类列表里了：{stale}"
    for key, declared in HOST_SURFACE.items():
        actual = measured[key]
        if actual != declared:
            raise AssertionError(
                f"{key} 用到的宿主成员变了：\n"
                f"  新增 {sorted(actual - declared)}\n  不再需要 {sorted(declared - actual)}\n"
                "新增的那些是这个模块与窗口之间**多出来的一条依赖** —— 请连同本清单一起改，"
                "并在提交信息里说清为什么它需要窗口上的这样东西。"
            )


def test_the_surface_does_not_grow() -> None:
    """**耦合面不许变大** —— 上一条钉「清单对不对」，这一条钉「不许把清单改大」。

    那 489 条是一次一次长出来的，而每一次都只多要了一样窗口上的东西。
    """
    measured = _measured()
    ceiling = sum(len(v) for v in HOST_SURFACE.values())
    actual = sum(len(v) for v in measured.values())
    assert actual <= ceiling, (
        f"宿主耦合面从 {ceiling} 涨到 {actual}。想加一个成员请先回答：这样东西能不能不放在窗口上？"
    )


def test_the_widest_mixins_keep_their_ceiling() -> None:
    """**最深的那几间各自的上限** —— 总量守住了而某一间独涨也要拦。

    单看总量不够：一间从 74 涨到 80、另一间从 5 掉到 0，总量不变，而那正是最该看的
    那种变化（它说明那一间该拆了）。
    """
    ceiling = {
        # 73 → 74：切库 / 关库要先收掉放映（`stop_slideshow`），否则旧快照会去新模型里找行号。
        # 74 → 77：⌘R 刷新（ADR 0087）要掐掉旧索引那一遍、让索引重新对账，分组视图里还要
        # 弹回上一层再回到同一个组 —— 三样都属于别的流程，只能从窗口上要。
        "window/library_flow.py:LibraryFlowMixin": 77,
        # 56 → 57：「编辑事件…」那一行让这一间多要一个 `edit_photo_event_menu`
        # （事件清单按哪张照片算归 EventFlowMixin，这一间只把菜单摆进去）。
        # 57 → 56：「分组」「排序」两张子菜单改由窗口现建（与菜单栏逐行一致），
        # 这一间少要三个动作 / 字典、多要两个构造方法，净少一条依赖。
        # 56 → 58：分区标题上的组级命令（ADR 0089）要一枚自己的「复制分组条件」动作，
        # 以及一个「把这一组写进动作里」的工厂 —— 标题不是当前项，共用卡片那枚会复制错组。
        "window/context_menu_flow.py:ContextMenuFlowMixin": 58,
        # 45 → 46：方向从「一个勾选项」变成「升序 / 降序」两项，多一个动作要同步勾选态。
        # 46 → 48：分组形态（卡片式 / 分区式，ADR 0089）也是两项互斥的勾选项。
        "window/ordering.py:OrderingMixin": 48,
        # 44 → 45：Esc 退不动时要取消日历上选中的那一天（ADR 0070），而「选中哪一天」
        # 只有 CalendarPage 知道；这一间因此多了一条对日历页的依赖。
        # 45 → 46：换层时先收掉放映（`stop_slideshow`），放映的快照属于上一层。
        "window/navigation_flow.py:NavigationFlowMixin": 46,
    }
    measured = _measured()
    for key, limit in ceiling.items():
        assert len(measured[key]) <= limit, (
            f"{key} 要的宿主成员从 {limit} 涨到 {len(measured[key])} —— "
            "这一间已经是耦合最深的几间之一，它该拆了而不是再长"
        )


def test_this_guard_would_notice_a_new_dependency() -> None:
    """这一份自己必须验过一次 —— 扫 AST 的回归测试最常见的失效是「扫零个文件而恒绿」。"""
    measured = _measured()
    assert len(measured) == len(HOST_SURFACE) >= 18, "扫到的 mixin 数量对不上（路径算错了？）"
    # Qt 基类的方法**不算**耦合 —— 判据算错的话这一条会立刻变成误报满天飞。
    assert "_grid" in measured["write_ops.py:WriteOpsMixin"], "判据根本没在工作"
    assert "setGeometry" not in measured["window/title_bar_flow.py:TitleBarFlowMixin"]


#: ``mypy --strict src/gallery/ui/window`` 的实测值（ADR 0060 搬完模块之后）。
#:
#: **这个数从 921 涨到了这里，而耦合面从 489 降到了 457** —— 两件事不矛盾，因为它们量的
#: 不是同一件事：ADR 0058 把状态收进对象之后，转发用的 **property 只对它自己那间可见**，
#: 于是「用到一个转发名字」的 mixin 单独查时又看不见它了。**声明去重了，报错就回来了。**
#:
#: 上一次涨是 ADR 0058 的七个阶段；这一次是 **ADR 0059/0060 往 ``window/`` 里搬了八个模块**，
#: 而这一条扫的正是这个目录 —— 多扫进来几间，就多几处「mixin 里的 ``self`` 静态上不是
#: ``QWidget``」。新增的四条全是那一族（``Key_Return`` / ``Key_Escape`` / ``menuAction`` /
#: ``widgetAt``），没有一条是新性质。
#:
#: **该盯的数字是耦合面（上面那张表，457）与组合根（0 条）**，不是这个总数。
#: 留着的意义只是**锁定基线** —— 「比上次多了几条、多了在哪」这个问题得有人问。
#:
#: 它和上面那张表量的是同一片依赖，但不是一个数：表里是**不重复的成员**，而 mypy 报的是
#: **使用点**且按它自己的口径逐类去重。所以这里**不做交叉断言**。
MYPY_TOTAL_ERRORS = 841
MYPY_ATTR_DEFINED = 789

#: 真跑一次 mypy 的开关。跑一次要几秒，而这一份每次测试都会跑 —— **别每次都跑**。
UI_WINDOW_MYPY_RECHECK = "UI_WINDOW_MYPY_RECHECK"


def _mypy_errors() -> tuple[int, int]:
    """跑一次 ``mypy --strict src/gallery/ui/window``，返回 ``(总数, attr-defined 条数)``。"""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src/gallery/ui/window", "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        for code in re.findall(r"error: .*\[([a-z-]+)\]$", line):
            counts[code] = counts.get(code, 0) + 1
    return sum(counts.values()), counts.get("attr-defined", 0)


def test_the_composition_root_is_type_clean() -> None:
    """**``main_window.py``（组合根）在 ``--strict`` 下一条不报** —— 守的是「那一层组装得起来」。

    它 import 全部 mixin，全部字段声明都在它的 ``__init__`` 里，所以同一字段在两个基类里
    类型不一致这类错，在这里才会报出来（逐个 mixin 单独查时它们互相看不见，反而不报）。

    **它不证明 mixin 的函数体干净** —— mypy 只报目标文件自己的错，被 import 的模块只是被
    跟进来做推导。实测：在 ``filters.py`` 里注入一条 ``return "x"`` 给 ``-> int`` 的错，
    这一条仍然 ``Success``；要看后者得用 ``mypy --strict src/gallery/ui``（ADR 0056 后果）。
    """
    if os.environ.get(UI_WINDOW_MYPY_RECHECK) != "1":
        pytest.skip("要真跑 mypy：设 UI_WINDOW_MYPY_RECHECK=1")
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src/gallery/ui/main_window.py", "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "组合根在 --strict 下不干净了：\n" + "\n".join(
        line for line in result.stdout.splitlines() if " error: " in line
    )


#: 已经按 ADR 0057 补完声明、``--strict`` 下**一条不报**的那些 mixin。
#: 每一间补完就把文件名加进来 —— 这个清单只会变长。
TYPED_MODULES = ("library_flow.py",)


def test_the_finished_modules_stay_type_clean() -> None:
    """**补完声明的那几间，一条都不许再报** —— 清单只增不减。

    与 ``test_the_composition_root_is_type_clean`` 盯的不是同一件事：那一间守的是
    「组装得起来」，这一间守的是「**这个文件自己**干净」。ADR 0057 就是按这个清单
    一间一间往前推的，每补完一间加一行。
    """
    if os.environ.get(UI_WINDOW_MYPY_RECHECK) != "1":
        pytest.skip("要真跑 mypy：设 UI_WINDOW_MYPY_RECHECK=1")
    dirty: list[str] = []
    for name in TYPED_MODULES:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", f"src/gallery/ui/window/{name}", "--strict"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            dirty.append(name)
            print(f"--- {name}\n" + result.stdout)
    assert not dirty, (
        f"这几间已经补完声明了，不该再报：{dirty}。"
        "新出现的多半是**真问题**（类型变了、或者用了还没声明的宿主成员）—— 别用 ignore 盖掉。"
    )


def test_the_mypy_baseline_is_still_current() -> None:
    """**锁定的那两个数字与实测吻合** —— 默认不跑 mypy（要几秒）。

    带 ``UI_WINDOW_MYPY_RECHECK=1`` 时真跑一遍：报的是「比上次多了几条」。
    **剩下的** ``MYPY_TOTAL_ERRORS - MYPY_ATTR_DEFINED`` 条不是这一张表 —— 那是
    ``attr-defined`` 的下游（字段看不见 → 类型推不出来）与「mixin 里的 ``self`` 静态上
    不是 ``QWidget``」那一族（``QMessageBox`` / ``QFileDialog`` / ``QAction`` 传 ``self``），
    ADR 0056 记着它们各自为什么留着。
    """
    if os.environ.get(UI_WINDOW_MYPY_RECHECK) != "1":
        return
    total, attr_defined = _mypy_errors()
    assert attr_defined == MYPY_ATTR_DEFINED, (
        f"[attr-defined] 从 {MYPY_ATTR_DEFINED} 变成 {attr_defined}"
        f"（{attr_defined - MYPY_ATTR_DEFINED:+d}）"
    )
    assert total == MYPY_TOTAL_ERRORS, (
        f"总数从 {MYPY_TOTAL_ERRORS} 变成 {total}（{total - MYPY_TOTAL_ERRORS:+d}）—— "
        f"其中不是 [attr-defined] 的那 {MYPY_TOTAL_ERRORS - MYPY_ATTR_DEFINED} 条里多了或少了东西，"
        "那些是**真的**类型问题。"
    )
