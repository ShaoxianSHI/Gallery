"""``gallery.core.images`` 的测试：解码、缩放与 EXIF 方向。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.errors import ImageDecodeError
from gallery.core.images import HEIF_SUPPORT, decode_rgba


def test_decode_returns_rgba_bytes(make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(64, 48))

    raster = decode_rgba(path)

    assert raster.size == (64, 48)
    assert raster.nbytes == 64 * 48 * 4
    assert raster.has_alpha is False


def test_max_edge_scales_down_keeping_aspect(make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(400, 200))

    raster = decode_rgba(path, max_edge=100)

    assert raster.size == (100, 50)


def test_max_edge_never_upscales(make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(40, 20))

    raster = decode_rgba(path, max_edge=512)

    assert raster.size == (40, 20)


def test_max_edge_applies_to_portrait(make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg", size=(200, 400))

    raster = decode_rgba(path, max_edge=100)

    assert raster.size == (50, 100)


def test_draft_decode_matches_plain_scaling(make_photo: Callable[..., Path]) -> None:
    """JPEG 走 DCT 缩放快速通道后，尺寸仍必须精确等于目标。"""
    path = make_photo("a.jpg", size=(1024, 768))

    quick = decode_rgba(path, max_edge=64, use_draft=True)
    slow = decode_rgba(path, max_edge=64, use_draft=False)

    assert quick.size == (64, 48)
    assert slow.size == (64, 48)


def test_exif_orientation_is_applied(make_photo: Callable[..., Path]) -> None:
    """竖拍照片（Orientation=6）必须被摆正，否则界面里全是躺着的。"""
    path = make_photo("rotated.jpg", size=(40, 20), orientation=6)

    raster = decode_rgba(path)

    assert raster.size == (20, 40)


def test_png_alpha_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    image.save(path)
    image.close()

    assert decode_rgba(path).has_alpha is True


def test_unidentified_content_reports_name(tmp_path: Path) -> None:
    path = tmp_path / "假照片.jpg"
    path.write_text("这不是图片", encoding="utf-8")

    with pytest.raises(ImageDecodeError) as excinfo:
        decode_rgba(path)

    assert "假照片.jpg" in excinfo.value.message
    assert excinfo.value.path == path


def test_truncated_file_is_reported(make_photo: Callable[..., Path]) -> None:
    path = make_photo("truncated.jpg", size=(128, 128))
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])

    with pytest.raises(ImageDecodeError) as excinfo:
        decode_rgba(path)

    assert "损坏" in excinfo.value.message


def test_heic_decodes_when_supported(tmp_path: Path) -> None:
    if not HEIF_SUPPORT:  # pragma: no cover - 依赖缺失时跳过
        pytest.skip("pillow-heif 不可用")
    path = tmp_path / "photo.heic"
    image = Image.new("RGB", (32, 24), (10, 20, 30))
    try:
        image.save(path, format="HEIF")
    except (OSError, ValueError):  # pragma: no cover - 编码器不可用时跳过
        pytest.skip("当前环境无法写出 HEIC 测试文件")
    finally:
        image.close()

    raster = decode_rgba(path)

    assert raster.size == (32, 24)
