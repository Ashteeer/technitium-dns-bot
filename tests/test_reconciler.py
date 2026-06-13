"""Тесты логики приоритетов (_desired), check_domain и смены IP подмены."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
    assert len(rep.subdomains) == 1
    hit = rep.subdomains[0]
    assert hit.domain == "test.google.com"
    assert (hit.apex, hit.wildcard) == (True, True)
    assert hit.ips == ["10.0.0.53"]


def test_check_hyphenated_subdomain_zone():
    # Реальный кейс: проксируется robinfrontend-pa.googleapis.com,
    # проверка googleapis.com должна показать его в поддоменах.
    zone = "robinfrontend-pa.googleapis.com"
    zones = {zone: [_a(zone), _a(f"*.{zone}")]}
    rep = _check(zones, "googleapis.com")
    assert rep.proxied is False
    assert [h.domain for h in rep.subdomains] == [zone]
    assert (rep.subdomains[0].apex, rep.subdomains[0].wildcard) == (True, True)
    # сам зонный домен проксируется и покрыт wildcard'ом для своих поддоменов
    assert _check(zones, zone).proxied is True
    assert _check(zones, f"x.{zone}").proxied is True


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
    assert any(h.domain == "y.com" and h.wildcard and not h.apex for h in rep_apex.subdomains)
    assert _check(zones, "a.y.com").proxied is True


def test_check_nothing_found():
    rep = _check({}, "foo.bar")
    assert rep.proxied is False
    assert rep.subdomains == []


# -------------------------------------------------------- смена IP подмены
class _FakeClient:
    """Минимальный клиент: записывает вызовы set_spoof / delete_zone вместо HTTP."""

    def __init__(self):
        self.calls = []  # set_spoof
        self.deleted = []  # delete_zone

    async def set_spoof(self, base, apex, wildcard, ipv4, ipv6):
        self.calls.append((base, apex, wildcard, tuple(ipv4), tuple(ipv6)))

    async def delete_zone(self, zone):
        self.deleted.append(zone)


def _cfg(tmp_path):
    return SimpleNamespace(
        rules_file=tmp_path / "rules.yaml",
        spoof_ipv4=["9.9.9.9"],
        spoof_ipv6=[],
        blocklists=[],
        list_fetch_timeout=60,
    )


def test_managed_domains_union_dedup(rec):
    rec.state.list_domains = {"a.com", "b.com"}
    rec.state.set_rule("b.com", "block", WILDCARD)  # пересечение со списком
    rec.state.set_rule("c.com", "add", WILDCARD)
    assert rec.managed_domains() == ["a.com", "b.com", "c.com"]


def test_change_spoof_ips_reapplies_all_zones(tmp_path, state):
    client = _FakeClient()
    rec = Reconciler(cfg=_cfg(tmp_path), client=client, state=state)
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


def test_flush_deletes_zones_and_resets(tmp_path, state):
    client = _FakeClient()
    rec = Reconciler(cfg=_cfg(tmp_path), client=client, state=state)
    state.list_domains = {"a.com", "b.com"}
    state.set_rule("c.com", "add", WILDCARD)
    state.set_spoof_ips(["1.1.1.1"], [])

    n = asyncio.run(rec.flush())

    assert n == 3
    assert set(client.deleted) == {"a.com", "b.com", "c.com"}
    assert state.list_rules() == []
    assert state.list_domains == set()
    assert state.spoof_ipv4 == ["9.9.9.9"]  # пере-сидировано из конфига
    assert (tmp_path / "rules.yaml").exists()


def test_reload_reapplies_all_and_writes_rules(tmp_path, state):
    client = _FakeClient()
    rec = Reconciler(cfg=_cfg(tmp_path), client=client, state=state)
    state.list_domains = {"a.com"}
    state.set_rule("b.com", "add", WILDCARD)
    state.set_spoof_ips(["1.1.1.1"], [])

    res = asyncio.run(rec.reload(session=None))  # blocklists=[] → сеть не нужна

    assert res.new_domains == 0
    assert res.applied == 2
    assert {c[0] for c in client.calls} == {"a.com", "b.com"}
    assert (tmp_path / "rules.yaml").exists()
