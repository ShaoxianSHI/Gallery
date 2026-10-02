"""分组 / 排序字段的本地缓存（SQLite）。

**它是缓存，不是数据**：一行一张照片，只存 ``facets`` 抽出那几个可比较的值，
删掉整张表之后重新读一遍 EXIF 就能恢复到等价状态（红线 5）。唯一的数据仍在照片里。

命中判据是 ``(size, mtime_ns)`` 与扫描时一致 —— 文件被改过（元数据写回、外部工具）
就当没有，重读一次顺手更新。表结构变了就整表重建：``PRAGMA user_version`` 对不上
说明是老版本留下的，缓存本来就是可以丢的，没有迁移的价值。

并发：界面在若干个后台线程里读、后台任务在线程里写，因此连接本身带
``check_same_thread=False``，所有访问都由一把锁串起来。操作都很短（几万行的一次
``SELECT ... IN`` 与批量 ``UPSERT``），串行不会成为瓶颈。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets

logger = logging.getLogger(__name__)

#: 表结构版本。改动表结构就 +1；旧表会被整表丢掉重建（缓存不值得迁移）。
#:
#: 4 / 5 / 6：地点结论改了几轮（ADR 0011 的语言规则、港澳台、ADR 0012 的自带
#: 城市库）。**表结构始终没变**，但存进去的城市名、省名变了 —— 不升版本的话，
#: 老库里那些「波尔多 / 法国」「宝安 / 广东省」会永远留在索引里，新扫的照片却
#: 已经是新说法，同一个库里新老混排且再也刷不掉（``place_ok`` 记着「已经查过了」）。
SCHEMA_VERSION: Final = 6

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
)
"""

_COLUMNS: Final[str] = (
    "path, size, mtime_ns, captured, year, month, day, camera, lens, "
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
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

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
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                # 结构对不上就整表重建：缓存没有迁移的价值。
                connection.executescript("DROP TABLE IF EXISTS facets;")
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

    def prune(self, live: Iterable[Path]) -> int:
        """删掉不在这一批里的行，返回删掉了几行。

        照片被移走 / 改名之后，它那一行不会自己消失。索引是缓存，所以这纯属省空间，
        失败也不影响功能 —— 因此不抛异常。
        """
        keep = {str(path) for path in live}
        with self._lock:
            if self._connection is None:
                stale = [key for key in self._memory if key not in keep]
                for key in stale:
                    del self._memory[key]
                return len(stale)
            try:
                known = [
                    row[0] for row in self._connection.execute("SELECT path FROM facets").fetchall()
                ]
                stale = [key for key in known if key not in keep]
                self._connection.executemany(
                    "DELETE FROM facets WHERE path = ?", [(key,) for key in stale]
                )
                self._connection.commit()
                return len(stale)
            except sqlite3.Error as exc:  # pragma: no cover - 磁盘坏了才会走到
                logger.info("清理 facet 索引失败：%s", exc)
                return 0

    def clear(self) -> None:
        """清空缓存（清空缩略图缓存时一并清掉，避免两边的内容对不上）。"""
        with self._lock:
            self._memory.clear()
            if self._connection is None:
                return
            try:
                self._connection.execute("DELETE FROM facets")
                self._connection.commit()
            except sqlite3.Error as exc:  # pragma: no cover
                logger.info("清空 facet 索引失败：%s", exc)

    # ---- 内部 ---------------------------------------------------------------

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


def _row_to_facets(values: Sequence[object]) -> PhotoFacets | None:
    (
        captured,
        year,
        month,
        day,
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
