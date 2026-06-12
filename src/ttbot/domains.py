"""Утилиты для нормализации доменных имён и разбора пользовательского ввода.

Правила wildcard / exact:
  * Из списков и при обычном вводе в Telegram домен трактуется как *wildcard*
    (подменяется сам домен и все поддомены).
  * Если перед доменом стоит символ ``@`` — это *exact*: подменяется
    только сам домен, без поддоменов.

IDN: домены с не-ASCII символами (например, ``сайт.рф``) автоматически
приводятся к punycode (``xn--80aswg.xn--p1ai``), т.к. DNS хранит именно его.
"""

from __future__ import annotations

import re

# Метка DNS: 1..63 символов [a-z0-9-], не начинается и не заканчивается дефисом.
_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_DOMAIN_RE = re.compile(rf"^(?:{_LABEL}\.)+{_LABEL}$")

WILDCARD = "wildcard"
EXACT = "exact"


def _to_punycode(domain: str) -> str | None:
    """Привести IDN-домен к ASCII-форме (punycode). ``None`` при ошибке."""
    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None


def normalize_domain(raw: str) -> str | None:
    """Привести домен к канонической форме или вернуть ``None``, если он невалиден.

    Отбрасываются ведущие ``*.`` и ``@``, регистр приводится к нижнему,
    удаляется завершающая точка. Unicode-домены кодируются в punycode.
    IP-адреса и однолейбловые имена отклоняются.
    """
    if not isinstance(raw, str):
        return None
    d = raw.strip().lower().rstrip(".")
    if d.startswith("@"):
        d = d[1:].strip()
    if d.startswith("*."):
        d = d[2:]
    if not d or "/" in d or " " in d:
        return None
    # IDN -> punycode (только если есть не-ASCII символы).
    if not d.isascii():
        ascii_form = _to_punycode(d)
        if ascii_form is None:
            return None
        d = ascii_form
    # Простейшая отбраковка IPv4/IPv6 — домены, состоящие только из цифр/двоеточий.
    if re.fullmatch(r"[0-9.]+", d) or ":" in d:
        return None
    if len(d) > 253 or "." not in d:
        return None
    if not _DOMAIN_RE.match(d):
        return None
    return d


def parse_user_input(raw: str) -> tuple[str, str] | None:
    """Разобрать ввод пользователя в ``(домен, scope)``.

    ``@example.com`` -> ``("example.com", "exact")``
    ``example.com``  -> ``("example.com", "wildcard")``
    Возвращает ``None`` при невалидном вводе.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    scope = EXACT if raw.startswith("@") else WILDCARD
    domain = normalize_domain(raw)
    if domain is None:
        return None
    return domain, scope


def describe_scope(scope: str) -> str:
    return "только домен" if scope == EXACT else "домен + поддомены"
