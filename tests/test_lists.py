"""Тесты разбора JSON-списков подмены."""

from __future__ import annotations

import pytest

from ttbot.lists import extract_domains, normalize_list_url


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://github.com/u/r/blob/main/list.json",
            "https://raw.githubusercontent.com/u/r/main/list.json",
        ),
        (
            "https://github.com/u/r/raw/main/list.json",
            "https://raw.githubusercontent.com/u/r/main/list.json",
        ),
        (
            "https://github.com/u/r/blob/dev/sub/dir/list.json",
            "https://raw.githubusercontent.com/u/r/dev/sub/dir/list.json",
        ),
        # уже raw — без изменений
        (
            "https://raw.githubusercontent.com/u/r/main/list.json",
            "https://raw.githubusercontent.com/u/r/main/list.json",
        ),
        # сторонний хост — без изменений
        ("https://example.com/lists/a.json", "https://example.com/lists/a.json"),
    ],
)
def test_normalize_list_url(url, expected):
    assert normalize_list_url(url) == expected


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
