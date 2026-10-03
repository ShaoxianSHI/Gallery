"""把编辑草稿另存成副本（ADR 0027）。

两条路由都要钉住：**只改 Orientation 那一路像素必须一字不变**，**重编码那一路必须
带得上 ICC**。这两条各自对应一个真实发生过的问题 —— 少一条，坏掉的都是用户看得见的
颜色与方向，而不是某个测试的红绿。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.media.decode import upright
from gallery.core.media.edit import Crop, Mirror, Rotate, compose_orientation, orientation_of
from gallery.core.metadata.export import (
    EDITED_SUFFIX,
    ExifToolWriter,
    ExportError,
    export_edited_copy,
)
from gallery.core.metadata.journal import ACTION_EXPORT, read_records


class FakeWriter:
    """假写入器：记录调用，不起子进程。"""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.orientation_writes: list[tuple[Path, int]] = []
        self.metadata_copies: list[tuple[Path, Path]] = []

    def available(self) -> bool:
        return self._available

    def write_orientation(self, path: Path, value: int) -> None:
        self.orientation_writes.append((path, value))
        with Image.open(path) as image:
            exif = image.getexif()
            exif[274] = value
            image.save(path, exif=exif)

    def copy_metadata(self, source: Path, target: Path) -> None:
        self.metadata_copies.append((source, target))


# ---- 只改 Orientation 那条路 -------------------------------------------------------


def test_rotation_only_route_does_not_touch_the_pixels(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """原文件的**字节**原样复制过去，只改那一个标签 —— 画质损失因此是零。"""
    path = make_photo("原图.jpg", size=(40, 20), orientation=1)
    before = path.read_bytes()
    writer = FakeWriter()

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=writer)

    assert result.preserved is True
    assert path.read_bytes() == before, "原文件不能被动过"
    assert writer.orientation_writes == [(result.created, 8)]
    assert writer.metadata_copies == [], "像素没变就不必搬元信息"


def test_both_routes_agree_on_the_picture(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """**两条路由存出来的图必须一模一样。**

    用户看到的是同一份编辑，走哪条路只是实现选择；两者一旦分岔，答案就取决于「草稿里
    有没有裁剪」这种用户看不见的东西 —— 那是最难排查的一类不一致。
    """
    path = make_photo("原图.jpg", size=(60, 30), orientation=1)
    steps = (Rotate(90), Mirror())
    expected = compose_orientation(1, steps)
    assert expected is not None

    preserved = export_edited_copy(tmp_path / "a", path, steps, writer=FakeWriter())
    reencoded = export_edited_copy(tmp_path / "b", path, steps, writer=FakeWriter(available=False))

    assert preserved.preserved is True
    assert reencoded.preserved is False
    with Image.open(preserved.created) as a, Image.open(reencoded.created) as b:
        # 「标上 Orientation」的那一张要摆正之后才是用户看到的样子，而摆正用的是
        # Gallery 自己那一份实现 —— 于是这里比的就是两张「屏幕上的图」。
        shown = upright(a)
        try:
            assert shown.size == b.size
            assert _within(shown, b, tolerance=_JPEG_TOLERANCE)
        finally:
            shown.close()


def _within(left: Image.Image, right: Image.Image, *, tolerance: int) -> bool:
    """两张图逐像素比（带容差）：JPEG 重编码必然与原字节不同，比的是画面。"""
    if left.size != right.size:
        return False
    return all(
        abs(a - b) <= tolerance for a, b in zip(left.tobytes(), right.tobytes(), strict=True)
    )


#: q95 重编码的容差。整条通道是 255，而误差只在个位数 —— 够严，不会放过转错方向。
_JPEG_TOLERANCE = 12


def test_base_orientation_is_the_composition_base(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """竖拍照片（存着 6）再左转 90°，副本应当是 1 而不是「从正常算起」的 8。"""
    path = make_photo("竖拍.jpg", size=(40, 20), orientation=6)
    writer = FakeWriter()

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=writer)

    assert writer.orientation_writes == [(result.created, 1)]
    assert orientation_of(Image.open(result.created)) == 1


def test_crop_forces_the_reencoding_route(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """裁剪切掉了像素，标签表达不出来 —— 必须老老实实重编码。"""
    path = make_photo("原图.jpg", size=(600, 400))
    writer = FakeWriter()

    result = export_edited_copy(tmp_path, path, (Crop((0.0, 0.0, 0.5, 1.0)),), writer=writer)

    assert result.preserved is False
    assert writer.orientation_writes == []
    assert writer.metadata_copies == [(path, result.created)]
    with Image.open(result.created) as image:
        assert image.size == (300, 400)


# ---- 重编码那一路：元信息与色彩 ----------------------------------------------------


def test_reencoding_route_keeps_the_icc_profile(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**ICC 不带过去就整体偏色** —— 广色域的照片最明显，而且看不出是「丢了」。

    这是这一路最容易漏的一条：Pillow 存 JPEG 时不会自己去翻 ``info['icc_profile']``，
    忘了显式传就是静默地按 sRGB 存。
    """
    path = make_photo("广色域.jpg", size=(40, 30))
    profile = _icc_profile()
    with Image.open(path) as image:
        image.save(path, format="JPEG", icc_profile=profile)

    result = export_edited_copy(
        tmp_path, path, (Crop((0.1, 0.1, 0.9, 0.9)),), writer=FakeWriter(available=False)
    )

    with Image.open(result.created) as created:
        assert created.info.get("icc_profile") == profile


def _icc_profile() -> bytes:
    """一份真的 sRGB ICC 配置（用 Pillow 现造，省得往仓库里塞二进制）。"""
    from PIL import ImageCms

    return (
        ImageCms.createProfile("sRGB")
        and ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    )


def test_reencoding_route_stamps_orientation_one(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """像素已经摆正，标签必须跟着摆成 1 —— 抄着旧值会被别的软件再转一次。"""
    path = make_photo("竖拍.jpg", size=(40, 20), orientation=6)

    result = export_edited_copy(
        tmp_path, path, (Crop((0.0, 0.0, 1.0, 0.5)),), writer=FakeWriter(available=False)
    )

    with Image.open(result.created) as created:
        assert created.getexif().get(274) == 1


def test_png_keeps_its_transparency(tmp_path: Path) -> None:
    """PNG 走重编码，但它是**无损**格式：透明不能被压成一片黑。"""
    path = tmp_path / "透明.png"
    image = Image.new("RGBA", (80, 60), (255, 0, 0, 0))
    image.save(path)
    image.close()

    result = export_edited_copy(
        tmp_path, path, (Crop((0.0, 0.0, 0.5, 1.0)),), writer=FakeWriter(available=False)
    )

    with Image.open(result.created) as created:
        assert created.mode == "RGBA"
        assert created.getpixel((5, 5))[3] == 0
        assert created.size == (40, 60)


# ---- 名字：绝不覆盖 ----------------------------------------------------------------


def test_the_copy_is_named_with_the_suffix(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    path = make_photo("京都.jpg", size=(40, 30))

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=FakeWriter())

    assert result.created.name == f"京都{EDITED_SUFFIX}.jpg"


def test_an_existing_copy_is_never_overwritten(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """同名已存在就加编号 —— 用户自己放进去的东西不能被顶掉（与粘贴同一条规矩）。"""
    path = make_photo("京都.jpg", size=(40, 30))
    existing = tmp_path / f"京都{EDITED_SUFFIX}.jpg"
    existing.write_bytes("用户自己的东西".encode())

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=FakeWriter())

    assert result.created.name == f"京都{EDITED_SUFFIX} 2.jpg"
    assert existing.read_bytes() == "用户自己的东西".encode()


def test_the_suffix_sits_before_the_extension(tmp_path: Path) -> None:
    path = tmp_path / "风景.jpeg"
    Image.new("RGB", (60, 40)).save(path)

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=FakeWriter())

    assert result.created.name == f"风景{EDITED_SUFFIX}.jpeg"


# ---- 记账 ------------------------------------------------------------------------


def test_saving_is_journalled(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """另存要记账，但它**不在撤销栈里** —— 撤销它就是删文件（红线 1）。"""
    from gallery.core.metadata.undo import pending_undo_runs

    path = make_photo("原图.jpg", size=(40, 30))

    result = export_edited_copy(tmp_path, path, (Rotate(90),), writer=FakeWriter())

    records = [record for record in read_records(tmp_path) if record.action == ACTION_EXPORT]
    assert [(r.source, r.target, r.succeeded) for r in records] == [(path, result.created, True)]
    assert pending_undo_runs(tmp_path) == []


def test_a_failed_save_leaves_nothing_behind(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """失败时把刚写了一半的文件清掉 —— 库里不能留下一张解码不了的「照片」。"""

    class Exploding(FakeWriter):
        def write_orientation(self, path: Path, value: int) -> None:
            path.write_bytes("半截".encode())
            raise OSError("写不进去了")

    path = make_photo("原图.jpg", size=(40, 30))

    with pytest.raises(ExportError):
        export_edited_copy(tmp_path, path, (Rotate(90),), writer=Exploding())

    assert list(tmp_path.glob("*edited*")) == []
    assert [r.succeeded for r in read_records(tmp_path) if r.action == ACTION_EXPORT] == [False]


def test_an_unreadable_source_is_reported_as_such(tmp_path: Path) -> None:
    """「读不出这张照片」与「存不下来」要分得开 —— 前者是文件坏了。"""
    path = tmp_path / "坏文件.jpg"
    path.write_bytes("这不是一张图片".encode())

    with pytest.raises(ExportError, match="坏文件"):
        export_edited_copy(tmp_path, path, (Rotate(90),), writer=FakeWriter())


# ---- 降级 ------------------------------------------------------------------------


def test_without_exiftool_the_result_says_metadata_is_incomplete(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    path = make_photo("原图.jpg", size=(40, 30))

    result = export_edited_copy(tmp_path, path, (Mirror(),), writer=FakeWriter(available=False))

    assert result.degraded is True
    assert result.preserved is False, "没有 exiftool 就写不了标签，只能重编码"


# ---- 真的 exiftool（只测它拼出来的命令行）------------------------------------------


def test_the_real_writer_speaks_exiftool() -> None:
    """真实写入器只会起两种命令，都用 ``-Orientation#=``（HEIC 必须要带 ``#``）。"""
    writer = ExifToolWriter(executable="exiftool-not-installed")

    assert writer.available() is False


@pytest.mark.skipif(not ExifToolWriter().available(), reason="本机没装 exiftool")
def test_orientation_is_written_with_the_numeric_form(tmp_path: Path) -> None:
    """``-Orientation=6`` 写不进 HEIF（实测报 "not in PrintConv"），``-Orientation#=6`` 才行。"""
    path = tmp_path / "方向.jpg"
    Image.new("RGB", (40, 20)).save(path)

    ExifToolWriter().write_orientation(path, 6)

    assert orientation_of(Image.open(path)) == 6
