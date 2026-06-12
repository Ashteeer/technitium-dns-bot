"""Тесты разбора JSON-списков подмены."""

from __future__ import annotations

import pytest

from ttbot.lists import extract_domains


def test_extract_domains():
    sample = {
        "version": 1,
        "rules": [
            {"domain_suffix": ["A.com", "b.com", "b.com"]},  # дубли/регистр
            {"domain_suffix": ["c.org"], "domain": ["d.net"]},
            {"domain_suffix": "single.io"},  # строка вместо списка
        ],
    }
    assert extract_domains(sample) == {"a.com", "b.com", "c.org", "d.net", "single.io"}


def test_extract_domains_idn():
    data = {"version": 1, "rules": [{"domain_suffix": ["сайт.рф"]}]}
    assert extract_domains(data) == {"xn--80aswg.xn--p1ai"}


def test_extract_domains_skips_invalid():
    data = {"rules": [{"domain_suffix": ["ok.com", "1.2.3.4", "localhost", "bad/x"]}]}
    assert extract_domains(data) == {"ok.com"}


@pytest.mark.parametrize("bad", [[], "nope", 42, {"rules": "x"}, {"version": 1}])
def test_extract_domains_bad_shape(bad):
    with pytest.raises(ValueError):
        extract_domains(bad)
