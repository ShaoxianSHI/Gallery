"""把编辑草稿存成一份新文件 —— 界面上是「保存编辑后的副本」⌘S（见 ADR 0027）。

原文件**一个字节都不动**，编辑的结果落到同目录下的一个新文件里（ADR 0027 决策 2）。
这一条与粘贴（ADR 0018）是同一类操作，因此在同一个地方继承着同一份约束：

- **只增不改**：产物是新文件，源不动；
- **绝不覆盖**：同名就在后面加编号（``一张-edited.jpg`` → ``一张-edited 2.jpg``），
  规则与 :func:`~gallery.core.metadata.naming.free_name_in` 同源；
- **记账但不可撤销**：撤销一次保存就是删文件，而删除只允许用户在界面上亲手触发
  （红线 1）。因此它只写 :data:`~gallery.core.metadata.journal.ACTION_EXPORT` 一条记录，
  **不进撤销栈**（理由与粘贴、新建相册完全一样）。

## 两条路由

:func:`export_edited_copy` 按草稿内容在两条路里挑一条，两条路的**像素结果完全一样**
（那张表与它的测试在 :mod:`gallery.core.media.edit`）：

**一、只改 ``Orientation`` 标签（像素与元信息原样保留）**
    草稿只有旋转与镜像时，它们合成得出一个新的 ``Orientation``。这时**不解码、不重编码**：
    原文件的字节复制过去，只让 exiftool 把那个标签改成要的值。于是 EXIF / IPTC / XMP /
    ICC **逐字段原封不动**（包括解码器碰不到的 MakerNote），一张 30 MB 的 JPEG 也只要
    几十毫秒。

**二、重新编码像素（含裁剪时只能走这条）**
    全分辨率解码（**不降采样**，见 :func:`~gallery.core.media.decode.decode_rgba`）→ 施加草稿
    → 存回去，再让 exiftool 把元信息从原文件搬过来。

重编码那一路有三个坑，都必须堵（否则「编辑前后元信息一致」这句话是假的）：

- **ICC 色彩描述文件**不显式带上就**整体偏色**（宽色域的照片最明显）；
- **``Orientation``** 必须改写成 1 —— 像素已经摆正了，还抄着旧值会被别的软件再转一次；
- **XMP 内嵌预览图与 JFIF 缩略图**画的是编辑前的图，**留着比没有更糟**（访达的快速查看
  会显示没转过的照片），一并删掉。

无 exiftool 时降级为「Pillow 自己写基础 EXIF」，并在结果里如实说元信息不完整 ——
与 :mod:`gallery.core.metadata.exif` 那套降级口径一致。
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from PIL import Image

from gallery.core.errors import GalleryError
from gallery.core.media.decode import HEIF_SUPPORT, upright
from gallery.core.media.edit import (
    ORIENTATION_TAG,
    EditStep,
    apply_steps,
    compose_orientation,
    orientation_of,
)
from gallery.core.metadata.journal import (
    ACTION_EXPORT,
    RESULT_FAILED,
    RESULT_OK,
    WriteRecord,
    append_record,
)
from gallery.core.metadata.naming import free_name_in, split_suffix

#: 副本名字夹在主干与扩展名之间的那一段。
EDITED_SUFFIX: Final = "-edited"

#: 另存走 re-encode 时 JPEG 的质量与色度抽样。
#:
#: JPEG 重编码必然有损（量化表就是这么定的，哪怕 quality=100 也不同于原字节），于是
#: 这里在「画质」与「体积」之间取偏画质的一档；色度抽样用 4:4:4 而不是默认的 4:2:0，
#: 因为原片里的饱和色块正是 4:2:0 最先糊掉的地方。
JPEG_QUALITY: Final = 95
JPEG_SUBSAMPLING: Final = "4:4:4"

#: 起一次 exiftool 的超时（秒）。与读取那一侧同值。
EXIFTOOL_TIMEOUT_SECONDS: Final = 15.0

#: exiftool 的可执行文件名（运行时外部依赖，不进 pyproject.toml）。
EXIFTOOL_EXECUTABLE: Final = "exiftool"

#: ``-Orientation`` 必须带 ``#``（跳过 PrintConv）才能写进 HEIF。
#:
#: 实测：``exiftool -Orientation=6`` 作用在 HEIC 上会报 "Can't convert IFD0:Orientation
#: (not in PrintConv)" 且什么都不做；写成 ``-Orientation#=6`` 才写得进去，而且
#: 读回来正是 ``Rotate 90 CW``。JPEG 上两种写法都行，用带 ``#`` 的那个统一。
_ORIENTATION_WRITE: Final = "-Orientation#="


class ExportError(GalleryError):
    """另存副本被拒绝或失败（源打不开、格式存不了、目录没写权限）。"""


class MetadataWriter(Protocol):
    """要改 ``Orientation``、或要把元信息搬到新文件上时用得着的那些能力。

    默认实现走 exiftool 子进程（红线 3：元数据写回必须走它，不得手写二进制）。
    测试注入假实现，因此**这条路径不依赖本机装了 exiftool**。
    """

    def available(self) -> bool:
        """这个写入器现在能不能用（没有 exiftool 就是降级那一档）。"""
        ...

    def write_orientation(self, path: Path, value: int) -> None:
        """把 ``path`` 的方向改成 ``value``，顺手删掉编辑前那张内嵌预览图。

        :raises MetadataError: 命令跑不通或 exiftool 报错。
        """
        ...

    def copy_metadata(self, source: Path, target: Path) -> None:
        """把 ``source`` 的全部元信息搬到 ``target``，并把与新像素矛盾的标签摆正。

        :raises MetadataError: 命令跑不通或 exiftool 报错。
        """
        ...


class ExifToolWriter:
    """真的去起 exiftool 子进程的实现。"""

    def __init__(
        self,
        executable: str = EXIFTOOL_EXECUTABLE,
        *,
        timeout: float = EXIFTOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which(self._executable) is not None

    def write_orientation(self, path: Path, value: int) -> None:
        self._run(
            [
                f"{_ORIENTATION_WRITE}{value}",
                # 留着一张编辑前的内嵌预览图比没有更糟：访达的快速查看会显示没转过的那张。
                "-XMP:Thumbnails=",
                "-PreviewImage=",
                "-overwrite_original",
                str(path),
            ]
        )

    def copy_metadata(self, source: Path, target: Path) -> None:
        self._run(
            [
                f"-tagsfromfile={source}",
                "-all:all",
                # 像素已经摆正，抄来的方向必须改成 1，否则别的软件会再转一次。
                f"{_ORIENTATION_WRITE}1",
                "-XMP:Thumbnails=",
                "-PreviewImage=",
                "-overwrite_original",
                str(target),
            ]
        )

    def _run(self, arguments: list[str]) -> None:
        from gallery.core.errors import MetadataError

        try:
            completed = subprocess.run(
                [self._executable, *arguments],
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            raise MetadataError(
                f"运行 exiftool 失败：{error}。安装后重试：brew install exiftool",
                detail=str(error),
            ) from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            message = stderr or completed.stdout.decode("utf-8", "replace").strip()
            raise MetadataError("exiftool 没有写成功。", detail=message)


@dataclass(frozen=True, slots=True)
class ExportResult:
    """另存一次的结果。"""

    #: 源文件（**没有被改动**）。
    source: Path
    #: 同目录里新出现的那个。
    created: Path
    #: ``True`` 表示走的是「只改 Orientation」那条路：像素与元信息原样保留。
    preserved: bool
    #: 没有 exiftool，元信息只保留了一部分（界面要如实说出来）。
    degraded: bool
    #: 是否记下了这次保存。为 ``False`` 时界面应说明它不可追溯。
    journaled: bool = True


def export_edited_copy(
    root: Path,
    source: Path,
    steps: tuple[EditStep, ...],
    *,
    writer: MetadataWriter | None = None,
) -> ExportResult:
    """把 ``source`` 按 ``steps`` 存成一份新文件，返回结果。

    :param root: 库根，记账写在它下面（与其它写操作同一处）。
    :param source: 原图，**只读**。
    :param steps: 编辑草稿（见 :mod:`gallery.core.media.edit`）。
    :param writer: 替换默认的 exiftool 写入器（测试注入假实现）。
    :raises ExportError: 源打不开、格式存不了、或目标目录写不进去。

    **失败时把本模块刚写了一半的文件清掉**，与粘贴同一条规矩：库里不能留下一张
    解码不了的「照片」。
    """
    source = Path(source)
    target = _target_for(source)
    tool = writer if writer is not None else ExifToolWriter()
    usable_tool = tool.available()
    try:
        orientation = compose_orientation(_orientation_of_file(source), steps)
        if orientation is not None and usable_tool:
            _copy_bytes(source, target)
            tool.write_orientation(target, orientation)
            preserved = True
        else:
            _write_pixels(source, target, steps)
            if usable_tool:
                tool.copy_metadata(source, target)
            preserved = False
    except (GalleryError, OSError, ValueError, KeyError) as error:
        _remove_partial(target)
        message = error.message if isinstance(error, GalleryError) else f"没能存下这份编辑：{error}"
        append_record(
            root,
            WriteRecord(
                action=ACTION_EXPORT,
                source=source,
                target=target,
                result=RESULT_FAILED,
                message=message,
            ),
        )
        if isinstance(error, GalleryError):
            raise
        raise ExportError(message, path=source, detail=str(error)) from error

    return ExportResult(
        source=source,
        created=target,
        preserved=preserved,
        degraded=not usable_tool,
        journaled=append_record(
            root,
            WriteRecord(
                action=ACTION_EXPORT,
                source=source,
                target=target,
                result=RESULT_OK,
            ),
        ),
    )


def _target_for(source: Path) -> Path:
    """副本该叫什么：``一张.jpg`` → ``一张-edited.jpg``，同名已占则加编号。

    加编号走 :func:`free_name_in`，于是「绝不覆盖用户已有的东西」这条规矩是同一份
    实现 —— 粘贴、改名、移动到相册那几个入口也都用它。
    """
    stem, suffix = split_suffix(source.name)
    name = free_name_in(source.parent, f"{stem}{EDITED_SUFFIX}{suffix}")
    if name is None:
        raise ExportError("这个名字太长，再加编号也放不下了。", path=source)
    return source.parent / name


def _orientation_of_file(source: Path) -> int:
    """读出源文件**存着的**方向 —— 合成 Orientation 的基数，不能想当然从 1 开始。"""
    try:
        with Image.open(source) as image:
            return orientation_of(image)
    except OSError as error:
        raise ExportError(
            f"读不出这张照片：{source.name}。", path=source, detail=str(error)
        ) from error


def _copy_bytes(source: Path, target: Path) -> None:
    """原文件的字节**一字不改**地复制过去。

    刻意不用 ``copy2``：修改时间要跟着「新文件」而不是原文件，否则库里按修改时间排的
    那一层会出现两张一模一样的照片，而其中一张看起来已经存在了很久。
    """
    shutil.copyfile(source, target)
    shutil.copymode(source, target)


def _write_pixels(source: Path, target: Path, steps: tuple[EditStep, ...]) -> None:
    """全分辨率解码 → 施加草稿 → 存成同格式的文件。

    **像素与 ICC 由这里负责，其余元信息交给 exiftool**（或没有它时的 Pillow 降级）——
    职责分开是因为「像素对不对」与「元信息全不全」是两种失败，各自要能单独验证。
    """
    with Image.open(source) as image:
        icc = image.info.get("icc_profile")
        image_format = (image.format or "").upper()
        uprighted = upright(image)
        try:
            edited = apply_steps(uprighted, steps)
            try:
                exif = edited.getexif()
                # 像素已经摆正；方向必须摆成 1（而不是留着源文件里那个），否则
                # 「显示时已经摆正」与「标签说还要转」会在别的软件里打架。
                exif[ORIENTATION_TAG] = 1
                _save(edited, target, image_format, icc, exif)
            finally:
                if edited is not uprighted:
                    edited.close()
        finally:
            if uprighted is not image:
                uprighted.close()


def _save(
    image: Image.Image,
    target: Path,
    image_format: str,
    icc: bytes | None,
    exif: Image.Exif,
) -> None:
    """按原格式存出去，并保留 ICC 与基础 EXIF（exiftool 缺席时这两样就是全部了）。"""
    suffix = target.suffix.lower()
    try:
        if image_format in {"JPEG", "MPO"} or suffix in {".jpg", ".jpeg"}:
            # JPEG 存不下透明，而原图带透明的场合（截图转存）不该在这里报一个
            # 没人看得懂的错：按 RGB 存，颜色不会错。
            flattened = image if image.mode in {"RGB", "L"} else image.convert("RGB")
            try:
                flattened.save(
                    target,
                    format="JPEG",
                    quality=JPEG_QUALITY,
                    subsampling=JPEG_SUBSAMPLING,
                    icc_profile=icc,
                    exif=exif,
                )
            finally:
                if flattened is not image:
                    flattened.close()
            return
        if image_format in {"PNG", "PNG8"} or suffix == ".png":
            # PNG 是无损的：重压缩只会改体积，不会动像素，带透明的那部分也原样留着。
            image.save(target, format="PNG", icc_profile=icc, exif=exif)
            return
        if image_format in {"HEIF", "HEIC"} or suffix in {".heic", ".heif"}:
            if not HEIF_SUPPORT:
                raise ExportError("缺少 HEIC/HEIF 的编码支持，存不了这个格式。", path=target)
            image.save(target, format="HEIF", quality=100, icc_profile=icc, exif=exif)
            return
        if image_format in {"TIFF", "MPEG"} or suffix in {".tif", ".tiff"}:
            image.save(target, format="TIFF", icc_profile=icc, exif=exif)
            return
        # 其余格式（GIF / WebP / BMP…）存原样。这一支**不塞 EXIF**：那些插件各有各的
        # 关键字，塞错了报出来的错用户看不懂，而 exiftool 那一路本来会把元信息补回来。
        image.save(target, format=image_format or None, icc_profile=icc)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ExportError(
            f"存不下这个格式（{suffix or image_format or '未知'}）：{error}",
            path=target,
            detail=str(error),
        ) from error


def _remove_partial(target: Path) -> None:
    """把**本模块刚写了一半**的那个副本清掉。别的文件一个都不碰。"""
    # 清不掉就如实留着，横幅会说明没存成 —— 总比假装删了强。
    with contextlib.suppress(OSError):
        target.unlink(missing_ok=True)


__all__ = [
    "EDITED_SUFFIX",
    "JPEG_QUALITY",
    "JPEG_SUBSAMPLING",
    "ExifToolWriter",
    "ExportError",
    "ExportResult",
    "MetadataWriter",
    "export_edited_copy",
]
