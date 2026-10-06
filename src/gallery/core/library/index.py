"""分组 / 排序 / 检索的本地缓存（SQLite）。**它是缓存不是数据**（红线 5）：重扫一遍即回到等价状态。
``facets`` 存从 EXIF 抽出的可比较值、``photos`` 存扫描时就有的东西；命中判据是
``(size, mtime_ns)`` 与扫描时一致，表结构一变就整表重建（缓存不值得迁移）。
只有读过 EXIF 的照片才有 facet 行，所以搜文件名与相册走 ``photos``（ADR 0026）。访问一把锁串起来。
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

#: 表结构版本。表结构或存进去的结论变了就 +1（老表整表重建，缓存不值得迁移）：4 / 5 / 6 是地点
#: 结论改了几轮，不升版本的话老库里那些旧地名会永远留着（``place_ok`` 记着「已经查过了」）；
#: 7 新增 ``hour`` / ``minute``：日历按墙上钟点摆格子（ADR 0033 决策 3），``captured`` 反推不出。
SCHEMA_VERSION: Final = 7

#: 后加的 ``photos`` 表**没有**升版本（ADR 0026 决策 3）：语句全带 ``IF NOT EXISTS``，老库连上来
#: 自动多出这张表，一行已读好的 facet 都不用重读；将来要**改**它的列时那才升版本。
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

#: 每一行有几个值。写死的数字曾经和列数对不上（17 vs 18），分组于是退化成「全是时间未知」；
#: 宁可从列名里数出来。
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
        """关掉连接，**连带把 ``_path`` 一起清掉**：过去只清了 ``_connection``，于是 ``_open`` 里那
        两句 ``_path is None`` 的分支有一半永远走不到，下次 ``store`` 会在关掉的状态上重新
        ``sqlite3.connect`` 同一个文件 —— 那是「已经关掉的东西被偷偷重新打开」。
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
            # 缓存写不进去不该让分组功能整个不可用：退化成内存缓存，本次会话照常能用，下次重读即可。
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
        """取出这些照片里**缓存仍然有效**的那些。「有效」= 索引里记的 ``(大小, 修改时间)`` 与扫描时
        一致；文件被写回元数据、被访达改过都会让这一行作废，那时重读一次并顺手写回。
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

        :raises IndexUnavailableError: 索引没落盘（库所在卷只读）。**分组与排序在同样情况下照常
            可用** —— 那是把读到的值揣在内存里算的，不需要索引。
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

        照片被移走 / 改名之后那一行不会自己消失；索引是缓存，删不干净也不影响浏览，所以不抛异常。
        **两张表一起清**：少清一张，检索会拿那些指向已删文件的行当命中（``prune`` 只在重建时用）。
        目前唯一的调用者是它自己的测试 —— 接口留着，「清掉已不存在照片那一行」是索引该有的能力。
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
        """删掉指定路径的行，返回删掉了几行。与 :meth:`prune` 的区别是**只删给定的路径**：相册模式下
        「当前这一层」不等于「整个库」，按层去重会把别的相册的行一起删掉（ADR 0013）。

        **两张表一起删**：留着 ``photos`` 那一行就是给检索留一条假命中，搜出来的却是一张打不开
        的缩略图。它是缓存，删不干净也不影响浏览，因此不抛异常。
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
        """清空缓存（清缩略图缓存时一并清掉）。**两张表都清**：只清 facet 的话，检索会立刻
        拿一份刚被丢掉的文件表继续查，用户看到的是「缩略图清了但搜索还在」。
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
    """把提交强度降到「缓存该有的强度」：``journal_mode=DELETE`` + ``synchronous=NORMAL``。

    **索引是缓存，不是数据**（红线 5）：默认设置下每次 ``commit`` 都要 ``fsync``，而存照片时是一张张
    来的，等的是用户的耐心 —— 为一个随时可以重建的东西付满额断电持久性是白付。

    **日志模式是 ``DELETE`` 而不是 ``WAL``（ADR 0038 决策 5）**：WAL 的卖点是读写不互斥，而这里一把
    Python 锁已经把访问串起来了；剩下的代价却实打实 —— ``-wal`` / ``-shm`` 跟着用户的库被云同步
    来回传，两台机器互相覆盖有损坏索引的风险。实测批量路径没有差别，落盘文件数从 3 个降到 1 个。

    **失败只记一条日志、绝不致命**：落到默认设置也只是多一个 ``-journal`` 文件。
    """
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
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
