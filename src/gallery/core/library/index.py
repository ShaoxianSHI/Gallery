"""分组 / 排序 / 检索的本地缓存（SQLite）。**它是缓存不是数据**：删掉重扫一遍即可回到等价状态。
``facets`` 存从 EXIF 抽出的可比较值、``photos`` 存扫描时就有的东西；命中判据是
``(size, mtime_ns)`` 与扫描时一致，表结构一变就整表重建（缓存不值得迁移）。
只有读过 EXIF 的照片才有 facet 行，所以搜文件名与相册走 ``photos``（ADR 0026）。访问一把锁串起来。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Final, cast

from gallery.core import paths
from gallery.core.errors import IndexUnavailableError
from gallery.core.library.photos import PhotoRef
from gallery.core.library.search import (
    RESULT_LIMIT,
    SearchCondition,
    SearchOutcome,
    build_count_query,
    build_match_query,
    build_query,
    rows_to_refs,
)
from gallery.core.metadata.facets import PhotoFacets, relative_folder
from gallery.core.sql import file_identity

logger = logging.getLogger(__name__)

#: 建好连接之后的挂钩：把别的东西挂到同一条连接上，返回值由 :attr:`FacetIndex.attached` 原样
#: 交出去（事件库用它，ADR 0043 决策 1）。**索引不知道挂钩是什么**，所以事件那条线可以整个
#: 不传 —— 测试里不传就没有事件库。失败要由挂钩自己吞掉：索引不能因为挂钩挂了就不可用。
type AttachHook = Callable[[sqlite3.Connection, threading.Lock], object]

#: 表结构版本。表结构或存进去的结论变了就 +1（老表整表重建，缓存不值得迁移）—— 地点结论改过几轮，
#: 不升版本的话老库里那些旧地名会永远留着（``place_ok`` 记着「已经查过了」）；7 增了
#: ``hour`` / ``minute``（日历按墙上钟点摆格子，ADR 0033 决策 3）；8 增了视频的 GPS 与相机
#: （ADR 0092）—— 老行里没有这两栏，不整表重建就永远读不到。
#: 后加的 ``photos`` 表**没有**升版本（ADR 0026 决策 3）：语句全带 ``IF NOT EXISTS``，老库连上来
#: 自动多出这张表；将来要**改**它的列时那才升版本。
SCHEMA_VERSION: Final = 8

#: 一次 ``UPSERT`` 最多写多少行。太大则一条语句过长，太小则语句条数多。
WRITE_CHUNK: Final = 500

#: facet 表里那 22 个字段，**一份就是全部**：建表语句、列清单、写行与读行都由它推出来。
#:
#: 每一项是 ``(列名, SQL 类型, PhotoFacets 上的属性名, 取值方式, DDL 附加约束)``，
#: 「取值方式」决定两端怎么转，见 :func:`_encode_field` 与 :func:`_decode_field`。
#: **列顺序就是这张表的顺序。**
#:
#: 这个布局原来手写了五遍（建表语句、列清单、:meth:`FacetIndex.store`、读行、写行），
#: 而加一个字段漏改哪一处都不会报错 —— SQL 照样执行成功，只是那一列永远是空的，
#: 分组退化成「全是时间未知」。所以 :func:`_row_to_facets` 那道 ``zip(strict=True)``
#: 是刻意留着的：列数与字段数一旦对不上，它当场抛，而不会安静地少读一列。
_FACET_FIELDS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    ("captured", "REAL", "captured_at", "float", ""),
    ("year", "INTEGER", "year", "int", ""),
    ("month", "INTEGER", "month", "int", ""),
    ("day", "INTEGER", "day", "int", ""),
    ("hour", "INTEGER", "hour", "int", ""),
    ("minute", "INTEGER", "minute", "int", ""),
    ("camera", "TEXT", "camera", "str", ""),
    ("lens", "TEXT", "lens", "str", ""),
    ("city", "TEXT", "city", "str", ""),
    ("region", "TEXT", "region", "str", ""),
    ("country", "TEXT", "country", "str", ""),
    ("keywords", "TEXT", "keywords", "keywords", ""),
    ("latitude", "REAL", "latitude", "float", ""),
    ("longitude", "REAL", "longitude", "float", ""),
    ("inferred", "INTEGER", "place_inferred", "bool", " NOT NULL DEFAULT 0"),
    ("place_ok", "INTEGER", "place_checked", "bool", " NOT NULL DEFAULT 0"),
    ("place_km", "REAL", "place_distance_km", "float", ""),
    ("aperture", "REAL", "aperture", "float", ""),
    ("shutter", "REAL", "shutter", "float", ""),
    ("iso", "REAL", "iso", "float", ""),
    ("focal", "REAL", "focal", "float", ""),
    ("pixels", "REAL", "pixels", "float", ""),
)

#: 前三列来自 :class:`~gallery.core.library.photos.PhotoRef` 而不是 facet，所以不在上面那张表里。
_ROW_HEAD: Final[tuple[tuple[str, str, str], ...]] = (
    ("path", "TEXT", "TEXT PRIMARY KEY"),
    ("size", "INTEGER", "INTEGER NOT NULL"),
    ("mtime_ns", "INTEGER", "INTEGER NOT NULL"),
)

_FACET_DDL: Final = ",\n".join(
    [f"    {name:<10} {declaration}" for name, _, declaration in _ROW_HEAD]
    + [f"    {name:<10} {sql_type}{extra}" for name, sql_type, _, _, extra in _FACET_FIELDS]
)

_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS facets (
{_FACET_DDL}
);

CREATE TABLE IF NOT EXISTS photos (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    name       TEXT NOT NULL COLLATE NOCASE,
    folder     TEXT NOT NULL COLLATE NOCASE,
    suffix     TEXT NOT NULL COLLATE NOCASE
);

-- 索引只给**等值与区间**那几类条件：相册与格式是等值（走 photos_folder /
-- photos_suffix），拍摄时间是区间（走 facets_captured）。文件名与文本字段是子串匹配，
-- SQLite 的 LIKE 对 '%q%' 一律全表扫 —— 铺再多索引也用不上，那是拿写放大换不存在的好处。
CREATE INDEX IF NOT EXISTS photos_folder    ON photos(folder);
CREATE INDEX IF NOT EXISTS photos_suffix    ON photos(suffix);
CREATE INDEX IF NOT EXISTS facets_captured  ON facets(captured);
"""

_PHOTO_COLUMNS: Final[str] = "path, size, mtime_ns, name, folder, suffix"

_PHOTO_PLACEHOLDERS: Final[str] = ", ".join("?" * len(_PHOTO_COLUMNS.split(",")))

_COLUMNS: Final[str] = ", ".join(
    [name for name, _, _ in _ROW_HEAD] + [name for name, _, _, _, _ in _FACET_FIELDS]
)

#: 关键词在库里用一个分隔符存 —— 关键词本身可能含逗号，不能用逗号拼回去。
_KEYWORD_SEPARATOR = "\x1f"

#: 索引没落盘时给用户的那一句（检索与筛选共用）。
_UNAVAILABLE_HINT: Final = (
    "当前库无法建立检索索引：通常是因为照片所在磁盘不允许写入缓存。分组与排序仍然可用。"
)

#: 按日期挑照片那句 —— 比上面多一个「挑不到」的后果，因为它只服务那一件事。
_BY_DATE_UNAVAILABLE: Final = (
    "当前库无法建立索引：通常是因为照片所在磁盘不允许写入缓存，因此无法按日期范围挑选照片。"
)

#: 索引自己那两张表。**凡是要动其中一张的地方都必须两张一起动**：只清 facet 的话检索会
#: 拿一份刚被丢掉的文件表继续查（「缩略图清了但搜索还在」），只清 photos 则给检索留一条
#: 打不开的假命中。这条不变量原先靠 ``prune`` / ``forget`` / ``clear`` 三处各写一遍
#: ``("facets", "photos")``，命名一次才不会漏。
_FACET_TABLES: Final = ("facets", "photos")

#: 每一行有几个值。**从列名里数出来，不要硬编码数字** —— 硬编码的那个曾经与列数差一个。
_COLUMN_COUNT = len(_COLUMNS.split(","))

#: 单行插入用的占位符。
_PLACEHOLDERS: Final[str] = ", ".join("?" * _COLUMN_COUNT)

#: SQLite 认识的绑定类型（这里存不到二进制，所以不含 bytes）。
type SqlValue = str | int | float | None


class FacetIndex:
    """按 ``(路径, 大小, 修改时间)`` 缓存 facet 值。

    ``path`` 为 ``None`` 时**不落盘**，只在内存里缓存本次会话读到的值（只读卷、建不出配置目录时）。
    **这是唯一一个 ``None`` 的来路**：ADR 0038 已删掉 ``cache_in_library`` 开关，别把它加回来。
    """

    def __init__(self, path: Path | None = None, *, attach: AttachHook | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._memory: dict[str, tuple[tuple[int, int], PhotoFacets]] = {}
        #: 挂钩，**重开时要再跑一遍** —— 挂在连接上的东西（事件库）跟着一起换新的。
        self._attach = attach
        #: 挂在这条连接上的东西（事件库，ADR 0043）。**索引不认识它** —— 只把返回值原样收着，
        #: 由 ``Library`` 接线、取走。没挂上或挂失败都是 ``None``。
        self._attached: object | None = None
        #: 打开那一刻这份文件的身份（见 :func:`~gallery.core.sql.file_identity`）。变了就是被换了。
        self._identity: tuple[int, int] | None = None
        self._open(attach)

    # ---- 生命周期 -----------------------------------------------------------

    @classmethod
    def open(cls, root: Path) -> FacetIndex:
        """按约定打开：索引永远在库内的配置目录下（见 ``paths.index_file``）。索引不跟缩略图一起
        放系统目录（ADR 0038）：它与库级的界面偏好同在 ``config.toml`` 旁边。
        """
        return cls(paths.index_file(root))

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def persistent(self) -> bool:
        """是否真的落盘了。``False`` 表示退化成只读内存。"""
        return self._connection is not None and self._path is not None

    def close(self) -> None:
        """关掉连接，**连带把 ``_path`` 一起清掉**。

        ``_path`` 留着就表示「这个索引是落盘的」，而 :meth:`_open` 与 :meth:`persistent`
        都拿它当判据 —— 只清 ``_connection`` 的话，下一次 :meth:`store` 会在一个已经关掉的
        对象上重新 ``sqlite3.connect`` 同一个文件，即「关掉的东西被偷偷打开」。
        """
        with self._lock:
            self._close_locked()
            self._path = None
            self._memory.clear()

    def _open(self, attach: AttachHook | None = None) -> None:
        # 两条失败路径都会提前返回，所以先把上一轮那两样清掉，别让它们看起来还活着。
        self._attached = None
        self._identity = None
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path, check_same_thread=False)
        except (OSError, sqlite3.Error) as exc:
            # 缓存写不进去不该让分组功能整个不可用：退化成内存缓存，本次会话照常能用，下次重读即可。
            logger.info("facet 索引不可用（%s），改用内存缓存", exc)
            self._path = None
            return
        _relax_cache_durability(connection)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                # 结构对不上就整表重建：缓存没有迁移的价值。
                connection.executescript(
                    "DROP TABLE IF EXISTS facets; DROP TABLE IF EXISTS photos;"
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.executescript(_SCHEMA)
            connection.commit()
        except sqlite3.Error as exc:  # pragma: no cover - 磁盘坏了才会走到
            logger.info("facet 索引初始化失败（%s），改用内存缓存", exc)
            connection.close()
            self._path = None
            return
        self._connection = connection
        self._identity = file_identity(self._path)
        if attach is not None:
            # 挂钩在**建表之后**才跑：两边的表各自建各的，谁也不需要先看见谁。
            # 挂钩自己吞掉失败（返回 ``None``），索引照常可用（ADR 0043 决策 1）。
            self._attached = attach(connection, self._lock)

    @property
    def attached(self) -> object | None:
        """挂在连接上的东西（事件库）。类型由挂钩决定，所以这里是 ``object``。"""
        return self._attached

    @property
    def connection(self) -> sqlite3.Connection | None:
        """这条连接（事件库挂在它上面）。**用的地方先过 :meth:`ensure_current`**。"""
        return self._connection

    def ensure_current(self) -> bool:
        """磁盘上那份库文件被别的程序（云盘）换掉时重开一次连接，返回是否真的重开了。

        **读写都要先走它**：文件被换掉之后，写会报 ``attempt to write a readonly database``，
        而读还在给旧内容 —— 症状正是「封面下来了事件却没建起来」与「删掉的东西还在」。
        判据是文件身份（``st_dev`` + ``st_ino``）变了，**不是 mtime**：内容变了该照常接着用。
        整份不见了时也重开 —— 索引是可再生的缓存，重建一份正是它该有的活法。
        """
        with self._lock:
            path = self._path
            if path is None or self._connection is None:
                return False
            if file_identity(path) == self._identity:
                return False
            self._reopen()
            return True

    def reopen(self) -> sqlite3.Connection | None:
        """**无条件**关掉再开一次，返回新连接；这份索引本来就没落盘（或文件不在了）返回 ``None``。

        与 :meth:`ensure_current` 分开：那个按文件身份自己判断，这个的判据来自**一次真实的写失败**
        （错误码说连接底下的文件不对了），所以不看身份，直接重来。
        """
        with self._lock:
            if self._path is None or not self._path.exists():
                return None
            self._reopen()
            return self._connection

    def _reopen(self) -> None:
        """关掉再开一次。**调用方要已经拿着锁**（见 :meth:`ensure_current` / :meth:`reopen`）。"""
        self._close_locked()
        self._open(self._attach)

    def _close_locked(self) -> None:
        """关掉连接并清掉挂在它上面的东西（``_attached`` / ``_identity``）。

        **调用方要已经拿着锁**。``attached`` 留着最坏：它还会说有事件库，而它的连接已经关了 ——
        症状是每次写都抛 ``Cannot operate on a closed database``。
        """
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._attached = None
        self._identity = None

    # ---- 读写 ---------------------------------------------------------------

    def lookup(self, refs: Sequence[PhotoRef]) -> dict[Path, PhotoFacets]:
        """取出这些照片里**缓存仍然有效**的那些。「有效」= 索引里记的 ``(大小, 修改时间)`` 与扫描时
        一致；文件被写回元数据、被访达改过都会让这一行作废，那时重读一次并顺手写回。
        """
        if not refs:
            return {}
        self.ensure_current()
        wanted = {str(ref.path): (ref.size, ref.mtime_ns) for ref in refs}
        result: dict[Path, PhotoFacets] = {}
        for key, size, mtime_ns, values in self._select(list(wanted)):
            if wanted.get(key) != (size, mtime_ns):
                continue  # 文件改过了，这行已经过期
            facets = _row_to_facets(values)
            if facets is not None:
                result[Path(key)] = facets
        return result

    def store(self, items: Sequence[tuple[PhotoRef, PhotoFacets]]) -> int:
        """写回一批结果，返回真正写入的行数。"""
        if not items:
            return 0
        self.ensure_current()
        rows: list[tuple[SqlValue, ...]] = [
            (str(ref.path), ref.size, ref.mtime_ns, *_facets_to_row(facets))
            for ref, facets in items
        ]
        with self._lock:
            if self._connection is None:
                for ref, facets in items:
                    self._memory[str(ref.path)] = ((ref.size, ref.mtime_ns), facets)
                return len(rows)
            # 一行一条语句、一次事务提交：几万行也只是几十毫秒，换来的是不必手工摊平绑定值。
            statement = f"INSERT OR REPLACE INTO facets ({_COLUMNS}) VALUES ({_PLACEHOLDERS})"
            written = 0
            for start in range(0, len(rows), WRITE_CHUNK):
                chunk = rows[start : start + WRITE_CHUNK]
                self._connection.executemany(statement, chunk)
                written += len(chunk)
            self._connection.commit()
        return written

    def store_photos(self, refs: Sequence[PhotoRef], *, root: Path) -> int:
        """写回一批照片的「文件自带的」那些字段，返回真正写入的行数。

        **只写库里没有这一行、或这一行已经过期的**（判据同 :meth:`lookup`），所以打开一个没变过的
        库几乎不写。没落盘时什么都不写：那一份缓存是本次会话的 facet 值，文件表留着它也检索不了。
        """
        if not refs:
            return 0
        root = Path(root)
        rows = [
            (
                str(ref.path),
                ref.size,
                ref.mtime_ns,
                ref.path.name,
                relative_folder(ref.path, root),
                ref.path.suffix.lower(),
            )
            for ref in refs
        ]
        # 先问「哪些行已经过期」——这一句自己拿锁，所以**必须在进锁之前问**：
        # 那把锁不是可重入的，进去之后再问就是自己等自己。
        known = {
            key: (size, mtime_ns)
            for key, size, mtime_ns in self._photo_stamps([row[0] for row in rows])
        }
        fresh = [row for row in rows if known.get(row[0]) != (row[1], row[2])]
        if not fresh:
            return 0
        with self._lock:
            if self._connection is None:
                return 0
            statement = (
                f"INSERT OR REPLACE INTO photos ({_PHOTO_COLUMNS}) VALUES ({_PHOTO_PLACEHOLDERS})"
            )
            for start in range(0, len(fresh), WRITE_CHUNK):
                self._connection.executemany(statement, fresh[start : start + WRITE_CHUNK])
            self._connection.commit()
        return len(fresh)

    def count_photos(self) -> int:
        """文件表里有多少行。**用来回答「打开库时那一遍还有没有活干」**（ADR 0026 决策 5）。"""
        self.ensure_current()
        with self._lock:
            if self._connection is None:
                return 0
            try:
                return int(self._connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            except sqlite3.Error:  # pragma: no cover - 磁盘坏了才会走到
                return 0

    def _require_connection(self, message: str = _UNAVAILABLE_HINT) -> sqlite3.Connection:
        """拿连接；索引没落盘就按 ``message`` 抛。

        **检索与筛选用同一句提示** —— 它们是同一件事的两面，各写一份迟早会说成两套话。
        :meth:`photos_in_days` 传自己那一句，因为它多一个「按日期挑不到照片」的后果要说清。
        """
        self.ensure_current()
        if self._connection is None:
            raise IndexUnavailableError(message)
        return self._connection

    def search(
        self,
        conditions: Sequence[SearchCondition],
        *,
        limit: int = RESULT_LIMIT,
        root: Path | None = None,
    ) -> SearchOutcome:
        """按这些条件检索（SQL 在 :mod:`gallery.core.library.search` 里生成）。

        :param root: 库根。**事件条件靠它把相对键接回绝对路径**（ADR 0086）；没有事件条件时可省。
        :raises IndexUnavailableError: 索引没落盘（库所在卷只读）。**分组与排序在同样情况下照常
            可用** —— 那是把读到的值揣在内存里算的，不需要索引。
        """
        connection = self._require_connection()
        sql, values = build_query(conditions, limit=limit, root=root)
        count_sql, count_values = build_count_query(conditions, root=root)
        with self._lock:
            try:
                rows = connection.execute(sql, values).fetchall()
                total = int(connection.execute(count_sql, count_values).fetchone()[0])
            except sqlite3.Error as exc:
                logger.info("检索失败：%s", exc)
                raise IndexUnavailableError("检索未能完成，请重试。", detail=str(exc)) from exc
        return SearchOutcome(refs=rows_to_refs(rows), total=total)

    def match_paths(
        self, conditions: Sequence[SearchCondition], *, root: Path | None = None
    ) -> set[str]:
        """这些条件命中了哪些路径 —— 当前层筛选要的那一份（ADR 0040 决策 2）。

        **不截断**（``build_match_query`` 里就没有 ``LIMIT``）：调用方拿它与手上那份列表求交，
        少给一条就等于凭空少一张照片，而那与「文件被删了」长得一模一样。
        :raises IndexUnavailableError: 索引没落盘。与 :meth:`search` 同一句提示。
        """
        connection = self._require_connection()
        sql, values = build_match_query(conditions, root=root)
        with self._lock:
            try:
                rows = connection.execute(sql, values).fetchall()
            except sqlite3.Error as exc:
                logger.info("筛选失败：%s", exc)
                raise IndexUnavailableError("筛选未能完成，请重试。", detail=str(exc)) from exc
        return {row[0] for row in rows if isinstance(row[0], str)}

    def photos_in_days(self, first_day: str, last_day: str) -> list[PhotoRef]:
        """这几天里拍的那些照片（按**照片里记的那个日子**，ADR 0043）。

        用 year / month / day 三列拼 ``YYYY-MM-DD`` 比字符串，比 ``captured`` 的 epoch 区间更稳
        （时区不换算，ADR 0032 / 0033）：走不了索引，但几万行是毫秒级，没时间的拼出来是全零。
        :raises IndexUnavailableError: 索引没落盘（只读卷）。
        """
        connection = self._require_connection(_BY_DATE_UNAVAILABLE)
        sql = (
            "SELECT p.path, p.size, p.mtime_ns FROM photos p"
            " JOIN facets f ON f.path = p.path"
            " WHERE printf('%04d-%02d-%02d', f.year, f.month, f.day) BETWEEN ? AND ?"
            " ORDER BY p.path"
        )
        with self._lock:
            try:
                rows = connection.execute(sql, (first_day, last_day)).fetchall()
            except sqlite3.Error as exc:
                logger.info("按日期范围挑选照片失败：%s", exc)
                raise IndexUnavailableError(
                    "按日期范围挑选照片未能完成，请重试。", detail=str(exc)
                ) from exc
        return rows_to_refs(rows)

    def prune(self, live: Iterable[Path]) -> int:
        """删掉不在这一批里的行，返回删掉了几行。
        照片被移走 / 改名之后那一行不会自己消失；索引是缓存，删不干净也不影响浏览，所以不抛异常。
        **两张表一起清**：少清一张，检索会拿那些指向已删文件的行当命中（``prune`` 只在重建时用）。
        目前唯一的调用者是它自己的测试 —— 接口留着，「清掉已不存在照片那一行」是索引该有的能力。
        """
        keep = {str(path) for path in live}
        self.ensure_current()
        with self._lock:
            if self._connection is None:
                stale = [key for key in self._memory if key not in keep]
                for key in stale:
                    del self._memory[key]
                return len(stale)
            try:
                removed = 0
                for table in _FACET_TABLES:
                    known = [
                        row[0] for row in self._connection.execute(f"SELECT path FROM {table}")
                    ]
                    stale = [key for key in known if key not in keep]
                    self._connection.executemany(
                        f"DELETE FROM {table} WHERE path = ?", [(key,) for key in stale]
                    )
                    removed += len(stale)
                self._connection.commit()
                return removed
            except sqlite3.Error as exc:  # pragma: no cover - 磁盘坏了才会走到
                logger.info("清理索引失败：%s", exc)
                return 0

    def forget(self, gone: Iterable[Path]) -> int:
        """删掉指定路径的行，返回删掉了几行。与 :meth:`prune` 的区别是**只删给定的路径**：相册模式下
        「当前这一层」不等于「整个库」，按层去重会把别的相册的行一起删掉（ADR 0013）。

        **两张表一起删**：留着 ``photos`` 那一行就是给检索留一条假命中，搜出来的却是一张打不开
        的缩略图。它是缓存，删不干净也不影响浏览，因此不抛异常。
        """
        keys = {str(path) for path in gone}
        if not keys:
            return 0
        self.ensure_current()
        with self._lock:
            if self._connection is None:
                stale = [key for key in self._memory if key in keys]
                for key in stale:
                    del self._memory[key]
                return len(stale)
            removed = 0
            try:
                unique = list(keys)
                for start in range(0, len(unique), 900):
                    chunk = unique[start : start + 900]
                    marks = ", ".join("?" * len(chunk))
                    for table in _FACET_TABLES:
                        cursor = self._connection.execute(
                            f"DELETE FROM {table} WHERE path IN ({marks})", chunk
                        )
                        removed += max(0, cursor.rowcount)
                self._connection.commit()
            except sqlite3.Error as exc:  # pragma: no cover - 磁盘坏了才会走到
                logger.info("清理索引失败：%s", exc)
            return removed

    def clear(self) -> None:
        """清空缓存（清缩略图缓存时一并清掉）。**两张表都清**：只清 facet 的话，检索会立刻
        拿一份刚被丢掉的文件表继续查，用户看到的是「缩略图清了但搜索还在」。
        """
        self.ensure_current()
        with self._lock:
            self._memory.clear()
            if self._connection is None:
                return
            try:
                for table in _FACET_TABLES:
                    self._connection.execute(f"DELETE FROM {table}")
                self._connection.commit()
            except sqlite3.Error as exc:  # pragma: no cover
                logger.info("清空索引失败：%s", exc)

    # ---- 内部 ---------------------------------------------------------------

    def _photo_stamps(self, keys: Sequence[str]) -> list[tuple[str, int, int]]:
        """按路径取 ``(路径, 大小, 修改时间)``，用来判「这一行是不是已经过期了」。"""
        with self._lock:
            if self._connection is None:
                return []
            found: list[tuple[str, int, int]] = []
            unique = list(dict.fromkeys(keys))
            for start in range(0, len(unique), 900):
                chunk = unique[start : start + 900]
                marks = ", ".join("?" * len(chunk))
                rows = self._connection.execute(
                    f"SELECT path, size, mtime_ns FROM photos WHERE path IN ({marks})", chunk
                ).fetchall()
                found.extend((str(row[0]), int(row[1]), int(row[2])) for row in rows)
            return found

    def _select(self, keys: Sequence[str]) -> list[tuple[str, int, int, tuple[object, ...]]]:
        """按路径取行：``(路径, 大小, 修改时间, 其余字段)``。"""
        wanted = set(keys)
        with self._lock:
            if self._connection is None:
                return [
                    (key, stamp[0], stamp[1], _facets_to_row(facets))
                    for key, (stamp, facets) in self._memory.items()
                    if key in wanted
                ]
            found: list[tuple[str, int, int, tuple[object, ...]]] = []
            unique = list(dict.fromkeys(keys))
            for start in range(0, len(unique), 900):
                # 一批一批查：SQLite 的变量数有上限（999），一万个 IN 会直接报错。
                chunk = unique[start : start + 900]
                marks = ", ".join("?" * len(chunk))
                rows = self._connection.execute(
                    f"SELECT {_COLUMNS} FROM facets WHERE path IN ({marks})", chunk
                ).fetchall()
                found.extend(
                    (str(row[0]), int(row[1]), int(row[2]), tuple(row[3:])) for row in rows
                )
            return found


def _relax_cache_durability(connection: sqlite3.Connection) -> None:
    """把提交强度降到「缓存该有的强度」：``journal_mode=DELETE`` + ``synchronous=NORMAL``。

    **索引是缓存，不是数据**（删掉可重扫恢复），不值得付满额断电持久性。**日志模式用 ``DELETE``
    而不是 ``WAL``**（ADR 0038 决策 5）：``-wal`` / ``-shm`` 会被云同步来回传，两台机器互相覆盖
    有损坏索引的风险。**失败只记日志绝不致命**；**别与事件库那个合并**（事件是用户填的数据）。
    """
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as exc:
        logger.info("facet 索引沿用默认日志模式（%s）", exc)


def _row_to_facets(values: Sequence[object]) -> PhotoFacets | None:
    """一行的后 22 列 → :class:`PhotoFacets`。字段与类型由 :data:`_FACET_FIELDS` 决定。

    ``zip(strict=True)`` 是重点：列数与字段数对不上时它当场抛，而不会安静地少读一列（那会退化成
    「分组全是时间未知、SQL 照样成功」）。属性名与 :class:`PhotoFacets` 对不对得上由一条测试盯着。
    """
    decoded = {
        attribute: _decode_field(kind, value)
        for (_, _, attribute, kind, _), value in zip(_FACET_FIELDS, values, strict=True)
    }
    return PhotoFacets(**cast("dict[str, Any]", decoded))


def _encode_field(kind: str, value: object) -> object:
    """:class:`PhotoFacets` 上的值 → 存进库的那一份。"""
    if kind == "keywords":
        return _KEYWORD_SEPARATOR.join(cast("tuple[str, ...]", value))
    if kind == "bool":
        return 1 if value else 0
    return value


def _decode_field(kind: str, value: object) -> object:
    """库里读回来的那一份 → :class:`PhotoFacets` 上该有的类型。

    数值与文本都要过一遍转换而不是直接用：SQLite 是弱类型的，而后面那些
    「按 0 / 空串判未知」的分组判据吃的就是类型对不对。
    """
    if kind == "float":
        return _optional_float(value)
    if kind == "int":
        return _optional_int(value)
    if kind == "str":
        return _optional_str(value)
    if kind == "bool":
        return bool(value)
    return tuple(word for word in str(value or "").split(_KEYWORD_SEPARATOR) if word)


def _facets_to_row(facets: PhotoFacets) -> tuple[SqlValue, ...]:
    """:class:`PhotoFacets` → 一行的后 22 列。内存那条读路径与落盘那条共用它。"""
    return cast(
        "tuple[SqlValue, ...]",
        tuple(
            _encode_field(kind, getattr(facets, attribute))
            for _, _, attribute, kind, _ in _FACET_FIELDS
        ),
    )


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_str(value: object) -> str | None:
    text = str(value) if value else ""
    return text or None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


__all__ = ["SCHEMA_VERSION", "FacetIndex"]
