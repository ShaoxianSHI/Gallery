"""缩略视图的数据源。

模型保管的是**一层里的条目**：相册（子文件夹）与照片混在一起，相册在前。
它只负责展示字段与缩略图缓存，**不负责解码**：需要哪张图由视图通知
（``viewport_changed``），结果由 :meth:`set_thumbnail` 回填，所以 ``data()`` 永不阻塞。

缩略图在这里被缩放到图标尺寸再缓存 —— 一次平滑缩放，换来每次重绘都省下的一次缩放。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QSize, Qt
from PySide6.QtGui import QImage, QPixmap

from gallery.core.photos import FolderRef, GridEntry, PhotoRef
from gallery.ui.formatting import photo_tooltip
from gallery.ui.thumb_sizes import DEFAULT_EDGE

#: 取条目本身（``PhotoRef`` 或 ``FolderRef``）的角色。
ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: 取路径字符串的角色（调试用）。
PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 2

#: 这个格子是不是相册。
ALBUM_ROLE = int(Qt.ItemDataRole.UserRole) + 3

#: 默认缩略图边长，与档位定义（``ui.thumb_sizes``）同源。
DEFAULT_ICON_EDGE = DEFAULT_EDGE

#: 内存中最多保留多少张已缩放的缩略图。按 176px 估算约 30 MB。
DEFAULT_PIXMAP_CACHE = 600


@dataclass(slots=True)
class _CachedThumb:
    """一张已缩放的缩略图，连同它当时是按哪个边长缩放的。"""

    pixmap: QPixmap
    edge: int


class PhotoListModel(QAbstractListModel):
    """一层内容（相册 + 照片）的数据源。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        icon_edge: int = DEFAULT_ICON_EDGE,
        cache_size: int = DEFAULT_PIXMAP_CACHE,
    ) -> None:
        super().__init__(parent)
        self._entries: list[GridEntry] = []
        self._row_by_path: dict[Path, int] = {}
        self._pixmaps: OrderedDict[Path, _CachedThumb] = OrderedDict()
        self._tooltips_enabled = True
        self._icon_edge = icon_edge
        self._cache_size = max(1, cache_size)

    # ---- 只读属性 ----------------------------------------------------------

    @property
    def icon_size(self) -> QSize:
        return QSize(self._icon_edge, self._icon_edge)

    def entries(self) -> tuple[GridEntry, ...]:
        return tuple(self._entries)

    def entry_at(self, row: int) -> GridEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def photo_at(self, row: int) -> PhotoRef | None:
        """第 ``row`` 行如果是照片就返回它，相册或越界返回 ``None``。"""
        entry = self.entry_at(row)
        return entry if isinstance(entry, PhotoRef) else None

    def counts(self) -> tuple[int, int]:
        """``(相册数, 照片数)``。"""
        albums = sum(1 for entry in self._entries if isinstance(entry, FolderRef))
        return albums, len(self._entries) - albums

    def row_of_index(self, index: QModelIndex) -> int:
        return index.row() if index.isValid() else -1

    # ---- 数据装载 ----------------------------------------------------------

    def append(self, entries: Sequence[GridEntry]) -> None:
        """追加一批条目（扫描是流式的，界面不等整层扫完就能出图）。"""
        if not entries:
            return
        start = len(self._entries)
        self.beginInsertRows(QModelIndex(), start, start + len(entries) - 1)
        for offset, entry in enumerate(entries):
            self._row_by_path[entry.path] = start + offset
            self._entries.append(entry)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._entries.clear()
        self._row_by_path.clear()
        self._pixmaps.clear()
        self.endResetModel()

    def set_thumbnail(self, path: Path, image: QImage) -> None:
        """回填一张缩略图（相册的缩略图就是它的封面）。必须从主线程调用。"""
        row = self._row_by_path.get(path)
        if row is None:
            return  # 条目已经被换层清掉了，直接丢弃
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > self._icon_edge or pixmap.height() > self._icon_edge:
            pixmap = pixmap.scaled(
                self._icon_edge,
                self._icon_edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._pixmaps[path] = _CachedThumb(pixmap=pixmap, edge=self._icon_edge)
        self._pixmaps.move_to_end(path)
        while len(self._pixmaps) > self._cache_size:
            self._pixmaps.popitem(last=False)
        self._notify_row(row)

    def set_icon_edge(self, edge: int) -> None:
        """切换缩略图尺寸。

        **刻意不清空已有缩略图**：清空会让整屏先闪成占位框、再一张张弹回来。
        旧图由委托按新尺寸缩放显示（略软），新图到了原地替换，用户看不到跳变。
        哪些条目需要按新尺寸重取，由 :meth:`needs_thumbnail` 回答。
        """
        self._icon_edge = edge

    def set_tooltips_enabled(self, enabled: bool) -> None:
        """网格不在屏幕上时关掉悬停提示，回来时再打开。"""
        if enabled == self._tooltips_enabled:
            return
        self._tooltips_enabled = enabled
        if not self._entries:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._entries) - 1, 0)
        self.dataChanged.emit(top_left, bottom_right, [int(Qt.ItemDataRole.ToolTipRole)])

    def has_thumbnail(self, path: Path) -> bool:
        """这个条目是否有可画的东西（哪怕是按旧尺寸缩放的）。"""
        return path in self._pixmaps

    def thumbnail_pixmap(self, path: Path) -> QPixmap | None:
        """这张照片已经缓存好的缩略图（没有就返回 ``None``）。

        打开大图时先用它顶上：立刻显示的是**正确那张照片**，只是尺寸小一些 ——
        比留着上一张照片、或闪一下黑屏都好。
        """
        cached = self._pixmaps.get(path)
        return cached.pixmap if cached is not None else None

    def needs_thumbnail(self, path: Path) -> bool:
        """这个条目是否需要（重新）取缩略图：没有，或手里的不是当前尺寸。"""
        cached = self._pixmaps.get(path)
        return cached is None or cached.edge != self._icon_edge

    def clear_thumbnails(self) -> None:
        """丢掉内存里缓存的缩略图（清空磁盘缓存后调用，让它们重新生成）。"""
        self._pixmaps.clear()
        if not self._entries:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._entries) - 1, 0),
            [int(Qt.ItemDataRole.DecorationRole)],
        )

    # ---- QAbstractListModel 接口 -------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 - Qt 命名
        if parent is not None and parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid():
            return None
        row = index.row()
        if not 0 <= row < len(self._entries):
            return None
        entry = self._entries[row]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return entry.name
        if role == int(Qt.ItemDataRole.ToolTipRole):
            # 大图打开时不提供提示：Qt 已经排好的悬停定时器醒来问不到内容，
            # 就不会在照片上凭空闪出一个小窗（见 MainWindow._load_detail）。
            return _tooltip(entry) if self._tooltips_enabled else ""
        if role == int(Qt.ItemDataRole.DecorationRole):
            # 还没有缩略图时返回 None，由委托画占位（相册画文件夹图标），模型不阻塞等待。
            cached = self._pixmaps.get(entry.path)
            return cached.pixmap if cached is not None else None
        if role == ENTRY_ROLE:
            return entry
        if role == PATH_ROLE:
            return str(entry.path)
        if role == ALBUM_ROLE:
            return isinstance(entry, FolderRef)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _notify_row(self, row: int) -> None:
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.DecorationRole)])


def _tooltip(entry: GridEntry) -> str:
    if isinstance(entry, PhotoRef):
        return photo_tooltip(entry)
    return "\n".join((entry.display_name, "相册", str(entry.path)))


__all__ = [
    "ALBUM_ROLE",
    "DEFAULT_ICON_EDGE",
    "DEFAULT_PIXMAP_CACHE",
    "ENTRY_ROLE",
    "PATH_ROLE",
    "PhotoListModel",
]
