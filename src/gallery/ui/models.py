"""网格视图的数据源。

模型只保管 ``PhotoRef``、缩略图的 LRU 与必要的展示字段，**不负责解码**：
需要哪张图由视图通知（``viewport_changed``），结果由 :meth:`set_thumbnail` 回填。
因此 ``data()`` 永远不会阻塞，滚动时不会出现「卡一下才出图」。

缩略图在这里被缩放到图标尺寸再缓存 —— 一次平滑缩放，换来每次重绘都省下的一次缩放。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QSize, Qt
from PySide6.QtGui import QImage, QPixmap

from gallery.core.photos import PhotoRef
from gallery.ui.formatting import photo_tooltip
from gallery.ui.thumb_sizes import DEFAULT_EDGE

#: 取 ``PhotoRef`` 的自定义角色。
PHOTO_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: 取路径字符串的自定义角色（拖放、调试用）。
PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 2

#: 默认缩略图边长，与档位定义（``ui.thumb_sizes``）同源。
DEFAULT_ICON_EDGE = DEFAULT_EDGE

#: 内存中最多保留多少张已缩放的缩略图。按 176px 估算约 30 MB。
DEFAULT_PIXMAP_CACHE = 600


class PhotoListModel(QAbstractListModel):
    """网格的数据源。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        icon_edge: int = DEFAULT_ICON_EDGE,
        cache_size: int = DEFAULT_PIXMAP_CACHE,
    ) -> None:
        super().__init__(parent)
        self._refs: list[PhotoRef] = []
        self._row_by_path: dict[Path, int] = {}
        self._pixmaps: OrderedDict[Path, QPixmap] = OrderedDict()
        self._icon_edge = icon_edge
        self._cache_size = max(1, cache_size)

    # ---- 只读属性 ----------------------------------------------------------

    @property
    def icon_size(self) -> QSize:
        return QSize(self._icon_edge, self._icon_edge)

    def refs(self) -> tuple[PhotoRef, ...]:
        return tuple(self._refs)

    def ref_at(self, row: int) -> PhotoRef | None:
        if 0 <= row < len(self._refs):
            return self._refs[row]
        return None

    def row_of_index(self, index: QModelIndex) -> int:
        return index.row() if index.isValid() else -1

    # ---- 数据装载 ----------------------------------------------------------

    def append(self, refs: Sequence[PhotoRef]) -> None:
        """追加一批照片（扫描是流式的，界面不等整库扫完就能出图）。"""
        if not refs:
            return
        start = len(self._refs)
        self.beginInsertRows(QModelIndex(), start, start + len(refs) - 1)
        for offset, ref in enumerate(refs):
            self._row_by_path[ref.path] = start + offset
            self._refs.append(ref)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._refs.clear()
        self._row_by_path.clear()
        self._pixmaps.clear()
        self.endResetModel()

    def set_thumbnail(self, path: Path, image: QImage) -> None:
        """回填一张缩略图（必须从主线程调用）。"""
        row = self._row_by_path.get(path)
        if row is None:
            return  # 照片已经被切库清掉了，直接丢弃
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > self._icon_edge or pixmap.height() > self._icon_edge:
            pixmap = pixmap.scaled(
                self._icon_edge,
                self._icon_edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._pixmaps[path] = pixmap
        self._pixmaps.move_to_end(path)
        while len(self._pixmaps) > self._cache_size:
            self._pixmaps.popitem(last=False)
        self._notify_row(row)

    # ---- QAbstractListModel 接口 -------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 - Qt 命名
        if parent is not None and parent.isValid():
            return 0
        return len(self._refs)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid():
            return None
        row = index.row()
        if not 0 <= row < len(self._refs):
            return None
        ref = self._refs[row]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return ref.name
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return photo_tooltip(ref)
        if role == int(Qt.ItemDataRole.DecorationRole):
            # 没有缩略图时返回 None，由委托画占位卡片，模型不阻塞等待。
            return self._pixmaps.get(ref.path)
        if role == PHOTO_ROLE:
            return ref
        if role == PATH_ROLE:
            return str(ref.path)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return None

    def set_icon_edge(self, edge: int) -> None:
        """切换缩略图尺寸。

        内存里缓存的缩略图是按旧尺寸缩放的，尺寸一变就全部作废：丢掉它们，
        交给视图重新请求 —— 磁盘缓存还在，重读一次很快。
        """
        if edge == self._icon_edge:
            return
        self._icon_edge = edge
        self.clear_thumbnails()

    def has_thumbnail(self, path: Path) -> bool:
        """这张照片当前是否已经有可画的缩略图。"""
        return path in self._pixmaps

    def clear_thumbnails(self) -> None:
        """丢掉内存里缓存的缩略图（清空磁盘缓存后调用，让它们重新生成）。"""
        self._pixmaps.clear()
        if not self._refs:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._refs) - 1, 0),
            [int(Qt.ItemDataRole.DecorationRole)],
        )

    def _notify_row(self, row: int) -> None:
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.DecorationRole)])


__all__ = [
    "DEFAULT_ICON_EDGE",
    "DEFAULT_PIXMAP_CACHE",
    "PATH_ROLE",
    "PHOTO_ROLE",
    "PhotoListModel",
]
