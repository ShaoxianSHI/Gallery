"""分组 / 排序这一族的状态（ADR 0058 阶段 2）。

三样东西住在这儿：**用户选了什么**、**这一层读到了什么**、**上一次重排的输入**。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core.events.models import EventRef
from gallery.core.library.config import DEFAULT_GROUP_LAYOUT, DEFAULT_SORT_FACET
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.facets import PhotoFacets
from gallery.core.places.resolver import LazyPlaceResolver

__all__ = ["OrderingState"]


class OrderingState:
    """分组 / 排序这一族的**唯一存储**（ADR 0058 阶段 2）。

    窗口上仍留着一组同名 property 转发 —— 属性名一个字都不改（测试直接摸它们），
    而**转发不产生第二份存储**：真正的值只在本对象里。
    """

    #: 分组字段 id（``None`` = 不分组）。存进库的 ``config.toml``，下次打开沿用。
    _group_facet: str | None
    #: 分组形态（卡片式 / 分区式，ADR 0089）。同样是库级偏好。
    _group_layout: str
    #: 排序字段 id。**恒为真字段 id** —— 菜单里没有「不排序」可退回（``set_sort_facet`` 对
    #: ``None`` 直接 return，``resolved_sort_facet`` 也交回一个能用的值）。
    _sort_facet: str
    #: 降序就是字面的 ``reverse``；所有字段默认一律升序（ADR 0049）。
    _sort_descending: bool
    #: 这一层的照片各有哪些 facet 值。跟着 facet 那个后台任务回来，不在主线程现查。
    _facet_values: dict[Path, PhotoFacets]
    #: 这一层的照片各属于哪些事件（ADR 0043）。**只给「按事件分组」看**。
    _event_values: dict[Path, tuple[EventRef, ...]]
    #: 整库地名解析器。**用起来才造**，所以一开始是 ``None``。
    _place_resolver: LazyPlaceResolver | None
    # guard: allow-long-comment（上一轮重排的五个输入是一组，拆开就没法看出它们必须同时换）
    #: **上一次重排的输入** —— 用来在排序**之前**判「这次会不会变」
    #: （见 ``ordering._reorder_level``），少了它监听器每报一次自己写操作的回环
    #: 就要白付一次整层排序。``_reorder_values`` 存的是**对象本身**（不是 ``id()``），
    #: 这样它不会被回收、地址不会被复用，判 ``is`` 才靠得住。
    _reorder_fingerprint: tuple[object, ...] | None
    _reorder_values: dict[Path, PhotoFacets] | None
    _reorder_events: dict[Path, tuple[EventRef, ...]] | None
    _reorder_photos: tuple[PhotoRef, ...]
    _reorder_albums: tuple[FolderRef, ...]

    def __init__(self) -> None:
        """初始状态：不分组、按默认字段升序、这一层什么都还没读。"""
        self._group_facet = None
        self._group_layout = DEFAULT_GROUP_LAYOUT
        self._sort_facet = DEFAULT_SORT_FACET
        self._sort_descending = False
        self._facet_values = {}
        self._event_values = {}
        self._place_resolver = None
        self._reorder_fingerprint = None
        self._reorder_values = None
        self._reorder_events = None
        self._reorder_photos = ()
        self._reorder_albums = ()

    def forget_reorder_input(self) -> None:
        """**忘掉上一次重排的输入** —— 换库时调。

        不清的话，下次重排会拿上一个库的 ``_facet_values`` 判「输入没变」而跳过整层排序，
        症状是**换库之后这一层的顺序是上一个库的**。
        """
        self._facet_values = {}
        self._event_values = {}
        self._reorder_fingerprint = None
        self._reorder_values = None
        self._reorder_events = None
        self._reorder_photos = ()
        self._reorder_albums = ()
