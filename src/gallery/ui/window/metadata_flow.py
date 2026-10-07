"""右侧那块元数据面板：显示当前这张照片的什么，以及地点是怎么得出的。

⌘I 展开 / 收起，面板照着**当前这一张**显示 EXIF（读一次约 50ms，命中缓存直接显示，没命中
就说「正在读取」—— **绝不把上一张的元数据留在屏幕上**，那是错的照片的数据）。

**地点**是按坐标反查出来的，必须与分组显示的地点同一个说法（ADR 0010），查法是「先读索引，
索引里没有才现场反查」。「编辑元数据」那个按钮只发信号（面板不碰文件，红线 1）。
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QModelIndex

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef
from gallery.core.metadata.exif import read_metadata
from gallery.core.metadata.facets import PhotoFacets
from gallery.core.metadata.report import MetadataReport
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import is_real_coordinate
from gallery.ui.tasks import TaskContext
from gallery.ui.window.level_stack import PhotoFrame

#: 离最近的城市超过这么多公里，就在地点后面补一句距离。标签本身已经写了「（推断）」；
#: 偏得远时才补 —— 那是在提醒「这只是最近的城市」，不是在报误差。
PLACE_DISTANCE_NOTICE_KM = 50.0


def _place_text(facets: PhotoFacets) -> str:
    """索引里那一行的地点 → 面板上显示的一句话。

    拼装用 ``places.place_label``，与反查结果自己的 ``Place.label()`` 是同一个函数 ——
    面板和分组显示的必须是同一个说法（ADR 0010）。
    """
    label = place_label(facets.city, facets.region, facets.country)
    if not label:
        return "坐标附近没有城市记录"
    distance = facets.place_distance_km
    if distance is None or distance < PLACE_DISTANCE_NOTICE_KM:
        return label
    return f"{label}（最近城市约 {distance:.0f} 公里外）"


class MetadataFlowMixin:
    """元数据面板的显隐、读取与缓存，以及按坐标反查出来的地点。"""

    # ---- 元数据面板、地点与进入
    def toggle_metadata(self, visible: bool) -> None:
        """⌘I：展开 / 收起右侧的元数据面板，并把选择记进会话。"""
        self._metadata_visible = visible
        self._panel.setVisible(visible)
        self._save_ui_state()
        self._layout_banner()
        if visible:
            self._load_metadata()
        else:
            self._cancel_metadata_job()

    def _update_metadata_editable(self) -> None:
        """面板上「编辑位置」那一处此刻摆什么。

        **能改就摆按钮，不能改就把那句话摆在那里**（ADR 0014 决策 4）：一个点下去只会报错的
        按钮比没有更糟，而只字不提又会让用户以为 Gallery 压根不管这件事。
        """
        library = self._library
        if library is not None and library.can_write_metadata():
            self._panel.set_metadata_editable(True)
            return
        self._panel.set_metadata_editable(
            False,
            reason="需要 exiftool 才能修改照片里的位置。安装后即可使用：brew install exiftool",
        )

    def _cancel_metadata_job(self) -> None:
        if self._metadata_job is not None:
            self._runner.cancel(self._metadata_job)
            self._metadata_job = None
        if self._metadata_copy_job is not None:
            self._runner.cancel(self._metadata_copy_job)
            self._metadata_copy_job = None

    def _load_metadata(self) -> None:
        """读当前这张照片的元数据；面板没展开就什么都不做。

        命中缓存就同步显示；没命中就老实说「正在读取」，**不要把上一张的元数据留在屏幕上**
        —— 用户会把错的照片的数据当成当前这张的信息看。
        """
        self._cancel_metadata_job()
        if not self._panel.isVisible():
            return
        row = self._current_row
        ref = self._model.photo_at(row)
        if ref is None:
            self._panel.clear()
            return
        generation = self._generation
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._panel.set_report(cached)
            self._prefetch_metadata(row)
            # 缓存里的报告可能还没推断过地点（预读只读元数据）：漏了这一步，
            # 带坐标的照片永远不显示地点。
            self._maybe_lookup_place(ref, cached, generation, announce=False)
            return
        self._panel.show_loading(ref.name)

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._metadata_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_ready, row, generation),
            on_error=partial(self._on_metadata_failed, row, generation),
        )

    def _on_metadata_ready(self, row: int, generation: int, payload: object) -> None:
        if not isinstance(payload, MetadataReport):
            return
        ref = self._model.photo_at(row)
        if ref is not None:
            self._cache_metadata(ref, payload)
        if generation != self._generation or row != self._current_row:
            return  # 用户已经翻到别处了，这份结果只进缓存，不上屏
        self._panel.set_report(payload)
        self._prefetch_metadata(row)
        self._maybe_lookup_place(ref, payload, generation, announce=True)

    def _on_metadata_failed(self, row: int, generation: int, message: str) -> None:
        if generation != self._generation or row != self._current_row:
            return
        self._panel.show_error(message)

    def _maybe_lookup_place(
        self,
        ref: PhotoRef,
        report: MetadataReport,
        generation: int,
        *,
        announce: bool,
    ) -> None:
        """按坐标给出地点：**先读索引**，索引里没有才现场反查一次。

        打开库时那一遍已经把结论存进索引了（ADR 0010），所以绝大多数照片在这里是一次同步
        读缓存就显示到位。现场反查只兜两种情况：打开时那一遍还没轮到它，或者后来才加进库的
        照片；那条路走 ``Library.resolve_photo_place``，**查完并回索引**。

        ``announce`` 为真时才显示中间状态。预读与缓存命中都传 ``False``：那时面板上显示的
        是当前这张照片，拿别的照片的中间状态去替换它，用户只会看到内容闪一下。
        """
        if report.latitude is None or report.longitude is None:
            return
        if not is_real_coordinate(report.latitude, report.longitude):
            return  # 坐标是 (0,0)：那是「没定到 GPS」的占位写法，没有地点可言
        if report.place_from_file or report.place_resolved:
            return  # 文件里写了地点，或已经推断过一次（含「没有记录」这种结论）
        if not self._panel.isVisible():
            return
        library = self._library
        if library is None:
            return
        stored = library.cached_facets([ref]).get(ref.path)
        if stored is not None and stored.place_checked:
            self._show_place(ref, report, generation, _place_text(stored))
            return
        if announce:
            self._panel.set_report(report.with_place("正在查询最近的城市…"))
        resolver = self._place_lookup()
        latitude, longitude = report.latitude, report.longitude

        def work(context: TaskContext) -> object:
            return library.resolve_photo_place(
                ref,
                latitude,
                longitude,
                resolver=resolver,
                status=lambda text: context.progress(text),
            )

        # 不取消进行中的地点查询：它只往索引里写，切走了也仍然有用，切回来反而能一次显示到位。
        self._runner.submit(
            work,
            on_progress=partial(self._on_place_progress, ref, report, generation),
            on_result=partial(self._on_place_ready, ref, report, generation),
            on_error=partial(self._on_place_failed, ref, report, generation),
        )

    def _is_current_photo(self, ref: PhotoRef, generation: int) -> bool:
        """这张照片是不是当前显示的那张（换代过就不算）。"""
        if generation != self._generation:
            return False
        current = self._model.photo_at(self._current_row)
        return current is not None and current.path == ref.path

    def _on_place_progress(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        if isinstance(payload, str) and self._is_current_photo(ref, generation):
            self._panel.set_report(report.with_place(payload))

    def _on_place_ready(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        """``Library.resolve_photo_place`` 的结论：直接拿来显示。"""
        if not isinstance(payload, PhotoFacets):
            self._show_place(ref, report, generation, "未能推断地点：没有读到坐标")
            return
        self._show_place(ref, report, generation, _place_text(payload))

    def _show_place(
        self, ref: PhotoRef, report: MetadataReport, generation: int, text: str
    ) -> None:
        """把地点插进面板、入缓存，并且只在这张正是当前照片时才上屏。"""
        enriched = report.with_place(text)
        self._cache_metadata(ref, enriched)
        if self._is_current_photo(ref, generation):
            self._panel.set_report(enriched)

    def _on_place_failed(
        self, ref: PhotoRef, report: MetadataReport, generation: int, message: str
    ) -> None:
        self._show_place(ref, report, generation, f"未能推断地点：{message}")

    def _on_entry_activated(self, index: QModelIndex) -> None:
        """双击一个格子：相册或分组就进去，照片就打开大图。"""
        entry = self._model.entry_at(index.row())
        if isinstance(entry, FolderRef):
            self.enter_folder(entry.path)
        elif isinstance(entry, GroupRef):
            self.enter_group(entry)
        else:
            self._open_detail(index)

    def _open_detail(self, index: QModelIndex) -> None:
        row = index.row()
        photo = self._model.photo_at(row)
        if photo is None:
            return
        self._current_row = row
        # 照片帧只在大图页里翻页时换掉栈顶 —— 往深里压的话 Esc 要按几十次才退得回去。
        if self._photo_frame is not None:
            self._push_frame(PhotoFrame(photo.path), replace=True)
        else:
            self._push_frame(PhotoFrame(photo.path))
        self._sync_page_to_stack()
        self._detail.setFocus()
        self._update_action_states()
        self._load_detail(row)


__all__ = ["MetadataFlowMixin"]
