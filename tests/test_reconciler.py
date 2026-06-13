"""Тесты логики приоритетов (_desired), check_domain и смены IP подмены."""

from __future__ import annotations

import asyncio

from ttbot.domains import EXACT, WILDCARD
from ttbot.reconciler import Reconciler


# ----------------------------------------------------------------- _desired
def test_desired_from_list(rec):
    rec.state.list_domains = {"fromlist.com"}
    assert rec._desired("fromlist.com") == (True, True)


def test_desired_unknown(rec):
    assert rec._desired("nope.com") == (False, False)


def test_desired_user_add_wildcard(rec):
    rec.state.set_rule("uadd.com", "add", WILDCARD)
    assert rec._desired("uadd.com") == (True, True)


def test_desired_user_add_exact_overrides_list(rec):
    rec.state.list_domains = {"uexact.com"}
    rec.state.set_rule("uexact.com", "add", EXACT)
    assert rec._desired("uexact.com") == (True, False)


def test_desired_user_block_wildcard_kills_list(rec):
    rec.state.list_domains = {"fromlist.com"}
    rec.state.set_rule("fromlist.com", "block", WILDCARD)
    assert rec._desired("fromlist.com") == (False, False)


def test_desired_user_block_exact_keeps_list_wildcard(rec):
    rec.state.list_domains = {"bexact.com"}
    rec.state.set_rule("bexact.com", "block", EXACT)
    assert rec._desired("bexact.com") == (False, True)


def test_desired_user_block_exact_not_in_list(rec):
    rec.state.set_rule("blonely.com", "block", EXACT)
    assert rec._desired("blonely.com") == (False, False)


# --------------------------------------------------- check_domain (через API)
def _a(name: str, ip: str = "10.0.0.53") -> dict:
    return {"name": name, "type": "A", "rData": {"ipAddress": ip}}


class _FakeZonesClient:
    """Клиент, отдающий заранее заданные зоны и их записи (без сети)."""

    def __init__(self, zones: dict[str, list[dict]]):
        self._zones = zones

    async def list_zones(self):
        return set(self._zones)

    async def get_records(self, zone: str):
        return self._zones.get(zone, [])


def _check(zones: dict, query: str):
    rec = Reconciler(cfg=None, client=_FakeZonesClient(zones), state=None)
    return asyncio.run(rec.check_domain(query))


def test_check_parent_shows_proxied_subdomain():
    # google.com не проксируется, но test.google.com — да (wildcard).
    zones = {"test.google.com": [_a("test.google.com"), _a("*.test.google.com")]}
    rep = _check(zones, "google.com")
    assert rep.proxied is False
    assert [(h.pattern, h.ips) for h in rep.subdomains] == [("*.test.google.com", ["10.0.0.53"])]


def test_check_query_apex_and_wildcard():
    zones = {"test.google.com": [_a("test.google.com"), _a("*.test.google.com")]}
    rep = _check(zones, "test.google.com")
    assert rep.proxied is True
    assert rep.ips == ["10.0.0.53"]
    assert rep.reason == "домен и поддомены"


def test_check_covered_by_parent_wildcard():
    zones = {"test.google.com": [_a("test.google.com"), _a("*.test.google.com")]}
    rep = _check(zones, "mail.test.google.com")
    assert rep.proxied is True
    assert "wildcard" in rep.reason


def test_check_exact_only_zone():
    zones = {"x.com": [_a("x.com")]}  # только apex
    rep_apex = _check(zones, "x.com")
    assert rep_apex.proxied is True
    assert rep_apex.reason == "только домен"
    rep_sub = _check(zones, "sub.x.com")  # exact не покрывает поддомены
    assert rep_sub.proxied is False
    assert rep_sub.subdomains == []


def test_check_wildcard_only_zone():
    zones = {"y.com": [_a("*.y.com")]}  # только wildcard, без apex
    rep_apex = _check(zones, "y.com")
    assert rep_apex.proxied is False  # сам y.com не покрыт *.y.com
    assert any(h.pattern == "*.y.com" for h in rep_apex.subdomains)
    assert _check(zones, "a.y.com").proxied is True


def test_check_nothing_found():
    rep = _check({}, "foo.bar")
    assert rep.proxied is False
    assert rep.subdomains == []


# -------------------------------------------------------- смена IP подмены
class _FakeClient:
    """Минимальный клиент: записывает вызовы set_spoof вместо HTTP."""

    def __init__(self):
        self.calls = []

    async def set_spoof(self, base, apex, wildcard, ipv4, ipv6):
        self.calls.append((base, apex, wildcard, tuple(ipv4), tuple(ipv6)))


def test_managed_domains_union_dedup(rec):
    rec.state.list_domains = {"a.com", "b.com"}
    rec.state.set_rule("b.com", "block", WILDCARD)  # пересечение со списком
    rec.state.set_rule("c.com", "add", WILDCARD)
    assert rec.managed_domains() == ["a.com", "b.com", "c.com"]


def test_change_spoof_ips_reapplies_all_zones(state):
    client = _FakeClient()
    rec = Reconciler(cfg=None, client=client, state=state)
    state.list_domains = {"a.com"}
    state.set_rule("b.com", "add", WILDCARD)
    state.set_spoof_ips(["10.0.0.1"], [])

    applied, failed = asyncio.run(rec.change_spoof_ips(["10.0.0.2"], ["fd00::2"]))

    assert (applied, failed) == (2, 0)
    assert state.spoof_ipv4 == ["10.0.0.2"]
    assert state.spoof_ipv6 == ["fd00::2"]
    # Обе зоны пересозданы именно с новым IP.
    assert {c[0] for c in client.calls} == {"a.com", "b.com"}
    for _base, _apex, _wildcard, v4, v6 in client.calls:
        assert v4 == ("10.0.0.2",)
        assert v6 == ("fd00::2",)
