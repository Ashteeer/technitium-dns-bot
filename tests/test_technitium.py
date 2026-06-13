"""Тесты разбора записей зоны Technitium (parse_zone_spoof)."""

from __future__ import annotations

from ttbot.technitium import parse_zone_spoof


def _a(name: str, ip: str = "10.0.0.53") -> dict:
    return {"name": name, "type": "A", "rData": {"ipAddress": ip}}


def test_parse_both_apex_and_wildcard():
    recs = [_a("x.com"), _a("*.x.com")]
    assert parse_zone_spoof(recs, "x.com") == (True, True, ["10.0.0.53"])


def test_parse_apex_only():
    assert parse_zone_spoof([_a("x.com")], "x.com") == (True, False, ["10.0.0.53"])


def test_parse_wildcard_only():
    assert parse_zone_spoof([_a("*.x.com")], "x.com") == (False, True, ["10.0.0.53"])


def test_parse_ignores_unrelated_records():
    recs = [
        {"name": "x.com", "type": "SOA", "rData": {}},
        _a("ns1.x.com", "1.2.3.4"),  # ни apex, ни wildcard
        _a("x.com", "10.0.0.9"),
    ]
    assert parse_zone_spoof(recs, "x.com") == (True, False, ["10.0.0.9"])


def test_parse_ipv6_and_dedup():
    recs = [
        _a("x.com", "10.0.0.1"),
        {"name": "x.com", "type": "AAAA", "rData": {"ipAddress": "fd00::1"}},
        _a("*.x.com", "10.0.0.1"),  # дубль IP — не повторяем
    ]
    has_apex, has_wild, ips = parse_zone_spoof(recs, "x.com")
    assert (has_apex, has_wild) == (True, True)
    assert ips == ["10.0.0.1", "fd00::1"]


def test_parse_value_fallback():
    recs = [{"name": "x.com", "type": "A", "rData": {"value": "10.0.0.7"}}]
    assert parse_zone_spoof(recs, "x.com") == (True, False, ["10.0.0.7"])
