"""Тесты персистентности состояния и операций с правилами."""

from __future__ import annotations

from ttbot.domains import WILDCARD
from ttbot.state import StateStore


def test_persistence_roundtrip(tmp_path):
    s = StateStore(tmp_path / "state.json")
    s.set_rule("a.com", "add", WILDCARD)
    s.set_rule("b.com", "block", WILDCARD)
    s.add_list_domains({"x.com", "y.com"})

    s2 = StateStore(tmp_path / "state.json")
    s2.load()
    assert len(s2.list_rules()) == 2
    assert s2.list_domains == {"x.com", "y.com"}


def test_set_rule_overwrite_moves_to_end(tmp_path):
    s = StateStore(tmp_path / "state.json")
    s.set_rule("a.com", "add", WILDCARD)
    s.set_rule("b.com", "add", WILDCARD)
    s.set_rule("a.com", "block", WILDCARD)  # перезапись → правило в конец
    assert [r.domain for r in s.list_rules()] == ["b.com", "a.com"]
    assert s.get_rule("a.com").action == "block"


def test_pop_rule_by_index(tmp_path):
    s = StateStore(tmp_path / "state.json")
    s.set_rule("a.com", "add", WILDCARD)
    s.set_rule("b.com", "add", WILDCARD)
    assert s.pop_rule(99) is None  # вне диапазона
    popped = s.pop_rule(1)
    assert popped is not None
    assert popped.domain == "a.com"
    assert len(s.list_rules()) == 1


def test_load_missing_file_is_empty(tmp_path):
    s = StateStore(tmp_path / "absent.json")
    s.load()
    assert s.list_rules() == []
    assert s.list_domains == set()


def test_list_domains_are_sticky(tmp_path):
    s = StateStore(tmp_path / "state.json")
    s.add_list_domains({"a.com"})
    s.add_list_domains({"b.com"})
    assert s.list_domains == {"a.com", "b.com"}


def test_spoof_ips_persistence(tmp_path):
    s = StateStore(tmp_path / "state.json")
    assert s.has_spoof_ips() is False
    s.set_spoof_ips(["10.0.0.5"], ["fd00::5"])
    assert s.has_spoof_ips() is True

    s2 = StateStore(tmp_path / "state.json")
    s2.load()
    assert s2.spoof_ipv4 == ["10.0.0.5"]
    assert s2.spoof_ipv6 == ["fd00::5"]
    assert s2.has_spoof_ips() is True
