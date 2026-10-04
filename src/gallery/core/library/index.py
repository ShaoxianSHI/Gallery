"""分组 / 排序 / 检索用的本地缓存（SQLite）。

**它是缓存，不是数据**：每张照片两行 —— ``facets`` 存从 EXIF 抽出来的可比较值，
``photos`` 存扫描时就有的东西（路径、大小、修改时间、文件名、所在相册、扩展名）。
删掉两张表之后重新扫一遍、读一遍 EXIF 就能恢复到等价状态（红线 5）。唯一的数据仍在照片里。

命中判据是 ``(size, mtime_ns)`` 与扫描时一致 —— 文件被改过（元数据写回、外部工具）
就当没有，重读一次顺手更新。表结构变了就整表重建：``PRAGMA user_version`` 对不上
说明是老版本留下的，缓存本来就是可以丢的，没有迁移的价值。

**两张表分工不重叠**：``facets`` 只有读过 EXIF 的照片才有行，因此搜索文件名与相册
必须走 ``photos``（它一行都不缺）；反过来，按元数据搜的时候有没有 facet 行就是
「这条结论我们到底知不知道」（ADR 0026）。

并发：界面在若干个后台线程里读、后台任务在线程里写，因此连接本身带
``check_same_thread=False``，所有访问都由一把锁串起来。操作都很短（几万行的一次
``SELECT ... IN`` 与批量 ``UPSERT``），串行不会成为瓶颈。

**提交强度按「缓存」而不是按「数据」配**（见 :func:`_relax_durability`）：
``journal_mode=WAL`` + ``synchronous=NORMAL``，因为这份东西随时可以删掉重扫，
为它每次 commit 都 fsync 是白等。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.errors import IndexUnavailableError
from gallery.core.library.photos import PhotoRef
from gallery.core.library.search import (
    RESULT_LIMIT,
    SearchCondition,
    SearchOutcome,
    build_count_query,
    build_query,
    rows_to_refs,
)
from gallery.core.metadata.facets import PhotoFacets, relative_folder

logger = logging.getLogger(__name__)

#: 表结构版本。改动表结构就 +1；旧表会被整表丢掉重建（缓存不值得迁移）。
#:
#: 4 / 5 / 6：地点结论改了几轮（ADR 0011 的语言规则、港澳台、ADR 0013 的自带
#: 城市库）。**表结构始终没变**，但存进去的城市名、省名变了 —— 不升版本的话，
#: 老库里那些「波尔多 / 法国」「宝安 / 广东省」会永远留在索引里，新扫的照片却
#: 已经是新说法，同一个库里新老混排且再也刷不掉（``place_ok`` 记着「已经查过了」）。
#: 7：新增 ``hour`` / ``minute`` 两栏 —— 日历视图的小时格子按**墙上钟点**摆
#: （ADR 0033 决策 3），而 ``captured`` 那个 epoch 反推不出墙上钟点（照片写了时区
#: 偏移时反推出来的是「在本机看到的时刻」）。因此这一版与上面几次不同：
#: **老库会被整表丢掉重建**，也就是全库重读一次 EXIF（一次性；缓存在配置目录里，
#: 重建得出等价状态，见 AGENTS 红线 5）。
SCHEMA_VERSION: Final = 7

#: **后加的 ``photos`` 表没有升版本**（ADR 0026 决策 3）：它是纯新增，语句全部带
#: ``IF NOT EXISTS``，老库连上来自动多出这张表，一行已读好的 facet 都不用重读。
#: 为一张新表把整库的 EXIF 重读一遍纯属浪费 —— 缓存本来就该整表重建，但不必每次都重建。
#: 将来若要**改** ``photos`` 的列，那时才升版本，并在这条注释里写清代价。
#:
#: 一次 ``UPSERT`` 最多写多少行。太大则一条语句过长，太小则语句条数多。
WRITE_CHUNK: Final = 500

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS facets (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    captured   REAL,
    year       INTEGER,
    month      INTEGER,
    day        INTEGER,
    hour       INTEGER,
    minute     INTEGER,
    camera     TEXT,
    lens       TEXT,
    city       TEXT,
    region     TEXT,
    country    TEXT,
    keywords   TEXT,
    latitude   REAL,
    longitude  REAL,
    inferred   INTEGER NOT NULL DEFAULT 0,
    place_ok   INTEGER NOT NULL DEFAULT 0,
    place_km   REAL,
    aperture   REAL,
    shutter    REAL,
    iso        REAL,
    focal      REAL,
    pixels     REAL
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

_COLUMNS: Final[str] = (
    "path, size, mtime_ns, captured, year, month, day, hour, minute, camera, lens, "
    "city, region, country, keywords, latitude, longitude, inferred, "
    "place_ok, place_km, aperture, shutter, iso, focal, pixels"
)

#: 关键词在库里用一个分隔符存 —— 关键词本身可能含逗号，不能用逗号拼回去。
_KEYWORD_SEPARATOR = "\x1f"

#: 每一行有几个值。写死的数字曾经和列数对不上（17 vs 18），于是每次写索引都报错，
#: 分组退化成「全是时间未知」；宁可从列名里数出来。
_COLUMN_COUNT = len(_COLUMNS.split(","))

#: 单行插入用的占位符。
_PLACEHOLDERS: Final[str] = ", ".join("?" * _COLUMN_COUNT)

#: SQLite 认识的绑定类型（这里存不到二进制，所以不含 bytes）。
type SqlValue = str | int | float | None


class FacetIndex:
    """按 ``(路径, 大小, 修改时间)`` 缓存 facet 值。

    ``path`` 为 ``None`` 时**不落盘**：只在内存里缓存本次会话读到的值
    （库所在卷只读、或用户不想在库里留任何文件时用）。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._memory: dict[str, tuple[tuple[int, int], PhotoFacets]] = {}
        self._open()

    # ---- 生命周期 -----------------------------------------------------------

    @classmethod
    def open(cls, root: Path, *, in_library: bool, base: Path | None = None) -> FacetIndex:
        """按缓存位置约定打开：库内配置目录（见 ``paths.index_file``），或系统缓存目录。"""
        path = paths.index_file(root) if in_library else paths.external_index_file(root, base=base)
        return cls(path)

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def persistent(self) -> bool:
        """是否真的落盘了。``False`` 表示退化成只读内存。"""
        return self._connection is not None and self._path is not None

    def close(self) -> None:
        """关掉连接。**连带把 ``_path`` 一起清掉**。

        过去只清了 ``_connection``，于是 ``_open`` 里那两句 ``_path is None`` 的
        分支有一半永远走不到：文件路径还在，下次 ``store`` 会在关掉的状态上重新
        ``sqlite3.connect`` 同一个文件 —— 在多数平台上这会成功（SQLite 自己管着
        自己的生命周期），但它是「已经关掉的东西被偷偷重新打开」，只有代码路径对了
        才谈得上别的。

        顺带把内存那一份也放掉，免得关了还占着。
        """
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._path = None
            self._memory.clear()

    def _open(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path, check_same_thread=False)
        except (OSError, sqlite3.Error) as exc:
            # 缓存写不进去不该让分组功能整个不可用：退化成内存缓存，
            # 本次会话照常能用，只是下次打开要重读一遍。
            logger.info("facet 索引不可用（%s），改用内存缓存", exc)
            self._path = None
            return
        _relax_durability(connection)
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

    # ---- 读写 ---------------------------------------------------------------

    def lookup(self, refs: Sequence[PhotoRef]) -> dict[Path, PhotoFacets]:
        """取出这些照片里**缓存仍然有效**的那些。

        「有效」= 索引里记的 ``(大小, 修改时间)`` 与扫描时一致。文件被写回元数据、
        被访达改过，都会让这一行作废 —— 那时重新读一次，顺手把新的值写回去。
        """
        if not refs:
            return {}
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
        rows: list[tuple[SqlValue, ...]] = [
            (
                str(ref.path),
                ref.size,
                ref.mtime_ns,
                facets.captured_at,
                facets.year,
                facets.month,
                facets.day,
                facets.hour,
                facets.minute,
                facets.camera,
                facets.lens,
                facets.city,
                facets.region,
                facets.country,
                _KEYWORD_SEPARATOR.join(facets.keywords),
                facets.latitude,
                facets.longitude,
                1 if facets.place_inferred else 0,
                1 if facets.place_checked else 0,
                facets.place_distance_km,
                facets.aperture,
                facets.shutter,
                facets.iso,
                facets.focal,
                facets.pixels,
            )
            for ref, facets in items
        ]
        with self._lock:
            if self._connection is None:
                for ref, facets in items:
                    self._memory[str(ref.path)] = ((ref.size, ref.mtime_ns), facets)
                return len(rows)
            # 一行一条语句、一次事务提交：几万行也只是几十毫秒，换来的是不必
            # 手工摊平绑定值（多行 VALUES 要摊平，传错还会静默写歪）。
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

        **只写「库里没有这一行」或「这一行已经过期」的。** 判据与 :meth:`lookup`
        同源（``(大小, 修改时间)`` 对不上就是过期），因此打开一个库时那一遍遍历
        对没变过的库几乎不写 —— 整表重写一遍只是白花几百毫秒。

        没落盘（内存模式）时什么都不写：那一份缓存是本次会话读到的 facet 值，
        文件表留着它也检索不了（见 :meth:`search`）。
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
        with self._lock:
            if self._connection is None:
                return 0
            try:
                return int(self._connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            except sqlite3.Error:  # pragma: no cover - 磁盘坏了才会走到
                return 0

    def search(
        self, conditions: Sequence[SearchCondition], *, limit: int = RESULT_LIMIT
    ) -> SearchOutcome:
        """按这些条件检索（SQL 在 :mod:`gallery.core.library.search` 里生成）。

        :raises IndexUnavailableError: 索引没落盘（库所在卷只读）。**分组与排序在
            同样情况下照常可用** —— 那是把读到的值揣在内存里算的，不需要索引。
        """
        if self._connection is None:
            raise IndexUnavailableError(
                "这个库没法建立检索索引（多半是照片所在的磁盘不允许写入缓存）。分组与排序仍然可用。"
            )
        sql, values = build_query(conditions, limit=limit)
        count_sql, count_values = build_count_query(conditions)
        with self._lock:
            try:
                rows = self._connection.execute(sql, values).fetchall()
                total = int(self._connection.execute(count_sql, count_values).fetchone()[0])
            except sqlite3.Error as exc:
                logger.info("检索失败：%s", exc)
                raise IndexUnavailableError("检索没能完成，请再试一次。", detail=str(exc)) from exc
        return SearchOutcome(refs=rows_to_refs(rows), total=total)

    def prune(self, live: Iterable[Path]) -> int:
        """删掉不在这一批里的行，返回删掉了几行。

        照片被移走 / 改名之后，它那一行不会自己消失。索引是缓存，所以这纯属省空间，
        失败也不影响功能 —— 因此不抛异常。

        **两张表一起清**：少清一张，过一阵子库里就攒下一堆指向已删文件的行，
        而检索恰恰会把它们当成命中（``prune`` 只在重建时用，正是攒的时候）。

        **目前唯一的调用者是它自己的测试。** 留着是因为「清掉已经不存在的照片那一行」
        是索引该有的完整能力，而删掉接口再想加回来时，多半又要重新设计一遍签名。
        等到有人真拿它去清一轮（``forget_paths`` 那条路，或下次做完整重建）时，
        那个调用者会自然出现。
        """
        keep = {str(path) for path in live}
        with self._lock:
            if self._connection is None:
                stale = [key for key in self._memory if key not in keep]
                for key in stale:
                    del self._memory[key]
                return len(stale)
            try:
                removed = 0
                for table in ("facets", "photos"):
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
        """删掉指定路径的行，返回删掉了几行。

        照片被移走或改名之后，原路径那一行不会自己消失。与 :meth:`prune` 的区别是
        这里**只删给定的路径**：相册模式下「当前这一层」不等于「整个库」，按层去重
        会把别的相册的行一起删掉。实时监听得知哪个路径不见了就删哪一个（ADR 0013）。

        **两张表一起删**：留着 ``photos`` 那一行就是给检索留一条假命中 ——
        文件已经不在了，搜出来的却是一张打不开的缩略图。

        它是缓存，删不干净也不影响浏览，因此不抛异常。
        """
        keys = {str(path) for path in gone}
        if not keys:
            return 0
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
                    for table in ("facets", "photos"):
                        cursor = self._connection.execute(
                            f"DELETE FROM {table} WHERE path IN ({marks})", chunk
                        )
                        removed += max(0, cursor.rowcount)
                self._connection.commit()
            except sqlite3.Error as exc:  # pragma: no cover - 磁盘坏了才会走到
                logger.info("清理索引失败：%s", exc)
            return removed

    def clear(self) -> None:
        """清空缓存（清空缩略图缓存时一并清掉，避免两边的内容对不上）。

        **两张表都清**：只清 facet 的话，检索会立刻拿一份刚被丢掉的文件表继续查，
        而分组那边是从零重建的 —— 两边对不上，用户看到的是「缩略图清了但搜索还在」。
        """
        with self._lock:
            self._memory.clear()
            if self._connection is None:
                return
            try:
                self._connection.execute("DELETE FROM facets")
                self._connection.execute("DELETE FROM photos")
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


def _relax_durability(connection: sqlite3.Connection) -> None:
    """把提交强度降到「缓存该有的强度」：``journal_mode=WAL`` + ``synchronous=NORMAL``。

    **索引是缓存，不是数据**（红线 5、模块开头那段话）：它没了，重扫一遍库、重读一遍
    EXIF 就回来了。而默认设置下每次 ``commit`` 都要 ``fsync`` —— 一次提交一次落到盘上的
    等待。存照片时是一张张来的，等的是用户的耐心，而这份索引随时可以丢：**为一个
    随时可以重建的东西付满额断电持久性，是白付**。

    ``WAL`` 还顺带把读与写分开：后台线程读的同时不必等写事务提交。

    **失败只记一条日志、绝不致命。** 落到默认设置也只是慢一点，但更常见的是「那个卷
    是只读的 / 不支持共享内存」—— 那种情况下索引本来就能只读地用着（WAL 需要建
    ``-wal`` 与 ``-shm`` 两个文件），不能因为一次 pragma 就把它整个打成内存模式。
    后面那句 ``PRAGMA user_version`` 仍会把真正的坏库降级掉。
    """
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as exc:
        logger.info("facet 索引沿用默认日志模式（%s）", exc)


def _row_to_facets(values: Sequence[object]) -> PhotoFacets | None:
    (
        captured,
        year,
        month,
        day,
        hour,
        minute,
        camera,
        lens,
        city,
        region,
        country,
        keywords,
        latitude,
        longitude,
        inferred,
        place_ok,
        place_km,
        aperture,
        shutter,
        iso,
        focal,
        pixels,
    ) = values
    words = tuple(word for word in str(keywords or "").split(_KEYWORD_SEPARATOR) if word)
    return PhotoFacets(
        captured_at=_optional_float(captured),
        year=_optional_int(year),
        month=_optional_int(month),
        day=_optional_int(day),
        hour=_optional_int(hour),
        minute=_optional_int(minute),
        camera=_optional_str(camera),
        lens=_optional_str(lens),
        city=_optional_str(city),
        region=_optional_str(region),
        country=_optional_str(country),
        keywords=words,
        latitude=_optional_float(latitude),
        longitude=_optional_float(longitude),
        place_inferred=bool(inferred),
        place_checked=bool(place_ok),
        place_distance_km=_optional_float(place_km),
        aperture=_optional_float(aperture),
        shutter=_optional_float(shutter),
        iso=_optional_float(iso),
        focal=_optional_float(focal),
        pixels=_optional_float(pixels),
    )


def _facets_to_row(facets: PhotoFacets) -> tuple[object, ...]:
    return (
        facets.captured_at,
        facets.year,
        facets.month,
        facets.day,
        facets.hour,
        facets.minute,
        facets.camera,
        facets.lens,
        facets.city,
        facets.region,
        facets.country,
        _KEYWORD_SEPARATOR.join(facets.keywords),
        facets.latitude,
        facets.longitude,
        1 if facets.place_inferred else 0,
        1 if facets.place_checked else 0,
        facets.place_distance_km,
        facets.aperture,
        facets.shutter,
        facets.iso,
        facets.focal,
        facets.pixels,
    )


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_str(value: object) -> str | None:
    text = str(value) if value else ""
    return text or None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


__all__ = ["SCHEMA_VERSION", "FacetIndex"]
