"""把用户改过的坐标 / 地点**写回照片文件**（ADR 0030）—— 文件不搬家，内容被改。

红线 1（只增不改）与红线 4（先记账再动文件、写完回读校验、失败回滚并如实报错）在这里最重：
只写 :data:`WRITABLE_TAGS` 里的字段，每一步都记字段级的原值与新值，撤销按它逐字段写回。

不弹窗、不问「要不要继续」：被拒绝就抛 :class:`~gallery.core.errors.MetadataWriteError`。
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from gallery.core import paths
from gallery.core.errors import MetadataWriteError
from gallery.core.library.photos import PhotoRef, is_supported_photo
from gallery.core.metadata.exif import (
    EMPTY_VALUES as _EXIF_EMPTY_VALUES,
)
from gallery.core.metadata.exif import (
    EXIFTOOL_CHARSET_ARGS,
    EXIFTOOL_EXECUTABLE,
    EXIFTOOL_TIMEOUT_SECONDS,
    as_text,
    latitude_decimal,
    longitude_decimal,
)
from gallery.core.metadata.failures import Failure
from gallery.core.metadata.journal import (
    ACTION_METADATA,
    ACTION_UNDO_METADATA,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PENDING,
    FieldChange,
    WriteRecord,
    append_record,
)

#: 坐标的两个数值标签与两个半球标签。**四个必须一起写**：少写一个，另一半就是上一个
#: 位置留下的值 —— 那是一个自信的错答案，比没有坐标糟糕得多。
COORDINATE_TAGS: Final[tuple[str, ...]] = (
    "EXIF:GPSLatitude",
    "EXIF:GPSLatitudeRef",
    "EXIF:GPSLongitude",
    "EXIF:GPSLongitudeRef",
)

#: 改坐标时**一并清掉**的那几个字段：海拔与定位时间记的是「另一个位置」上的测量值，
#: 留着它们等于让一条 GPS 记录自相矛盾（ADR 0030 决策 4）。
STALE_GPS_TAGS: Final[tuple[str, ...]] = (
    "EXIF:GPSAltitude",
    "EXIF:GPSAltitudeRef",
    "EXIF:GPSDateStamp",
)

#: 地点的三层。**XMP 与 IPTC 两份都写**：Lightroom 与 Capture One 读 XMP，老软件读 IPTC。
#: XMP 用一级族名 ``XMP:City`` 而不是全名：exiftool 的 ``-G`` 按一级族回填键，写用全名、读用
#: 一级族会让**每一次成功的写入都被判成没写进去**（ADR 0030 决策 2）。
XMP_PLACE_TAGS: Final[tuple[str, ...]] = (
    "XMP:City",
    "XMP:State",
    "XMP:Country",
)

#: **只给存得下 IPTC 的容器用**（见 :data:`UNSTORABLE_TAGS`）：HEIC / HEIF / WebP / GIF 上这一组
#: 会被静默丢弃，所以压根不发 —— 不是「发了没生效」。
IPTC_PLACE_TAGS: Final[tuple[str, ...]] = (
    "IPTC:City",
    "IPTC:Province-State",
    "IPTC:Country-PrimaryLocationName",
)

#: 三层地名的全部标签。**白名单意义上的那一份**（HEIC 上实际发出去的只有其中的 XMP）。
PLACE_TAGS: Final[tuple[str, ...]] = (*XMP_PLACE_TAGS, *IPTC_PLACE_TAGS)

#: 拍摄时间的两个标签（ADR 0032）。**别的日期字段一个字都不动**：``CreateDate`` /
#: ``DateTimeDigitized`` / ``ModifyDate`` 记的是**文件处理过程**，改它们等于制造一条没发生过的记录。
#: 偏移不必跟着时间一起写：照片没记时区时那个字段压根不存在，:func:`_changes` 会跳过它。
CAPTURE_TIME_TAGS: Final[tuple[str, ...]] = (
    "EXIF:DateTimeOriginal",
    "EXIF:OffsetTimeOriginal",
)

#: **可以写回的全部字段**（唯一来源）。加一个 tag 必须同时改这里与对应的守护测试。
WRITABLE_TAGS: Final[frozenset[str]] = frozenset(
    COORDINATE_TAGS + STALE_GPS_TAGS + PLACE_TAGS + CAPTURE_TIME_TAGS
)

#: EXIF 那一组（坐标 + 顺带清掉的海拔与定位时间 + 拍摄时间）。
_EXIF_TAGS: Final[frozenset[str]] = frozenset(COORDINATE_TAGS + STALE_GPS_TAGS + CAPTURE_TIME_TAGS)

#: **哪些容器存不下哪些标签**（ADR 0036，逐格式逐标签实测）。表里只有**静默丢弃**的那些 ——
#: exiftool 返回 0、不警告、什么都不写，于是收到这种赋值的结果不是「写失败」，而是「回读校验必定
#: 对不上 → 整体回滚连写成功的 XMP 一起擦掉」。明确报错并返回非 0 的（BMP）不在表里。
UNSTORABLE_TAGS: Final[dict[str, frozenset[str]]] = {
    ".heic": frozenset(IPTC_PLACE_TAGS),
    ".heif": frozenset(IPTC_PLACE_TAGS),
    ".webp": frozenset(IPTC_PLACE_TAGS),
    # GIF 只存得下 XMP —— 连 EXIF 那一组（坐标与拍摄时间）也放不进去。
    ".gif": _EXIF_TAGS | frozenset(IPTC_PLACE_TAGS),
}

#: 坐标比对的容差（度）。exiftool 把十进制度存成度分秒、秒只给两位小数（精度约 3e-6 度），
#: 取 1e-5（约 1.1 米）既能容忍那点量化误差，又不至于把「差了一条街」当成写成功了。
COORDINATE_TOLERANCE_DEGREES: Final = 1e-5

#: exiftool 说的「这个格式写不了」那几句（不同版本措辞略有差别）。
_UNSUPPORTED_MARKERS: Final = (
    "unknown file type",
    "not a recognized",
    "invalid file type",
    "unsupported",
)

#: 没装 exiftool 时给用户的那一句。
INSTALL_HINT: Final = "安装后即可使用：brew install exiftool"

#: exiftool 用这几个词表示「这个字段是空的」。**直接用读取侧那一份**而不是各写一套：
#: 写回后回读校验与读取走的是同一个 exiftool，两边的「空」判错一次就等于刚写进去的东西
#: 被自己判成没写。差别只在 ``exif`` 那份多一个空串，而它在这里够不着 —— 两处都先
#: ``strip()``、再用真值判断挡掉空串。
_EMPTY_VALUES = _EXIF_EMPTY_VALUES

#: **回滚那次写回用的超时**，比 :data:`EXIFTOOL_TIMEOUT_SECONDS` 短：一次 :func:`_apply` 最坏
#: 要起 6 个 exiftool，各能卡满正常超时，而「回滚没有成功」正是用户最需要尽快看到结果的那条
#: 路径。回滚是一次**重写**（目标值明确，不需要「先试原地改再退回」那种试探），所以给更短的上限。
ROLLBACK_TIMEOUT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class Assignment:
    """一次写回里一个字段的目标值。**空串 = 把这个字段删掉**（exiftool 的写法）。"""

    tag: str
    value: str


class MetadataWriter(Protocol):
    """元数据写回的工具。默认是 exiftool，测试注入假实现。"""

    def available(self) -> bool:
        """这个工具现在能不能用。"""
        ...

    def read(self, path: Path) -> Mapping[str, str]:
        """读出一个文件此刻的原始键值（读不到的标签压根不出现在返回的字典里）。

        **不按标签过滤**：``-G`` 回填的键名与请求时写的不一定逐字相同（见 :data:`PLACE_TAGS`
        那一段），过滤会让回读对不上键。
        """
        ...

    def write(self, path: Path, assignments: Sequence[Assignment]) -> None:
        """把这些字段写进 ``path``。

        :raises MetadataWriteError: 工具拒绝了这次写入。
        """


@dataclass(frozen=True, slots=True)
class WriteResult:
    """一次写回的结果。"""

    #: 写完之后的引用（大小与修改时间都已经变了）；文件已经不在时为 ``None``。
    entry: PhotoRef | None
    #: 逐字段记下的原值与新值 —— 撤销写回的就是它。
    changes: tuple[FieldChange, ...] = ()
    #: 是否留下了可回滚的日志记录。为 ``False`` 时界面要说明「⌘Z 撤销不了」。
    journaled: bool = True
    #: 是否真的动过文件。什么都没变时为 ``False``，此时既没写盘也没记日志。
    written: bool = False


@dataclass(frozen=True, slots=True)
class UndoMetadataResult:
    """撤销一次写回的结果。"""

    #: 撤销之后的引用（文件已经不在时为 ``None``）。
    entry: PhotoRef | None
    #: 确实写回了原值的那些字段。
    restored: tuple[FieldChange, ...] = ()
    #: 没能撤销的字段与原因。只有一种原因：**文件里已经不是 Gallery 当初写下去的那个值了**
    #: （用户在别的工具里改过）—— 那时按原值盖回去就是毁掉那次改动，因此如实报告、绝不硬改。
    conflicts: tuple[Failure, ...] = ()
    journaled: bool = True

    @property
    def ok(self) -> bool:
        return not self.conflicts


class ExifToolWriter:
    """真正去起 exiftool 子进程的实现。"""

    def __init__(
        self,
        executable: str = EXIFTOOL_EXECUTABLE,
        *,
        timeout: float = EXIFTOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout = timeout

    @property
    def executable(self) -> str:
        return self._executable

    def available(self) -> bool:
        return shutil.which(self._executable) is not None

    def read(self, path: Path) -> Mapping[str, str]:
        path = Path(path)
        payload = self._run(
            [self._executable, "-j", "-G", "-sort", *EXIFTOOL_CHARSET_ARGS, str(path)],
            action="读取",
            path=path,
        )
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MetadataWriteError(
                f"读不出「{Path(path).name}」现在的元数据，没法确认写入结果。",
                path=Path(path),
                detail=f"{exc}；输出前 200 字：{payload[:200]}",
            ) from exc
        if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
            return {}
        return {key: as_text(value) for key, value in parsed[0].items()}

    def write(
        self,
        path: Path,
        assignments: Sequence[Assignment],
        *,
        timeout: float | None = None,
    ) -> None:
        """写回。**先试原地改**（``-overwrite_original_in_place``）：只动对应的那几个字节，像素数据的
        位置也不变。部分容器格式不支持它，那种情况退回普通覆盖 —— 失败后重试是安全的：
        所有字段都会幂等地再写一遍。

        **回滚那次要传一个更短的 ``timeout``** —— 见 :func:`_apply` 里那段：那条路径上
        用户正等着一个「照片恢复原样了吗」的答复，不能让它先等满两轮
        :data:`EXIFTOOL_TIMEOUT_SECONDS`。协议（:class:`MetadataWriter`）里**没有**这个
        参数，所以 :func:`_apply` 用 ``isinstance`` 判断而不是鸭子类型 ——
        给协议加上它就得改所有假实现，而它们根本不起进程。
        """
        path = Path(path)
        base = [self._executable, "-overwrite_original", *EXIFTOOL_CHARSET_ARGS, "-q", "-q"]
        fields = _argv(assignments)
        try:
            self._run(
                [*base, "-overwrite_original_in_place", *fields, str(path)],
                action="写入",
                path=path,
                timeout=timeout,
            )
        except MetadataWriteError:
            self._run([*base, *fields, str(path)], action="写入", path=path, timeout=timeout)

    def _run(
        self, argv: Sequence[str], *, action: str, path: Path, timeout: float | None = None
    ) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 - argv 是自己拼的，没有 shell
                argv,
                capture_output=True,
                timeout=self._timeout if timeout is None else timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MetadataWriteError(
                f"{action}元数据超时（超过 {self._timeout:g} 秒）：{path.name}。稍后再试一次。",
                path=path,
                detail=str(exc),
            ) from exc
        except OSError as exc:
            raise MetadataWriteError(
                f"无法运行 exiftool：{exc.strerror or exc}。",
                path=path,
                detail=str(exc),
            ) from exc
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if completed.returncode != 0 or _looks_like_failure(stderr):
            raise MetadataWriteError(
                _explain(stderr, path=path, action=action), path=path, detail=stderr
            )
        return completed.stdout.decode("utf-8", "replace")


def write_metadata(
    root: Path,
    ref: PhotoRef,
    assignments: Sequence[Assignment],
    *,
    writer: MetadataWriter | None = None,
) -> WriteResult:
    """把这些字段写进 ``ref`` 指向的照片，记账，并校验结果。

    :raises MetadataWriteError: 被拒绝、工具拒绝了、或者**写完回读对不上**。
        第三种情况下文件已经按原值写了回去。
    """
    root = Path(root)
    path = Path(ref.path)
    items = _checked(assignments, path)
    if not items:
        raise MetadataWriteError(_nothing_to_write(assignments, path), path=path)
    _guard(root, path)
    tool = _tool(writer)

    before = tool.read(path)
    changes = _changes(before, items)
    if not changes:
        # 与原来一模一样：不写盘也不记日志 —— 把「没改」记成一次改动，会让 ⌘Z 去撤一件空事。
        return WriteResult(entry=ref, journaled=True, written=False)

    # guard: allow-long-comment（红线 4：先记账再动文件，中间断电就改没了而日志上没有）
    # **先记账，再动文件。** 落一条 pending 只要几十微秒，而 :func:`_apply` 要起一到两次 exiftool
    # 子进程（几十到几百毫秒）—— 那个窗口里断电，原图就改了而日志上一条都没有（红线 4）。
    # 记不上账也照样往下走（报 ``journaled=False``）：不能因为记不上就把一次用户要求的写入拒掉。
    logged = _journal(root, path, changes, RESULT_PENDING, None)
    try:
        _apply(path, changes, tool)
    except MetadataWriteError as exc:
        _journal(root, path, changes, RESULT_FAILED, exc.message)
        raise
    # 终态记录顶掉 pending，撤销栈因此只算一次 ⌘Z（见 ``undo.pending_undo_runs``）。
    return WriteResult(
        entry=_refresh(path),
        changes=changes,
        journaled=_journal(root, path, changes, RESULT_OK, None) and logged,
        written=True,
    )


def undo_metadata(
    root: Path,
    record: WriteRecord,
    *,
    writer: MetadataWriter | None = None,
) -> UndoMetadataResult:
    """把一次写回改回去 —— **只改「当前值仍是我们写下去的那个」的字段**。

    别人改过的字段跳过并如实报告：按原值盖回去就是毁掉那次改动。**不往撤销栈里再压一条** ——
    撤销的那一次记成 :data:`ACTION_UNDO_METADATA`（弹栈用的动作名），否则会变成无限循环。
    """
    root = Path(root)
    path = Path(record.target)
    if not record.changes:
        raise MetadataWriteError("这条记录里没有可撤销的字段。", path=path)
    if not path.is_file():
        raise MetadataWriteError(
            f"撤销不了：找不到「{path.name}」，它可能已经被移动或删除了。",
            path=path,
        )
    tool = _tool(writer)
    current = tool.read(path)
    pending = [change for change in record.changes if _holds(change.after, change.tag, current)]
    settled = {change.tag for change in pending}
    conflicts = tuple(
        Failure(
            name=change.tag,
            message="这一项在 Gallery 之外被改过，写回原值会盖掉它，所以没有动它。",
            source=path,
        )
        for change in record.changes
        if change.tag not in settled
    )
    if not pending:
        _journal(
            root,
            path,
            (),
            RESULT_FAILED,
            "；".join(f"{item.name}：{item.message}" for item in conflicts),
            action=ACTION_UNDO_METADATA,
        )
        return UndoMetadataResult(entry=_refresh(path), conflicts=conflicts)
    try:
        _apply(
            path,
            tuple(FieldChange(change.tag, change.after, change.before) for change in pending),
            tool,
        )
    except MetadataWriteError as exc:
        _journal(root, path, pending, RESULT_FAILED, exc.message, action=ACTION_UNDO_METADATA)
        raise
    logged = _journal(root, path, pending, RESULT_OK, None, action=ACTION_UNDO_METADATA)
    return UndoMetadataResult(
        entry=_refresh(path), restored=tuple(pending), conflicts=conflicts, journaled=logged
    )


# ---- 内部 ---------------------------------------------------------------------


def _tool(writer: MetadataWriter | None) -> MetadataWriter:
    """取写回工具。**缺席就当场说清楚**，而不是让调用方去猜为什么写不进去。"""
    tool: MetadataWriter = writer if writer is not None else ExifToolWriter()
    if not tool.available():
        raise MetadataWriteError(f"没有找到 exiftool，改不了照片里的元数据。{INSTALL_HINT}")
    return tool


def unstorable_tags(path: Path) -> frozenset[str]:
    """这个容器存不下哪些标签（:data:`UNSTORABLE_TAGS`）。不在表里的后缀一个都存得下。"""
    return UNSTORABLE_TAGS.get(Path(path).suffix.lower(), frozenset())


def _nothing_to_write(assignments: Sequence[Assignment], path: Path) -> str:
    """没东西可写时的那一句。**「本来就没改」与「这个格式存不下」必须分开说**：共用一句会把后者
    说成前者，用户于是以为自己没保存成功、再点一次还是同样的结果（ADR 0036 决策 4）。
    """
    dropped = sorted({item.tag for item in assignments} & unstorable_tags(path))
    if not dropped:
        return "没有要写入的改动。"
    return (
        f"「{Path(path).name}」存不下要写的字段（{'、'.join(dropped)}）："
        f"这种格式放不了它们，一个字都没写进去。"
    )


def _write_for_rollback(
    tool: MetadataWriter, path: Path, assignments: Sequence[Assignment]
) -> None:
    """回滚那一次写回，**能用更短超时就用**（理由见 :func:`_apply`）。

    ``MetadataWriter`` 是 :class:`Protocol`，签名里**没有** ``timeout`` ——
    给协议加上它就得改所有假实现，而它们根本不起进程。所以这里问「这个工具接不接受
    这个关键字」，而不是 ``isinstance(tool, ExifToolWriter)``：后者在测试里够不到
    （测试注入的都是假实现），那条分支就成了**永远验不到的死代码**。

    用 ``inspect`` 查签名而不是直接 ``getattr(tool, "write")`` 之后调用 —— 后者遇到一个
    真的不支持该参数的第三种实现会当场 ``TypeError``，而那时照片已经被写坏过一次了。
    """
    try:
        supports = "timeout" in inspect.signature(tool.write).parameters
    except (TypeError, ValueError):  # pragma: no cover - C 实现的 callable 没有签名
        supports = False
    if supports:
        tool.write(path, assignments, timeout=ROLLBACK_TIMEOUT_SECONDS)  # type: ignore[call-arg]
    else:
        tool.write(path, assignments)


def _apply(path: Path, changes: Sequence[FieldChange], tool: MetadataWriter) -> None:
    """把 ``changes`` 的目标值写进去并**回读校验**；对不上就按原值写回去。

    **正向与撤销共用这一段**：两者的差别只有「写什么」，校验与回滚的规矩一个字都不变 ——
    各写各的时「撤销」那条路是不校验的。

    **回滚本身也要回读校验**（红线 4）：「按原值写回去」是一次**请求**，不是保证 —— exiftool
    在空间不够、写到一半被截断这些要处理的失败情形里照样返回 0。不回读就宣布「已恢复原样」，
    用户拿回的就是一张坐标被写坏的照片，而记录里前后值都齐着、看上去一切正常。

    **回滚那次用更短的超时**（:data:`_ROLLBACK_TIMEOUT_SECONDS`）。这一段最坏要起 6 个
    exiftool（两次 ``write`` 各两个 + 两次 ``read``），每个能卡满
    :data:`EXIFTOOL_TIMEOUT_SECONDS` —— 最坏一分半，而用户此刻正盯着一个不动的界面，
    不知道他刚按的 ⌘Z 到底成没成。
    """
    path = Path(path)
    assignments = [Assignment(change.tag, change.after or "") for change in changes]
    tool.write(path, assignments)
    after = tool.read(path)
    mismatched = [change.tag for change in changes if not _holds(change.after, change.tag, after)]
    if not mismatched:
        return
    detail = f"回读到的值与目标不符：{mismatched}"
    try:
        _write_for_rollback(
            tool,
            path,
            [Assignment(change.tag, change.before or "") for change in changes],
        )
    except MetadataWriteError as exc:
        raise MetadataWriteError(
            f"写入没有生效，而且没能恢复原值：{'、'.join(mismatched)}。"
            f"请检查这张照片（{exc.message}）。",
            path=path,
            detail=f"{detail}；回滚也失败了：{exc.detail}",
        ) from exc
    # 回滚也回读一次 —— 「按原值写回去」是一次**请求**，不是一次保证。
    restored = tool.read(path)
    unrestored = [
        change.tag for change in changes if not _holds(change.before, change.tag, restored)
    ]
    if unrestored:
        raise MetadataWriteError(
            f"写入没有生效，而且这几个字段的原值没能恢复：{'、'.join(unrestored)}。"
            f"请打开这张照片核对一下这几个值（{'、'.join(mismatched)} 本次没有写成功）。",
            path=path,
            detail=f"{detail}；回滚之后这些字段仍然不是原值：{unrestored}",
        )
    raise MetadataWriteError(
        f"写入没有生效，照片已经恢复原样：{'、'.join(mismatched)}。",
        path=path,
        detail=detail,
    )


def _argv(assignments: Sequence[Assignment]) -> list[str]:
    return [f"-{item.tag}={item.value}" for item in assignments]


def _checked(assignments: Sequence[Assignment], path: Path) -> tuple[Assignment, ...]:
    """去重（同一个标签以最后一条为准）、逐条查白名单，再**去掉这个容器存不下的标签**。

    **两道闸在同一个出口**：白名单管「该不该由 Gallery 改」，存储缺口管「这个文件里放不放得下」。

    存不下的那条**静默跳过、不报错**：用户要的是「地点写进去」，而 HEIC 上 IPTC 那一份从来
    就不是用户看得到的东西（ADR 0036 决策 1）。真全部被跳过时由 :func:`_nothing_to_write`
    说清是哪种「没东西可写」。
    """
    unstorable = unstorable_tags(path)
    unique: dict[str, str] = {}
    for item in assignments:
        if item.tag not in WRITABLE_TAGS:
            raise MetadataWriteError(
                f"「{item.tag}」不在可以修改的字段里。Gallery 只改拍摄时间与位置。",
                detail=item.tag,
            )
        if item.tag in unstorable:
            continue
        unique[item.tag] = item.value
    return tuple(Assignment(tag, value) for tag, value in unique.items())


def _guard(root: Path, path: Path) -> None:
    """确认要改的文件在允许的范围内。与改名的守卫同源：网格里根本不会出现配置目录与库根本身，
    这里仍然挡一道 —— 这是**会动用户文件**的入口（红线 1）。
    """
    if not is_supported_photo(path):
        raise MetadataWriteError(
            f"「{path.name}」不是 Gallery 支持的照片格式，不改它的元数据。", path=path
        )
    if not paths.is_within(root, path):
        raise MetadataWriteError(f"只能修改库里的照片：{path}。", path=path)
    if path == paths.config_dir(root):
        raise MetadataWriteError("这是 Gallery 自己的配置目录。", path=path)
    if not path.is_file():
        raise MetadataWriteError(f"找不到这个文件了：{path.name}。", path=path)


def _changes(before: Mapping[str, str], items: Sequence[Assignment]) -> tuple[FieldChange, ...]:
    """读到的原值 + 目标值 → 真正要写的那些字段（值没变的跳过）。"""
    result: list[FieldChange] = []
    for item in items:
        original = _normalize(item.tag, before.get(item.tag))
        target = item.value.strip() or None
        if original == target or _holds(target, item.tag, before):
            continue
        result.append(FieldChange(tag=item.tag, before=original, after=target))
    return tuple(result)


def _normalize(tag: str, value: object) -> str | None:
    """读回来的值 → **我们会写回去的那一种写法**（空白与「不知道」都当没有）。

    坐标必须归一：exiftool 读回来的是 ``35 deg 1' 16"``、我们写进去的是 ``35.021100``，照抄
    原值会让 ⌘Z 写出一个毫无意义的值、校验随即判它没写进去。半球标记同理（``N`` / ``North``）。
    """
    if tag == "EXIF:GPSLatitude":
        latitude = latitude_decimal({"EXIF:GPSLatitude": as_text(value)} if value else {})
        return f"{abs(latitude):.6f}" if latitude is not None else None
    if tag == "EXIF:GPSLongitude":
        longitude = longitude_decimal({"EXIF:GPSLongitude": as_text(value)} if value else {})
        return f"{abs(longitude):.6f}" if longitude is not None else None
    if tag in {"EXIF:GPSLatitudeRef", "EXIF:GPSLongitudeRef"}:
        initial = _initial(_text_of(value))
        return {"N": "N", "S": "S", "E": "E", "W": "W"}.get(initial or "")
    return _text_of(value)


def _text_of(value: object) -> str | None:
    """读回来的值：空白与「不知道」都当没有。"""
    if value is None:
        return None
    text = as_text(value).strip()
    if not text or text.lower() in _EMPTY_VALUES:
        return None
    return text


def _holds(expected: str | None, tag: str, current: Mapping[str, str]) -> bool:
    """文件里这个字段现在是不是 ``expected`` 那个值。

    **坐标按数值比、不是按字符串比**：写进去的是 ``30.27415``，读回来是 ``30 deg 16' 26.94"``
    —— 逐字符比会把每一次成功的写入都判成失败（ADR 0030 决策 2）。
    """
    if tag == "EXIF:GPSLatitude":
        return _axis_holds(expected, current, latitude_decimal)
    if tag == "EXIF:GPSLongitude":
        return _axis_holds(expected, current, longitude_decimal)
    if tag in {"EXIF:GPSLatitudeRef", "EXIF:GPSLongitudeRef"}:
        return _initial(current.get(tag)) == _initial(expected)
    return _text_of(current.get(tag)) == (expected.strip() or None if expected else None)


def _axis_holds(
    expected: str | None,
    current: Mapping[str, str],
    axis: Callable[[Mapping[str, str]], float | None],
) -> bool:
    """一个坐标轴：期望值与实际值差在容差之内就算写成了。

    **只比绝对值**：正负由 ``GPSLatitudeRef`` 那两个标签管，它们各是一次独立的赋值，在这里
    一起被比掉 —— 分成两处判会有一半的组合漏判。
    """
    if expected is None:
        return axis(current) is None
    try:
        wanted = abs(float(expected))
    except ValueError:
        return False
    actual = axis(current)
    return actual is not None and abs(abs(actual) - wanted) <= COORDINATE_TOLERANCE_DEGREES


def _initial(value: str | None) -> str | None:
    """半球标记只取首字母（exiftool 给 ``North``、Pillow 给 ``N``）。"""
    text = _text_of(value)
    return text[:1].upper() if text else None


def _journal(
    root: Path,
    path: Path,
    changes: Sequence[FieldChange],
    result: str,
    message: str | None = None,
    *,
    action: str = ACTION_METADATA,
) -> bool:
    """记一条。**只有写回本身带 ``changes``**；撤销靠 ``record.target`` 与它配对。"""
    return append_record(
        Path(root),
        WriteRecord(
            action=action,
            source=path,
            target=path,
            result=result,
            message=message,
            changes=tuple(changes),
        ),
    )


def _refresh(path: Path) -> PhotoRef | None:
    """写完重新取一次引用（大小与修改时间都已经变了）。"""
    try:
        return PhotoRef.from_path(path)
    except OSError:  # pragma: no cover - 写完立刻就没了是极罕见情况
        return None


def _looks_like_failure(stderr: str) -> bool:
    lowered = stderr.lower()
    return "error" in lowered or any(marker in lowered for marker in _UNSUPPORTED_MARKERS)


def _explain(stderr: str, *, path: Path, action: str) -> str:
    """把 exiftool 的话翻译成一句用户看得懂、且**指向下一步**的话。"""
    lowered = stderr.lower()
    if "permission denied" in lowered or "read-only" in lowered or "can't create" in lowered:
        return f"没有权限{action}「{path.name}」：它或它所在的磁盘可能被设成了只读。"
    if "no space left" in lowered or "磁盘空间不足" in lowered:
        return f"磁盘满了，写不进「{path.name}」：腾出点空间再试。"
    if any(marker in lowered for marker in _UNSUPPORTED_MARKERS):
        return f"这个格式改不了「{path.name}」的元数据（{_first_line(stderr)}）。"
    if "not enough room" in lowered or "truncated" in lowered:
        return f"「{path.name}」写不下了：文件结构没有足够的空间放这个字段。"
    if "error" in lowered:
        return f"{action}「{path.name}」的元数据失败：{_first_line(stderr)}"
    return f"{action}「{path.name}」的元数据失败。"


def _first_line(stderr: str) -> str:
    for line in stderr.splitlines():
        text = line.strip()
        if text:
            return text.removeprefix("Error: ")
    return "exiftool 没有给出原因"


__all__ = [
    "CAPTURE_TIME_TAGS",
    "COORDINATE_TAGS",
    "COORDINATE_TOLERANCE_DEGREES",
    "INSTALL_HINT",
    "IPTC_PLACE_TAGS",
    "PLACE_TAGS",
    "STALE_GPS_TAGS",
    "UNSTORABLE_TAGS",
    "WRITABLE_TAGS",
    "XMP_PLACE_TAGS",
    "Assignment",
    "ExifToolWriter",
    "MetadataWriter",
    "UndoMetadataResult",
    "WriteResult",
    "undo_metadata",
    "unstorable_tags",
    "write_metadata",
]
