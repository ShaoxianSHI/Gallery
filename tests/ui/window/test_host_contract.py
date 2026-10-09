"""每个 mixin **需要宿主提供哪几十样东西**钉在这一份（ADR 0055）。

那十几条 mixin 之间没有任何接口声明：`LibraryFlowMixin` 要 74 样窗口上的东西、
`ContextMenuFlowMixin` 54 —— 而这个数字原先**没有任何地方写着**。用到了什么、
是 MainWindow 给的还是别的 mixin 给的，只存在于读代码的人的脑子里，于是
「这一间到底和窗口耦合多深」没人能回答，**它只会随着新功能悄悄长**。

所以这里钉两件事：**清单与代码一致**（用到一个新的宿主成员就要更新它，于是那处改动
会被看见）与**总量不许变大**（第二道）。

**这不是类型检查**：它不知道那些成员的**类型**，也不检查方法体。它换来的是
「耦合面可见 + 不可静默增长」，而真正要的类型检查是另一件事 —— ADR 0055 记了它要多少
工作量（`ui/window` 开 `--strict` 是 948 条，其中 863 条是「这份清单」）以及为什么
这一轮没做。
"""

from __future__ import annotations

import ast
import pathlib
import sys

#: 每个 mixin 的宿主耦合面 —— **这份表就是那道**。按需求从多到少排：前四间是耦合最深的，
#: 而它们也正是最该定期问一句「这一间还该存在吗」的地方。
#:
#: 想加一个成员请先回答：这样东西能不能不放在窗口上？
HOST_SURFACE: dict[str, frozenset[str]] = {
    "window/library_flow.py:LibraryFlowMixin": frozenset(
        {
            "_apply_thumbnail_size",
            "_arm_focus",
            "_banner",
            "_banner_text",
            "_browse_mode",
            "_cache_base",
            "_calendar_level",
            "_calendar_page",
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
            "_focus_state",
            "_generation",
            "_group_facet",
            "_group_view",
            "_is_library_search",
            "_legacy_ui",
            "_level_stack",
            "_library",
            "_library_title",
            "_maybe_start_photo_index",
            "_metadata_action",
            "_metadata_cache",
            "_metadata_visible",
            "_model",
            "_on_calendar_page",
            "_on_thumbnail_failed",
            "_on_thumbnail_ready",
            "_order_now",
            "_orphan_job",
            "_panel",
            "_pending_group",
            "_pipeline",
            "_place_job",
            "_place_lookup",
            "_place_pass_roots",
            "_preview_cache",
            "_recent_menu",
            "_refresh_thumbnail_window",
            "_refresh_undo_label",
            "_reload_level",
            "_report_error",
            "_runner",
            "_scan_job",
            "_search_scope",
            "_session",
            "_session_base",
            "_set_root",
            "_show_banner",
            "_show_calendar",
            "_size_index",
            "_sort_descending",
            "_sort_facet",
            "_staged",
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
            "_watch_pending",
            "_welcome",
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
            "_descending_action",
            "_duplicate_action",
            "_edit_event_action",
            "_edit_metadata_action",
            "_ensure_event_actions",
            "_focus_row",
            "_generation",
            "_grid",
            "_group_actions",
            "_in_detail_page",
            "_leave_event_action",
            "_library",
            "_metadata_action",
            "_metadata_copy_job",
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
            "_select_all_action",
            "_selected_entries",
            "_selected_paths",
            "_set_cover_action",
            "_show_banner",
            "_size_actions",
            "_sort_actions",
            "_start_job",
            "_sync_ui",
            "_trash_action",
            "_up_action",
            "cover_candidate",
            "covered_album",
            "covered_event",
            "metadata_edit_candidate",
            "record_as_candidate",
            "set_event_cover_menu",
        }
    ),
    "window/ordering.py:OrderingMixin": frozenset(
        {
            "_banner",
            "_banner_text",
            "_browse_mode",
            "_descending_action",
            "_event_values",
            "_facet_job",
            "_facet_values",
            "_feed_calendar",
            "_filters",
            "_flush_pending_watch",
            "_generation",
            "_grid",
            "_group_actions",
            "_group_facet",
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
            "_place_resolver",
            "_pop_frame",
            "_pop_to",
            "_push_frame",
            "_refresh_thumbnail_window",
            "_reload_level",
            "_reorder_albums",
            "_reorder_events",
            "_reorder_fingerprint",
            "_reorder_photos",
            "_reorder_values",
            "_restore_focus",
            "_save_ui_state",
            "_show_banner",
            "_sort_actions",
            "_sort_descending",
            "_sort_facet",
            "_staged",
            "_start_job",
            "_start_level_filter",
            "_sync_ui",
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
            "_staged",
            "_start_scan",
            "_sync_calendar_conditions",
            "_sync_search_frame",
            "_sync_ui",
            "leave_detail_page",
        }
    ),
    "window/calendar_flow.py:CalendarFlowMixin": frozenset(
        {
            "_apply_ordering",
            "_arm_focus",
            "_banner",
            "_banner_text",
            "_browse_mode",
            "_calendar_job",
            "_calendar_level",
            "_calendar_page",
            "_cancel_condition_edit",
            "_cancel_job",
            "_cancel_tier",
            "_clear_progress_notice",
            "_detail",
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
            "_staged",
            "_start_facet_read",
            "_start_job",
            "_start_scan",
            "_sync_ui",
            "_update_level_notice",
            "_update_mode_actions",
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
            "_current_row",
            "_detail_trashed",
            "_facet_values",
            "_focus_state",
            "_generation",
            "_grid",
            "_group_view",
            "_in_detail_page",
            "_library",
            "_load_detail",
            "_load_metadata",
            "_metadata_cache",
            "_metadata_copy_job",
            "_model",
            "_note_self_write",
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
    "window/search_flow.py:SearchFlowMixin": frozenset(
        {
            "_apply_ordering",
            "_banner_text",
            "_conditions",
            "_editing_condition",
            "_facet_job",
            "_filter_job",
            "_filters",
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
            "_scope_action",
            "_search_field",
            "_search_frame",
            "_search_job",
            "_search_scope",
            "_search_total",
            "_show_banner",
            "_staged",
            "_start_job",
            "_sync_ui",
            "pop_last_condition",
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
            "_level_shows_group_cards",
            "_library",
            "_load_detail",
            "_metadata_cache",
            "_model",
            "_on_grid_page",
            "_pending_group",
            "_pipeline",
            "_pop_to",
            "_preview_cache",
            "_refresh_thumbnail_window",
            "_reload_level",
            "_scan_job",
            "_select_after_removal",
            "_show_banner",
            "_shown_entry_key",
            "_staged",
            "_start_facet_read",
            "_update_level_notice",
            "_watch_echoes",
            "_watch_pending",
            "enter_group",
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
            "_start_job",
            "_sync_page_to_stack",
            "_sync_ui",
            "_title_bar",
            "go_up",
            "set_browse_mode",
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
    "window/event_flow.py:EventFlowMixin": frozenset(
        {
            "_clear_cover_event_action",
            "_cover_event_menu",
            "_edit_event_action",
            "_event_leave_menu",
            "_grid",
            "_groups_by_event",
            "_import_shows_action",
            "_leave_event_action",
            "_library",
            "_model",
            "_refresh_undo_label",
            "_reload_level",
            "_reorder_photos",
            "_selected_entries",
            "_selected_paths",
            "_show_banner",
            "_staged",
            "_start_facet_read",
        }
    ),
    "window/thumbnail_flow.py:ThumbnailFlowMixin": frozenset(
        {
            "_banner_text",
            "_cache_job",
            "_calendar_page",
            "_generation",
            "_grid",
            "_library",
            "_model",
            "_on_calendar_page",
            "_on_grid_page",
            "_pending_size_step",
            "_pipeline",
            "_save_ui_state",
            "_show_banner",
            "_size_actions",
            "_size_index",
            "_start_job",
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
            "_scope_toggle",
            "_search_field",
            "_stack",
            "_title_bar",
            "_title_bar_drag_armed",
            "_title_bar_drag_origin",
        }
    ),
    "window/jobs.py:JobsMixin": frozenset(
        {
            "_cache_job",
            "_calendar_job",
            "_detail_job",
            "_facet_job",
            "_filter_job",
            "_index_job",
            "_metadata_copy_job",
            "_metadata_job",
            "_orphan_job",
            "_place_job",
            "_runner",
            "_scan_job",
            "_search_job",
            "_write_job",
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
    "window/state_sync.py:StateSyncMixin": frozenset(
        {
            "_refresh_title",
            "_sync_order_actions",
            "_sync_page_to_stack",
            "_update_action_states",
            "_update_breadcrumb",
        }
    ),
    "window/banner_flow.py:BannerFlowMixin": frozenset(
        {
            "_banner",
            "_banner_text",
            "_panel",
            "_refresh_thumbnail_window",
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
}


def _source_root() -> pathlib.Path:
    """``src/gallery/ui`` 那一层（``parents[3]``，理由见 ``test_state_sync`` 里同一句）。"""
    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "gallery" / "ui"
    assert root.is_dir(), f"扫的目录不存在：{root}（回归测试会静默扫零个文件而一路绿）"
    return root


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

    root = _source_root().resolve()
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
        "window/library_flow.py:LibraryFlowMixin": 73,
        "window/context_menu_flow.py:ContextMenuFlowMixin": 56,
        "window/ordering.py:OrderingMixin": 45,
        "window/navigation_flow.py:NavigationFlowMixin": 44,
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
