"""``gallery.core.media.decode`` 的测试：解码、缩放与 EXIF 方向。"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.errors import ImageDecodeError
from gallery.core.media.decode import HEIF_SUPPORT, decode_rgba
from gallery.core.media.edit import Crop, Rotate


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


def test_orientation_hidden_in_heif_metadata_is_applied(tmp_path: Path) -> None:
    """**HEIF 的方向不在 EXIF 标签里**，藏在 ``info["original_orientation"]``。

    ``pillow-heif`` 在解码时把 EXIF 里那个标签重置成 1，另存一份到 ``info``。
    因此只认 EXIF 的实现（原先走 ``ImageOps.exif_transpose`` 就是那样）会让**所有
    竖拍 HEIC 都躺着** —— 而 HEIC 正是 iPhone 照片的默认格式。这条用「真写一个
    Orientation 标签进去」来复现：pillow-heif 会照它自己的规矩处理掉那个标签。
    """
    if not HEIF_SUPPORT:
        pytest.skip("pillow-heif 不可用")
    path = tmp_path / "竖拍.heic"
    image = Image.new("RGB", (40, 20), (200, 120, 40))
    exif = image.getexif()
    exif[274] = 6
    try:
        image.save(path, format="HEIF", exif=exif)
    except (OSError, ValueError, KeyError):  # pragma: no cover - 编码器缺失时的降级
        pytest.skip("这个 Pillow 存不了 HEIF")
    finally:
        image.close()
    with Image.open(path) as opened:
        assert opened.getexif().get(274, 1) == 1, "前提变了：标签没被 pillow-heif 挪走"
        assert opened.info.get("original_orientation") == 6

    assert decode_rgba(path).size == (20, 40)


def test_edit_steps_are_applied_after_uprighting(make_photo: Callable[..., Path]) -> None:
    """草稿叠加在**摆正之后** —— 用户拖的框、看的图，都是摆正后那张（ADR 0027 决策 6）。"""
    path = make_photo("rotated.jpg", size=(40, 20), orientation=6)

    turned = decode_rgba(path)
    turned_back = decode_rgba(path, steps=(Rotate(90),))

    assert turned.size == (20, 40)
    assert turned_back.size == (40, 20)


def test_edit_steps_happen_before_the_downscale(make_photo: Callable[..., Path]) -> None:
    """先裁后缩：草稿里的框是归一化坐标，所以裁在哪一档分辨率上都得到同一张图。"""
    path = make_photo("a.jpg", size=(400, 200))

    full = decode_rgba(path, steps=(Crop((0.0, 0.0, 0.5, 1.0)),))
    scaled = decode_rgba(path, max_edge=100, steps=(Crop((0.0, 0.0, 0.5, 1.0)),))

    assert full.size == (200, 200)
    # draft 把 400×200 降到 1/2（200×100）再裁成 100×100：裁的是左边那一半，
    # 于是不管在哪个分辨率上裁，左右都是对半的。
    assert scaled.size == (100, 100)


def test_png_alpha_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    image.save(path)
    image.close()

    assert decode_rgba(path).has_alpha is True


def test_palette_png_transparency_is_reported_too(tmp_path: Path) -> None:
    """**调色板**（模式 ``P``）的 PNG 把透明记在 ``info["transparency"]`` 里。

    只看 ``getbands()`` 里有没有 ``A`` 的话，这种图会被判成不透明 ——
    ``convert("RGBA")`` 明明给出了 alpha 通道，我们却说它没有，于是缩略图按 JPEG
    存盘，透明像素**写进缓存之后**再也回不来了（不是显示问题）。
    """
    path = tmp_path / "调色板透明.png"
    image = Image.new("P", (16, 16))
    image.putpalette([0, 0, 0] * 256)
    image.save(path, format="PNG", transparency=0)
    image.close()

    raster = decode_rgba(path)

    assert raster.has_alpha is True
    assert raster.rgba[:4] == bytes((0, 0, 0, 0)), "透明像素必须原样带出来"


def test_unidentified_content_reports_name(tmp_path: Path) -> None:
    path = tmp_path / "假照片.jpg"
    path.write_text("这不是图片", encoding="utf-8")

    with pytest.raises(ImageDecodeError) as excinfo:
        decode_rgba(path)

    assert "假照片.jpg" in excinfo.value.message
    assert excinfo.value.path == path


def test_a_missing_file_is_not_reported_as_corrupt(tmp_path: Path) -> None:
    """「文件不在了」与「文件坏了」必须说成两句话。

    混成一句会吓人：用户刚把照片移走或删掉时，一次迟到的解码失败若报成「可能已损坏」，
    一次成功的移动就被说成了数据损坏，用户很可能去抢救根本没坏的文件。
    """
    gone = tmp_path / "已经移走.jpg"

    with pytest.raises(ImageDecodeError) as excinfo:
        decode_rgba(gone)

    assert "已经移走.jpg" in excinfo.value.message
    assert "不在原处" in excinfo.value.message
    assert "损坏" not in excinfo.value.message


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


def _run_fresh_python(script: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    """在全新解释器里跑一段脚本：Pillow 的初始化状态必须从头观察才准。"""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        capture_output=True,
        timeout=180,
        check=False,
    )


def test_pillow_is_initialized_at_import_time() -> None:
    """导入 core.media.decode 时就要把 Pillow 初始化完。

    ``Image.open`` / ``Image.save`` 会惰性导入解码器，而那个「初始化过没有」的全局
    标志不是线程安全的：多个解码线程同时第一次用到 Pillow 时实测会**段错误**
    （不是抛异常，是整个进程挂掉）。这里在全新进程里检查那个标志，因此不受
    测试会话里先前已经用过 Pillow 的影响。
    """
    completed = _run_fresh_python(
        """
        import gallery.core.media.decode  # noqa: F401 - 导入本身就是要验证的行为
        from PIL import Image

        print("initialized =", getattr(Image, "_initialized", 0))
        raise SystemExit(0 if getattr(Image, "_initialized", 0) >= 1 else 1)
        """
    )

    assert completed.returncode == 0, (
        "导入 core.media.decode 之后 Pillow 仍未初始化，多线程首次使用时可能段错误："
        f"{completed.stdout.decode()}{completed.stderr.decode()}"
    )


def test_concurrent_first_use_of_pillow_does_not_crash(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """八个线程同时第一次用 Pillow 不能崩。

    段错误会带走整个进程，所以放到子进程里跑：真崩了也只是退出码非零，测试会话
    本身不受影响。图片由父进程先造好 —— 子进程里 Pillow 的第一次使用就发生在
    并发线程里，这正是会崩的那个时刻。线程用屏障对齐起跑。
    """
    library_root = tmp_path / "库"
    for index in range(16):
        make_photo(f"IMG_{index:04d}.jpg", directory=library_root)

    completed = _run_fresh_python(
        """
        import sys, threading
        from gallery.core.library.facade import Library

        library = Library.open(sys.argv[1])
        refs = list(library.iter_photos())
        barrier = threading.Barrier(8)
        failures = []

        def work(chunk):
            barrier.wait()
            try:
                for ref in chunk:
                    library.thumbnail(ref)
            except BaseException as exc:  # noqa: BLE001 - 子进程里如实回报
                failures.append(repr(exc))

        threads = [threading.Thread(target=work, args=(refs[i::8],)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        print("FAILURES" if failures else "OK")
        sys.exit(1 if failures else 0)
        """,
        str(library_root),
    )

    assert completed.returncode == 0, (
        f"并发缩略图崩了（退出码 {completed.returncode}）："
        f"{completed.stderr.decode('utf-8', 'replace')[-1500:]}"
    )
    assert b"OK" in completed.stdout
