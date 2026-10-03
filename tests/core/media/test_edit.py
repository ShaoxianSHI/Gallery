"""编辑草稿的运算：规范化、Orientation 合成，以及它们与像素渲染的一致性。

最要紧的是最后那一组：**「走标签路」（改 Orientation）与「走像素路」（真的转像素）
必须给出同一个画面**。这不是两条实现恰好对上了，而是同一套群论的两个投影 ——
测试直接比对两张图，任何一边写错都会变红。
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageOps

from gallery.core.media.edit import (
    MIN_CROP_EDGE,
    Crop,
    EditStep,
    Mirror,
    Rotate,
    apply_steps,
    compose_orientation,
    crop_box,
    is_crop_big_enough,
    is_identity,
    normalized,
    orientation_of,
)

#: 一张不对称的小图：左上红、右上蓝、左下绿、右下黄。
#: 转 / 镜之后每个角落在哪里一眼能看出来，不必去比对像素。
_CORNERS = (
    ("左上", (255, 0, 0)),
    ("右上", (0, 0, 255)),
    ("左下", (0, 255, 0)),
    ("右下", (255, 255, 0)),
)


def _make_image(width: int = 40, height: int = 26) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            index = (1 if x >= width // 2 else 0) + (2 if y >= height // 2 else 0)
            pixels[x, y] = _CORNERS[index][1]
    return image


def _corner_colors(image: Image.Image) -> dict[str, tuple[int, ...]]:
    """读回四角，确认画面转到了该在的位置。"""
    width, height = image.size
    points = {
        "左上": (1, 1),
        "右上": (width - 2, 1),
        "左下": (1, height - 2),
        "右下": (width - 2, height - 2),
    }
    return {name: tuple(image.getpixel(point)[:3]) for name, point in points.items()}  # type: ignore[misc]


#: JPEG 是有损的，两份文件哪怕像素完全一样，解回来的颜色也会差个一两。
#: 容差只要大过压缩误差、又远小于四种颜色之间的差距（那是整条通道），就足够判方向。
_COLOR_TOLERANCE = 12


def _same_corners(left: dict[str, tuple[int, ...]], right: dict[str, tuple[int, ...]]) -> bool:
    return all(
        len(a) == len(b) and all(abs(x - y) <= _COLOR_TOLERANCE for x, y in zip(a, b, strict=True))
        for a, b in zip(left.values(), right.values(), strict=True)
    )


def _stored(path_factory, orientation: int, tmp_path):  # noqa: ANN001, ANN202 - 测试助手
    """造一张带指定 Orientation 的 JPEG 并打开它（**未摆正**）。"""
    path = tmp_path / f"src-{orientation}.jpg"
    image = _make_image()
    exif = image.getexif()
    exif[274] = orientation
    image.save(path, format="JPEG", quality=95, exif=exif)
    return path


# ---- 规范化 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ((), ()),
        ((Rotate(90),), (Rotate(90),)),
        ((Rotate(90), Rotate(270)), ()),
        ((Rotate(90), Rotate(90)), (Rotate(180),)),
        ((Rotate(90), Rotate(90), Rotate(90)), (Rotate(270),)),
        ((Mirror(), Mirror()), ()),
        ((Mirror(), Rotate(90)), (Mirror(), Rotate(90))),
        # 镜像两次再加一次旋转：镜像是自己的逆运算，不该被当成「什么都没做」。
        ((Mirror(), Mirror(), Rotate(90)), (Rotate(90),)),
    ],
)
def test_normalized_collapses_rotation_and_mirror_runs(
    steps: tuple[EditStep, ...], expected: tuple[EditStep, ...]
) -> None:
    assert normalized(steps) == expected


def test_normalized_does_not_merge_across_a_crop() -> None:
    """裁剪两侧的旋转**不能**合并 —— 裁剪框记的是它当时那张图的坐标（ADR 0027 决策 4）。"""
    crop = Crop((0.0, 0.0, 0.5, 0.5))
    assert normalized((Rotate(90), crop, Rotate(90))) == (Rotate(90), crop, Rotate(90))


def test_is_identity_sees_through_a_round_trip() -> None:
    assert is_identity(())
    assert is_identity((Rotate(90), Rotate(270)))
    assert is_identity((Mirror(), Mirror()))
    assert not is_identity((Rotate(90),))
    assert not is_identity((Crop((0.1, 0.1, 0.9, 0.9)),))


def test_crop_is_never_identity() -> None:
    """两块拼起来等于整张，规范化后仍留着 —— 它的结果本来就是同一张图，不需要优化。"""
    assert not is_identity((Crop((0.0, 0.0, 0.5, 0.5)), Crop((0.0, 0.0, 1.0, 1.0))))


# ---- Orientation 合成 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "steps", "expected"),
    [
        (1, (), 1),
        # EXIF 的方向是**顺时针**说的，而 ``Rotate`` 记的是逆时针：两者差一个负号，
        # 这条参数化就是钉住那个负号的。
        (1, (Rotate(90),), 8),
        (1, (Rotate(180),), 3),
        (1, (Rotate(270),), 6),
        (1, (Mirror(),), 2),
        # 以基数合成：竖拍照片（顺时针 90° = 6）再逆时针转 90°，答案是 1 而不是从 1 重算。
        (6, (Rotate(90),), 1),
        (6, (), 6),
        (8, (Rotate(90),), 3),
        (8, (Rotate(270),), 1),
        (2, (Mirror(),), 1),
        (1, (Rotate(90), Rotate(90)), 3),
        (1, (Rotate(90), Mirror()), 7),
        (1, (Mirror(), Rotate(90)), 5),
    ],
)
def test_compose_orientation(base: int, steps: tuple[EditStep, ...], expected: int) -> None:
    assert compose_orientation(base, steps) == expected


def test_compose_orientation_refuses_a_crop() -> None:
    """裁剪切掉了像素，标签表达不出来 —— 这一档必须交给重新编码那条路。"""
    assert compose_orientation(1, (Rotate(90), Crop((0.0, 0.0, 0.8, 0.8)))) is None


def test_compose_orientation_treats_a_bogus_base_as_normal() -> None:
    assert compose_orientation(0, (Rotate(90),)) == 8
    assert compose_orientation(99, ()) == 1


# ---- 与像素渲染一致 --------------------------------------------------------------


@pytest.mark.parametrize("base", [1, 2, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize(
    "steps",
    [
        (),
        (Rotate(90),),
        (Rotate(180),),
        (Rotate(270),),
        (Mirror(),),
        (Mirror(), Rotate(90)),
        (Mirror(), Rotate(270)),
        (Rotate(90), Mirror()),
        (Rotate(90), Rotate(90), Mirror()),
        (Mirror(), Mirror(), Rotate(90)),
    ],
)
def test_orientation_route_agrees_with_the_pixel_route(
    tmp_path, base: int, steps: tuple[EditStep, ...]
) -> None:
    """**「改 Orientation」与「真的转像素」必须给出同一张图。**

    存副本时走哪条路由这里决定（见 ``core.metadata.export``），而两条路由共用下面这张
    表 —— 它们要是各写一遍，早晚会有一边转错，而那种错在别的软件里才看得出来。
    """
    with Image.open(_stored(None, base, tmp_path)) as source:
        via_pixels = apply_steps(ImageOps.exif_transpose(source), normalized(steps))
        expected_orientation = compose_orientation(base, steps)
        assert expected_orientation is not None
        with Image.open(_stored(None, expected_orientation, tmp_path)) as relabelled:
            expected = ImageOps.exif_transpose(relabelled)
        assert via_pixels.size == expected.size
        assert _same_corners(_corner_colors(via_pixels), _corner_colors(expected)), _corner_colors(
            via_pixels
        )


def test_pixel_route_moves_the_corners(tmp_path) -> None:
    """顺带看一眼像素本身：逆时针 90° 之后，原来的左上应当出现在左下。"""
    image = _make_image()
    rotated = apply_steps(image, (Rotate(90),))
    assert rotated.size == (26, 40)
    corners = _corner_colors(rotated)
    assert corners["左下"] == dict(_CORNERS)["左上"]
    mirrored = apply_steps(image, (Mirror(),))
    assert _corner_colors(mirrored)["左上"] == dict(_CORNERS)["右上"]


def test_crop_really_cuts_pixels() -> None:
    image = _make_image()
    cropped = apply_steps(image, (Crop((0.0, 0.0, 0.5, 0.5)),))
    assert cropped.size == (20, 13)


def test_apply_steps_returns_the_same_object_when_there_is_nothing_to_do() -> None:
    image = _make_image()
    assert apply_steps(image, ()) is image


# ---- 归一化坐标与尺寸 --------------------------------------------------------------


def test_crop_box_snaps_inside_the_canvas() -> None:
    assert crop_box((0.0, 0.0, 1.0, 1.0), (40, 26)) == (0, 0, 40, 26)
    # 取整朝「多留一点」的方向：0.391 × 100 = 39.1，必须给到 40 而不是 39 ——
    # 少给一列的话，用户拖出来的框看着就比框线窄一列。
    assert crop_box((0.0, 0.0, 0.391, 1.0), (100, 40))[2] == 40
    assert crop_box((0.0, 0.0, 0.39, 1.0), (100, 40))[2] == 39


def test_crop_box_never_leaves_an_empty_box() -> None:
    """再窄的框也得留下至少一个像素，否则 Pillow 会抛一个没头没脑的错。"""
    left, top, right, bottom = crop_box((0.5, 0.5, 0.5001, 0.5001), (40, 26))
    assert (right - left, bottom - top) == (1, 1)


def test_crop_size_floor() -> None:
    assert is_crop_big_enough((0.0, 0.0, 1.0, 1.0), (4000, 3000))
    # 最短边不到下限：手滑拖出来的一条。
    assert not is_crop_big_enough((0.0, 0.0, 0.01, 1.0), (4000, 3000))
    # 最短边够长（321 ≥ 320）但总像素不够（128 400 < 40 万）：一条细长。
    assert not is_crop_big_enough((0.0, 0.0, 0.1, 0.107), (4000, 3000))
    assert MIN_CROP_EDGE == 320


# ---- 读文件里存着的方向 ------------------------------------------------------------


def test_orientation_of_reads_the_exif_tag(tmp_path) -> None:
    path = tmp_path / "a.jpg"
    image = _make_image()
    exif = image.getexif()
    exif[274] = 6
    image.save(path, format="JPEG", exif=exif)
    with Image.open(path) as opened:
        assert orientation_of(opened) == 6


def test_orientation_of_reads_where_pillow_heif_hides_it(tmp_path) -> None:
    """HEIF 的方向不在 EXIF 里，而在 ``info["original_orientation"]``（ADR 0027 决策 5）。

    这条是**真的踩过的坑**：``pillow-heif`` 在解码时把 EXIF 的那个标签重置成 1，
    于是只认 EXIF 的实现会让所有竖拍 HEIC 躺着显示。
    """
    image = Image.new("RGB", (10, 5))
    image.info["original_orientation"] = 6
    assert orientation_of(image) == 6
    image.info["original_orientation"] = 99
    assert orientation_of(image) == 1


def test_orientation_of_prefers_what_pillow_heif_carried_over() -> None:
    """两者都在、值还不一样时，**以 info 里那份为准**。

    因为那个「不一样」正是 pillow-heif 造成的：它把 EXIF 标签重置成 1，把真值挪进
    ``info``。以标签为准的话，每一张竖拍 HEIC 都会被判成方向正常。
    """
    image = Image.new("RGB", (10, 5))
    image.info["original_orientation"] = 6
    exif = image.getexif()
    exif[274] = 1
    assert orientation_of(image) == 6


def test_orientation_of_falls_back_to_exif(tmp_path) -> None:
    """JPEG 没有那个 info 键，于是照旧走 EXIF —— 绝大多数照片走的是这一支。"""
    path = tmp_path / "b.jpg"
    image = _make_image()
    exif = image.getexif()
    exif[274] = 8
    image.save(path, format="JPEG", exif=exif)
    with Image.open(path) as opened:
        assert orientation_of(opened) == 8


def test_orientation_of_defaults_to_one() -> None:
    assert orientation_of(Image.new("RGB", (10, 5))) == 1
