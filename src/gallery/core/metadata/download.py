"""把一次下载下来的字节**作为一个新文件**写进用户的文件夹（「导入影片」抓来的封面，ADR 0063）。

与 :mod:`gallery.core.metadata.paste` 同属「只增不改」：写出来的是新文件，**绝不覆盖**已有同名
（撞上就按访达的规矩加编号），也**不进撤销栈**（撤销它就是删文件，而删除只允许用户亲手触发）。
一次写入仍然在配置目录的写操作日志里留一条记录 —— 用户该看得见「什么时候、往哪个文件夹写了
一个什么文件」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.atomic import write_bytes_atomic
from gallery.core.errors import MovieImportError
from gallery.core.i18n import t
from gallery.core.metadata.journal import (
    ACTION_DOWNLOAD,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_target, occupied_names


@dataclass(frozen=True, slots=True)
class SavedFile:
    """一次「把下载的字节写成新文件」的结果。"""

    #: 实际写出来的路径（可能加了编号，见 :func:`free_target`）。
    created: Path
    #: 是否留下了可回滚的日志记录。为 ``False`` 时界面要说明「没能记下操作日志」。
    journaled: bool = True


#: 认得出的图片格式。**按字节认，不按地址猜**：同一个来源会混着给 PNG 与 JPEG，
#: 而有的地址连后缀都没有（格式写在查询串里）。写错后缀的那张封面解不出来，却不报错。
_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF8", ".gif"),
)


def image_suffix(data: bytes) -> str | None:
    """这一串字节是什么格式的图片（``.jpg`` / ``.png`` / …）；认不出就是 ``None``。"""
    for magic, suffix in _MAGIC:
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return None


def save_downloaded_file(
    root: Path,
    directory: Path,
    wanted_name: str,
    data: bytes,
    *,
    batch: str | None = None,
) -> SavedFile:
    """把 ``data`` 作为 ``wanted_name`` 写进 ``directory``（撞名加编号），返回写出来的文件。

    :param root: 库根。记账写在它下面（``journal/``），与其余写操作同一处。
    :raises MovieImportError: ``directory`` 不在库内 / 不是一个文件夹，或者写不下去。
    """
    destination = Path(directory)
    try:
        paths.guard_within_root(root, destination)
    except paths.GuardError as exc:
        raise MovieImportError(
            t("metadata.download.guard", reason=str(exc)), path=destination
        ) from exc
    if not destination.is_dir():
        raise MovieImportError(
            t("metadata.download.not_folder", name=destination.name), path=destination
        )

    created = free_target(destination, wanted_name, occupied_names(destination))
    if created is None:
        raise MovieImportError(
            t("metadata.download.no_name", name=destination.name), path=destination
        )
    try:
        # 原子写：半截文件不会留在用户的文件夹里（与粘贴对「半截文件」的处置同一取向）。
        write_bytes_atomic(created, data)
    except OSError as exc:
        raise MovieImportError(
            t("metadata.download.failed", name=destination.name, reason=exc.strerror),
            path=destination,
        ) from exc

    journaled = append_record(
        root,
        WriteRecord(
            action=ACTION_DOWNLOAD,
            source=Path(wanted_name),
            target=created,
            result=RESULT_OK,
            batch=batch,
        ),
    )
    return SavedFile(created=created, journaled=journaled)


__all__ = ["SavedFile", "image_suffix", "save_downloaded_file"]
