"""查表本身：语言切换、回退、占位符与复数。

目录内容的完整性在 ``tests/core/locales/test_catalogs.py``；这里只盯 ``i18n`` 的行为。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from gallery.core import i18n
from gallery.core.i18n import (
    available_languages,
    current_language,
    resolve_language,
    set_language,
    t,
    t_plural,
)
from gallery.core.locales import DEFAULT_LANGUAGE

_FAKE = {
    "zh_CN": {
        "banner.greet": "你好，{name}。",
        "banner.photos": ("{count} 张照片", "{count} 张照片"),
        "banner.only_zh": "只有中文有这一条。",
    },
    "en": {
        "banner.greet": "Hello, {name}.",
        "banner.photos": ("{count} photo", "{count} photos"),
    },
}


@pytest.fixture(autouse=True)
def _restore_language() -> Iterator[None]:
    """语言是模块级的，用例改过之后必须放回去，否则同进程里后面的用例跟着变。"""
    before = current_language()
    yield
    set_language(before)


@pytest.fixture
def fake_catalogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "CATALOGS", _FAKE)


def test_default_language_is_the_source_language() -> None:
    assert DEFAULT_LANGUAGE == "zh_CN"
    assert available_languages()[0] == DEFAULT_LANGUAGE
    assert set(available_languages()) == {"zh_CN", "en"}


def test_unknown_language_stays_on_the_source_language() -> None:
    assert set_language("de") == DEFAULT_LANGUAGE
    assert current_language() == DEFAULT_LANGUAGE


def test_unknown_key_renders_the_key_itself() -> None:
    assert t("banner.nope") == "banner.nope"
    assert t_plural("banner.nope", 2) == "banner.nope"


def test_formatting_fills_params(fake_catalogs: None) -> None:
    assert t("banner.greet", name="京都") == "你好，京都。"
    set_language("en")
    assert t("banner.greet", name="Kyoto") == "Hello, Kyoto."


def test_missing_param_keeps_its_placeholder(fake_catalogs: None) -> None:
    assert t("banner.greet") == "你好，{name}。"


def test_plural_picks_a_form_by_count(fake_catalogs: None) -> None:
    set_language("en")
    assert t_plural("banner.photos", 1, name="x") == "1 photo"
    assert t_plural("banner.photos", 3) == "3 photos"


def test_missing_translation_falls_back_to_the_source_language(fake_catalogs: None) -> None:
    set_language("en")
    assert t("banner.only_zh") == "只有中文有这一条。"


def test_a_plain_string_serves_every_count(fake_catalogs: None) -> None:
    set_language("en")
    assert t_plural("banner.greet", 5, name="Kyoto") == "Hello, Kyoto."


@pytest.mark.parametrize(
    ("preference", "system", "expected"),
    [
        ("zh_CN", "en_US", "zh_CN"),
        ("en", "zh_CN", "en"),
        ("system", "zh-Hans-CN", "zh_CN"),
        ("system", "zh_CN", "zh_CN"),
        ("system", "en_GB", "en"),
        ("system", "ja_JP", "en"),
        ("de", "zh_CN", "zh_CN"),
    ],
)
def test_language_resolution(preference: str, system: str, expected: str) -> None:
    """显式选择优先于系统；``system`` 只看主语言标签，认不出的偏好回源语言。"""
    assert resolve_language(preference, system) == expected
