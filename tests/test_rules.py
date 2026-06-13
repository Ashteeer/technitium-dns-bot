"""Тесты генерации файла правил (для внешнего Technitium App)."""

from __future__ import annotations

import yaml

from ttbot.rules import build_rules, write_rules


def _desired_from(mapping):
    def desired(domain):
        return mapping.get(domain, (False, False))

    return desired


def test_build_rules_includes_only_spoofed():
    mapping = {
        "a.com": (True, True),  # домен и поддомены
        "b.com": (False, True),  # только поддомены (apex форвардится)
        "c.com": (True, False),  # только домен
        "d.com": (False, False),  # ничего — не должно попасть
    }
    data = build_rules(
        ["d.com", "a.com", "c.com", "b.com"], _desired_from(mapping), ["1.2.3.4"], ["fd00::1"]
    )
    assert data["spoof_ipv4"] == ["1.2.3.4"]
    assert data["spoof_ipv6"] == ["fd00::1"]
    got = {r["domain"]: (r["apex"], r["subdomains"]) for r in data["rules"]}
    assert got == {
        "a.com": (True, True),
        "b.com": (False, True),
        "c.com": (True, False),
    }
    # отсортировано по домену
    assert [r["domain"] for r in data["rules"]] == ["a.com", "b.com", "c.com"]


def test_write_rules_atomic_and_readable(tmp_path):
    path = tmp_path / "rules.yaml"
    data = {
        "spoof_ipv4": ["1.2.3.4"],
        "spoof_ipv6": [],
        "rules": [{"domain": "a.com", "apex": True, "subdomains": True}],
    }
    write_rules(path, data)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#")  # шапка-комментарий
    loaded = yaml.safe_load(text)
    assert loaded["rules"][0] == {"domain": "a.com", "apex": True, "subdomains": True}
