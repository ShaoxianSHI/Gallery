"""``gallery.core.metadata.download.image_suffix``：封面后缀按字节认（ADR 0082 决策 6）。

这一条盯的是一个**不会报错**的毛病：源站混着给 PNG 与 JPEG，而有的地址连后缀都没有 ——
按地址猜后缀会把一张 WebP 写成 ``.jpg``，之后那张封面解不出来，网格上只剩一个灰块。
"""

from __future__ import annotations

import pytest

from gallery.core.metadata.download import image_suffix

#: 每种格式最前面的那几个字节（够认了，不必是真图）。
SAMPLES = {
    ".jpg": bytes.fromhex("ffd8ffe000104a464946"),
    ".png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
    ".gif": b"GIF89a" + b"\x00" * 8,
    ".webp": b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 ",
    ".avif": b"\x00\x00\x00\x1cftypavif" + b"\x00" * 8,
}


@pytest.mark.parametrize(("suffix", "data"), SAMPLES.items())
def test_each_format_is_recognized_by_its_bytes(suffix: str, data: bytes) -> None:
    assert image_suffix(data) == suffix


@pytest.mark.parametrize("data", [b"", b"cover", b"\x00\x00\x00\x00"])
def test_something_we_cannot_read_is_none(data: bytes) -> None:
    """认不出就返回 ``None``，由调用方退回地址里写的那个后缀 —— 不猜。"""
    assert image_suffix(data) is None
