"""Тесты нормализации доменов, IDN и разбора пользовательского ввода."""

from __future__ import annotations

import pytest

from ttbot.domains import EXACT, WILDCARD, normalize_domain, parse_user_input

# Известные punycode-формы для проверок IDN.
SITE_RF = "xn--80aswg.xn--p1ai"  # сайт.рф


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("*.example.com", "example.com"),
        ("@example.com", "example.com"),
        ("a.b.example.com", "a.b.example.com"),
    ],
)
def test_normalize_ok(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["1.2.3.4", "fe80::1", "localhost", "foo .com", "???", "", "com", "a/b.com"],
)
def test_normalize_rejected(raw):
    assert normalize_domain(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("сайт.рф", SITE_RF),
        ("Сайт.РФ", SITE_RF),  # регистр
        ("@сайт.рф", SITE_RF),  # @-префикс
        ("*.сайт.рф", SITE_RF),  # wildcard-префикс
        (SITE_RF, SITE_RF),  # уже punycode — без изменений
    ],
)
def test_normalize_idn(raw, expected):
    assert normalize_domain(raw) == expected


def test_normalize_idn_mixed_labels():
    # Смешанные ASCII/Unicode метки: ASCII-часть остаётся, Unicode кодируется.
    out = normalize_domain("mail.сайт.рф")
    assert out == "mail." + SITE_RF
    assert out.isascii()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", ("example.com", WILDCARD)),
        ("@example.com", ("example.com", EXACT)),
        (" @Example.Com ", ("example.com", EXACT)),
        ("@сайт.рф", (SITE_RF, EXACT)),
        ("сайт.рф", (SITE_RF, WILDCARD)),
    ],
)
def test_parse_user_input_ok(raw, expected):
    assert parse_user_input(raw) == expected


@pytest.mark.parametrize("raw", ["???", "localhost", ""])
def test_parse_user_input_invalid(raw):
    assert parse_user_input(raw) is None
