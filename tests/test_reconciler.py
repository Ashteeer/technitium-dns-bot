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


# -------------------------------------------------------------- check_domain
def test_check_apex_in_list(rec):
    rec.state.list_domains = {"google.com"}
    proxied, _ = rec.check_domain("google.com")
    assert proxied is True


def test_check_subdomain_covered_by_list_wildcard(rec):
    rec.state.list_domains = {"google.com"}
    proxied, _ = rec.check_domain("test.google.com")
    assert proxied is True


def test_check_apex_blocked_exact_but_subdomain_stays(rec):
    rec.state.list_domains = {"google.com"}
    rec.state.set_rule("google.com", "block", EXACT)
    assert rec.check_domain("google.com")[0] is False  # apex заблокирован
    assert rec.check_domain("mail.google.com")[0] is True  # wildcard из списка


def test_check_exact_add_does_not_cover_subdomains(rec):
    rec.state.set_rule("exact-only.com", "add", EXACT)
    assert rec.check_domain("exact-only.com")[0] is True
    assert rec.check_domain("sub.exact-only.com")[0] is False


def test_check_block_wildcard_blocks_everything(rec):
    rec.state.list_domains = {"google.com"}
    rec.state.set_rule("google.com", "block", WILDCARD)
    assert rec.check_domain("google.com")[0] is False
    assert rec.check_domain("x.google.com")[0] is False


def test_check_unknown_domain(rec):
    proxied, reason = rec.check_domain("unrelated.org")
    assert proxied is False
    assert reason


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
