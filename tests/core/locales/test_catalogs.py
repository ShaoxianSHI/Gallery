"""文案目录的完整性：语言之间必须一一对应，占位符与单复数档数也要对得上。

**逐领域比较**（ADR 0106 决策 8）：迁移可以一个领域一个领域走，每个提交都是自洽的。
"""

from __future__ import annotations

import string
from collections.abc import Mapping

import pytest

from gallery.core.locales import CATALOGS, DEFAULT_LANGUAGE, en, zh_CN
from gallery.core.locales._catalog import DomainMessages, Message, merge

Catalog = Mapping[str, DomainMessages]


def _forms(message: Message) -> tuple[str, ...]:
    return (message,) if isinstance(message, str) else message


def _placeholders(message: Message) -> set[str]:
    return {
        field
        for text in _forms(message)
        for _, field, _, _ in string.Formatter().parse(text)
        if field is not None
    }


def catalog_problems(catalogs: Mapping[str, Catalog]) -> list[str]:
    """这份多语言目录有什么毛病；空列表就是齐全。"""
    source = catalogs[DEFAULT_LANGUAGE]
    problems: list[str] = []
    for code, domains in catalogs.items():
        if set(domains) != set(source):
            problems.append(
                f"{code} 的领域与 {DEFAULT_LANGUAGE} 不一致：{sorted(set(domains) ^ set(source))}"
            )
    for domain, messages in source.items():
        if not all(key.startswith(f"{domain}.") for key in messages):
            problems.append(f"{domain} 里有键不以领域名开头")
        for key, message in messages.items():
            if not all(text.strip() for text in _forms(message)):
                problems.append(f"{DEFAULT_LANGUAGE}:{key} 是空文案")
        for code, domains in catalogs.items():
            if domain not in domains:
                continue
            other = domains[domain]
            if set(other) != set(messages):
                missing = sorted(set(messages) - set(other))
                extra = sorted(set(other) - set(messages))
                problems.append(f"{code}:{domain} 缺 {missing}，多 {extra}")
            for key in sorted(set(other) & set(messages)):
                wanted = messages[key]
                got = other[key]
                if len(_forms(wanted)) != len(_forms(got)):
                    problems.append(f"{code}:{key} 的单复数档数与 {DEFAULT_LANGUAGE} 不一致")
                if _placeholders(wanted) != _placeholders(got):
                    problems.append(
                        f"{code}:{key} 的占位符不一致："
                        f"{sorted(_placeholders(wanted))} ≠ {sorted(_placeholders(got))}"
                    )
                if not all(text.strip() for text in _forms(got)):
                    problems.append(f"{code}:{key} 是空文案")
    return problems


def test_every_language_matches_the_source_language() -> None:
    problems = catalog_problems({code: _domains(code) for code in CATALOGS})
    assert not problems, "\n".join(problems)


def _domains(code: str) -> Catalog:
    module = {"zh_CN": zh_CN, "en": en}[code]
    return module.DOMAINS


def test_the_catalog_guard_would_notice_a_violation() -> None:
    base = {
        "banner": {"banner.hello": "你好，{name}。", "banner.count": ("{count} 张", "{count} 张")}
    }
    assert catalog_problems({"zh_CN": base, "en": base}) == []

    broken = {
        "zh_CN": base,
        "en": {"banner": {"banner.hello": "Hello.", "banner.extra": "x"}},
    }
    assert catalog_problems(broken)

    wrong_plural = {
        "zh_CN": base,
        "en": {"banner": {"banner.hello": "Hello, {name}.", "banner.count": "{count} photos"}},
    }
    assert catalog_problems(wrong_plural)

    empty = {
        "zh_CN": base,
        "en": {"banner": {"banner.hello": " ", "banner.count": ("{count}", "{count}")}},
    }
    assert catalog_problems(empty)


def test_merge_rejects_a_key_from_another_domain() -> None:
    with pytest.raises(ValueError, match="不属于领域"):
        merge({"banner": {"menu.open": "打开"}})
