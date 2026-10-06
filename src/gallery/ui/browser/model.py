"""缩略视图的数据源。

模型保管**一层里的条目**（相册在前、照片在后），只负责展示字段与缩略图缓存，
``data()`` 永不阻塞。实时监听靠插行 / 摘行就地改一格、不重扫整层（ADR 0013），
所以同一个路径在网格上只可能有一行；也刻意不提供悬停提示（信息都在元数据面板里）。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QSize, Qt
from PySide6.QtGui import QImage, QPixmap

from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.ui.browser.thumb_sizes import DEFAULT_EDGE
from gallery.ui.imaging import scale_pixmap

#: 取条目本身（``PhotoRef`` 或 ``FolderRef``）的角色。
ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: 取路径字符串的角色（调试用）。
PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 2

#: 这个格子是不是相册（子文件夹）。
ALBUM_ROLE = int(Qt.ItemDataRole.UserRole) + 3

#: 这个格子是不是分组卡片（按元数据归拢的一堆照片）。
GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 4

#: 分组卡片里有多少张照片（给委托画徽标）。
GROUP_COUNT_ROLE = int(Qt.ItemDataRole.UserRole) + 5

#: 默认缩略图边长，与档位定义（``ui.thumb_sizes``）同源。
DEFAULT_ICON_EDGE = DEFAULT_EDGE

#: 内存中最多保留多少张已缩放的缩略图。**这是兜底，不是主要的那个** ——
#: 真正的上限是 :data:`PIXMAP_MEMORY_BUDGET`，而这一条留着是为了「边长极小时张数无界」
#: 那种情形（此时每张只占几 KB，按字节算会一直存下去）。
DEFAULT_PIXMAP_CACHE = 600

#: 网格那一份已缩放缩略图的字节预算（约 64 MB）。每张的实际大小是 ``边长² × 4`` 字节，只按
#: 张数封顶的话内存跟着边长的**平方**走（600 张在 176 档 66 MB、在 288 档 237 MB）。
#: 两个上限一起用：先按字节砍到预算内，同时仍然守上面的张数上限。
PIXMAP_MEMORY_BUDGET = 64 * 1024 * 1024


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
        #: 与 :attr:`_entries` **逐行平行**的排序键（见 :func:`_sort_key`）。存在的理由只有
        #: 一个：键里的 ``name.casefold()`` 不记下来就得每行现算（三万行插一行要好几秒）。
        #: **任何改动 ``_entries`` 的地方都要同步它。**
        self._keys: list[tuple[int, str]] = []
        self._row_by_path: dict[Path, int] = {}
        #: 组卡片按**封面照片**索引：一张照片可以当好几个组的封面，所以是路径 → 多个行号。
        self._group_rows_by_cover: dict[Path, list[int]] = {}
        self._pixmaps: OrderedDict[Path, _CachedThumb] = OrderedDict()
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
        """``(相册数, 照片数)``。分组卡片既不是相册也不是照片，不计入。"""
        albums = sum(1 for entry in self._entries if isinstance(entry, FolderRef))
        groups = sum(1 for entry in self._entries if isinstance(entry, GroupRef))
        return albums, len(self._entries) - albums - groups

    def group_at(self, row: int) -> GroupRef | None:
        """第 ``row`` 行如果是分组卡片就返回它。"""
        entry = self.entry_at(row)
        return entry if isinstance(entry, GroupRef) else None

    def group_count(self) -> int:
        return sum(1 for entry in self._entries if isinstance(entry, GroupRef))

    def row_of_path(self, path: Path) -> int:
        """某个路径当前在第几行，没有就返回 ``-1``。"""
        return self._row_by_path.get(Path(path), -1)

    def shows_path(self, path: Path) -> bool:
        """这一层里还有没有这个东西：作为条目本身，或者作为某个分组卡片的封面。

        「在途的缩略图结果还该不该认」只由这一处说了算：回填与失败上报是同一件事的两面。
        """
        target = Path(path)
        return target in self._row_by_path or target in self._group_rows_by_cover

    # ---- 原地更新 ----------------------------------------------------------

    def replace_entry(self, row: int, entry: GridEntry) -> bool:
        """把某一行换成新的条目引用（重命名后路径变了）。返回是否替换成功。

        **不重新扫描**：改个名字而已。已经缩放好的缩略图顺手搬到新键上，这一格连闪都不闪。
        """
        if not 0 <= row < len(self._entries) or isinstance(entry, GroupRef):
            return False
        previous = self._entries[row]
        self._row_by_path.pop(previous.path, None)
        self._entries[row] = entry
        self._keys[row] = _sort_key(entry)
        self._row_by_path[entry.path] = row
        cached = self._pixmaps.pop(previous.path, None)
        if cached is not None:
            self._pixmaps[entry.path] = cached
            self._pixmaps.move_to_end(entry.path)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [
                int(Qt.ItemDataRole.DisplayRole),
                int(Qt.ItemDataRole.DecorationRole),
            ],
        )
        return True

    # ---- 数据装载 ----------------------------------------------------------

    def remove_rows(self, rows: Sequence[int]) -> list[GridEntry]:
        """删掉若干行，返回被删掉的条目（按它们的原顺序）。

        就地摘行而不是重扫；内存里缓存的缩略图也一起丢掉 —— 那些路径上已经没有文件了。
        """
        targets = sorted({row for row in rows if 0 <= row < len(self._entries)}, reverse=True)
        removed: list[GridEntry] = []
        for row in targets:
            entry = self._entries[row]
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._entries[row]
            del self._keys[row]
            self.endRemoveRows()
            if not isinstance(entry, GroupRef):
                # 分组卡片的封面照片还在组里活着，缩略图不能跟着这一行一起丢。
                self._row_by_path.pop(entry.path, None)
                self._pixmaps.pop(entry.path, None)
            removed.append(entry)
        removed.reverse()  # 还原成它们在网格里的先后顺序
        self._reindex()
        return removed

    def remove_paths(self, paths: Iterable[Path]) -> list[GridEntry]:
        """按路径摘掉若干行，返回被摘掉的条目（按网格里的先后顺序）。

        实时监听用（ADR 0013）。分组卡片不走这条路 —— 它的行号不在 ``_row_by_path`` 里。
        """
        wanted = {Path(path) for path in paths}
        rows = sorted({row for path, row in self._row_by_path.items() if path in wanted})
        return self.remove_rows(rows)

    def forget_thumbnails(self, paths: Iterable[Path]) -> None:
        """这些路径上的文件变了，手里那张缩略图不再作数。

        只清「确实缓存过」的：没缓存过的路径不发信号，免得平白让整屏重画。
        """
        rows: set[int] = set()
        for path in {Path(item) for item in paths}:
            if self._pixmaps.pop(path, None) is None:
                continue
            row = self._row_by_path.get(path)
            if row is not None:
                rows.add(row)
            rows.update(self._group_rows_by_cover.get(path, ()))
        for row in sorted(rows):
            self._notify_row(row)

    def insert_entry(self, entry: GridEntry, *, whole_level: bool = False) -> int:
        """把一条**取回来的**条目插回它该在的行，返回那一行的行号。

        撤销删除之后用：按「同一目录内（相册在前，名字）」插到与扫描器排序一致的位置，
        一个兄弟节点都不在屏幕上时追加到末尾。

        :param whole_level: 这一层不是同一个目录时（平铺模式、分组视图）置 ``True``：
            「兄弟节点」这个前提不成立，位置要在整层里按名字找；监听往平铺层里插一张
            照片必须用它，否则会被追加到末尾而与旁边的照片错位（ADR 0013）。
        """
        if isinstance(entry, GroupRef):
            # 撤销删除取回的是磁盘上的东西，不会有分组卡片从这里回来。
            return -1
        existing = self._row_by_path.get(entry.path, -1)
        if existing >= 0:
            # 同一个路径只可能有一行：来的是一份**更新的**引用，就顺手换掉 —— 留着旧的大小
            # 与修改时间会让取缩略图的缓存 key 对不上，那一格再也回不来（ADR 0013）。
            self.replace_entry(existing, entry)
            return existing
        row = self._insert_position(entry, whole_level=whole_level)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.insert(row, entry)
        self._keys.insert(row, _sort_key(entry))
        self.endInsertRows()
        self._track(entry, row)
        self._shift_rows_from(row + 1)
        return row

    def _shift_rows_from(self, first: int) -> None:
        """插在 ``first`` 之前：**``first`` 及其后的每一行行号 +1**。

        **不要整份重建**：一次插行把两张索引整个重扫一遍，而监听器一次报一批新文件，代价会随
        行数放大。真正变的只有**后移的那一段** —— ``first`` 之前的行号一个字都没动；从 ``first``
        起往后重登记，代价与「插在哪儿」成正比（通常插在末尾，那就是零）。

        **分组卡片那本仍然整份重建**：它的值是**一个行号列表**，只会被追加，插行之后列表里那些
        旧行号全都失效了，增量改不动 —— 一本「哪些行号指向这张照片」的历史清单没法逐条 +1。
        好在分组视图里插行是少数情形（监听器往平铺层插照片时压根没有组卡片），
        而 :meth:`_group_rows_by_cover` 只在真有组卡片时才扫。
        """
        self._group_rows_by_cover = {}
        for index in range(first, len(self._entries)):
            entry = self._entries[index]
            if isinstance(entry, GroupRef):
                self._track(entry, index)
            else:
                self._row_by_path[entry.path] = index

    def append(self, entries: Sequence[GridEntry]) -> None:
        """追加一批条目（扫描是流式的，界面不等整层扫完就能出图）。

        已经在屏幕上的路径会被跳过：那张会由监听单独插进来（ADR 0013），两处都收下就会
        出现两格同一张照片。
        """
        known = set(self._row_by_path)
        fresh: list[GridEntry] = []
        for entry in entries:
            if isinstance(entry, GroupRef) or entry.path in known:
                continue
            known.add(entry.path)  # 同一批里的重复也要挡住
            fresh.append(entry)
        if not fresh:
            return
        start = len(self._entries)
        self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
        for offset, entry in enumerate(fresh):
            self._track(entry, start + offset)
            self._entries.append(entry)
            self._keys.append(_sort_key(entry))
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._entries.clear()
        self._keys.clear()
        self._row_by_path.clear()
        self._group_rows_by_cover.clear()
        self._pixmaps.clear()
        self.endResetModel()

    def reorder(self, entries: Sequence[GridEntry]) -> None:
        """把这一层按新顺序重排（分组 / 排序之后调用）。

        **刻意不清缩略图缓存**：重排只是同一批照片换位置，而缓存按路径索引，清掉等于
        让整屏从磁盘缓存重读重缩放一遍。
        """
        self.beginResetModel()
        self._entries = list(entries)
        self._keys = [_sort_key(entry) for entry in entries]
        self._reindex()
        self.endResetModel()

    def set_thumbnail(self, path: Path, image: QImage, edge: int = 0) -> None:
        """回填一张缩略图（相册与分组卡片的缩略图就是它们的封面）。必须从主线程调用。

        :param edge: 这张图是按哪个长边缩好的（见 ADR 0015）。``0`` 表示没缩过 ——
            退回按当前 :attr:`icon_edge` 在这里缩一次。**记账用的就是这个值**：边长与
            当前档位对不上时 :meth:`needs_thumbnail` 会说还需要，在途的结果会被顶替。
        """
        if not self.shows_path(path):
            return  # 条目已经被换层清掉了，直接丢弃
        row = self._row_by_path.get(path)
        group_rows = self._group_rows_by_cover.get(path, ())
        pixmap = QPixmap.fromImage(image)
        if edge > 0:
            sized = edge
        else:
            sized = self._icon_edge
            pixmap = scale_pixmap(pixmap, sized)
        self._pixmaps[path] = _CachedThumb(pixmap=pixmap, edge=sized)
        self._pixmaps.move_to_end(path)
        self._trim_pixmaps()
        if row is not None:
            self._notify_row(row)
        for group_row in group_rows:
            self._notify_row(group_row)

    def _trim_pixmaps(self) -> None:
        """淘汰到**字节预算**之内（条目数上限同时留着，见两个常量的说明）。

        只按**条目数**淘汰是不够的：每张的实际大小是 ``边长² × 4`` 字节，内存跟着边长的
        **平方**走 —— ``DEFAULT_PIXMAP_CACHE`` = 600 时，176 档约 66 MB 而 288 档是 237 MB，
        用户只按了一下「大」。
        """
        total = sum(
            item.pixmap.width() * item.pixmap.height() * 4 for item in self._pixmaps.values()
        )
        while self._pixmaps and (
            total > PIXMAP_MEMORY_BUDGET or len(self._pixmaps) > self._cache_size
        ):
            _key, dropped = self._pixmaps.popitem(last=False)
            total -= dropped.pixmap.width() * dropped.pixmap.height() * 4

    def set_icon_edge(self, edge: int) -> None:
        """切换缩略图尺寸。

        **刻意不清空已有缩略图**：清掉会让整屏先闪成占位框再一张张弹回来。旧图由委托
        按新尺寸缩放显示，新图到了原地替换。
        """
        self._icon_edge = edge

    def has_thumbnail(self, path: Path) -> bool:
        """这个条目是否有可画的东西（哪怕是按旧尺寸缩放的）。"""
        return path in self._pixmaps

    def thumbnail_pixmap(self, path: Path) -> QPixmap | None:
        """这张照片已经缓存好的缩略图（没有就返回 ``None``）。

        打开大图时先用它顶上：立刻显示的是**正确那张照片**，只是尺寸小一些。
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
        # ToolTipRole 刻意不答：卡片不提供悬停提示，理由见模块文档那一段。
        if role == int(Qt.ItemDataRole.DecorationRole):
            # 没有缩略图时返回 None，由委托画占位（相册与分组画文件夹图标），不阻塞等待。
            cached = self._pixmaps.get(_cover_of(entry))
            return cached.pixmap if cached is not None else None
        if role == ENTRY_ROLE:
            return entry
        if role == PATH_ROLE:
            return str(entry.path) if not isinstance(entry, GroupRef) else str(entry.key)
        if role == ALBUM_ROLE:
            return isinstance(entry, FolderRef)
        if role == GROUP_ROLE:
            return isinstance(entry, GroupRef)
        if role == GROUP_COUNT_ROLE:
            return f"{entry.count} 张" if isinstance(entry, GroupRef) else None
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _insert_position(self, entry: GridEntry, *, whole_level: bool = False) -> int:
        """这条条目该插在第几行：排在它后面的第一行，或末尾。

        默认只看**同一个目录**里的邻居（相册在前、同类按名字，与扫描一致）；``whole_level``
        为真时在整层里按同一把尺子找位置。已经有分组 / 排序时改由调用方重排整层 ——
        那时「排在哪里」由排出来的顺序说了算。

        ``entry.path.parent`` 与 ``name.casefold()`` 都提到了循环外：写在 ``and`` 右边就是
        每行重算一遍同一个值，而 :attr:`_keys` 让后者变成纯列表取值（三万行插一行 48 → 6 ms）。
        """
        key = _sort_key(entry)
        parent = entry.path.parent
        keys = self._keys
        position = len(self._entries)
        for row, other in enumerate(self._entries):
            if isinstance(other, GroupRef):
                continue  # 组卡片不是兄弟节点
            if not whole_level and other.path.parent != parent:
                continue  # 不相邻的目录插在它前后都不对
            if keys[row] > key:
                return row  # 第一个排在它后面的那一行，就是它该待的位置
            position = row + 1
        return position

    def _reindex(self) -> None:
        """删行 / 插行之后行号全变了，重建路径到行号的映射。"""
        self._row_by_path = {}
        self._group_rows_by_cover = {}
        for row, entry in enumerate(self._entries):
            self._track(entry, row)

    def _track(self, entry: GridEntry, row: int) -> None:
        """把一条条目登记到行号索引里。"""
        if isinstance(entry, GroupRef):
            cover = _cover_of(entry)
            self._group_rows_by_cover.setdefault(cover, []).append(row)
        else:
            self._row_by_path[entry.path] = row

    def _notify_row(self, row: int) -> None:
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.DecorationRole)])


def _sort_key(entry: GridEntry) -> tuple[int, str]:
    """网格里的排序键：相册在前，同一类按名字。"""
    return (0 if isinstance(entry, FolderRef) else 1, entry.name.casefold())


def _cover_of(entry: GridEntry) -> Path:
    """这一格画哪张照片当封面：分组卡片用组内第一张，其余就是它自己。"""
    if isinstance(entry, GroupRef):
        cover = entry.cover
        return cover.path if cover is not None else entry.path
    return entry.path


__all__ = [
    "ALBUM_ROLE",
    "DEFAULT_ICON_EDGE",
    "DEFAULT_PIXMAP_CACHE",
    "ENTRY_ROLE",
    "GROUP_COUNT_ROLE",
    "GROUP_ROLE",
    "PATH_ROLE",
    "PhotoListModel",
]
