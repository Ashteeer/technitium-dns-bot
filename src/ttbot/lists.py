"""Скачивание и разбор списков подмены (формат JSON).

Поддерживается только JSON следующего вида::

    {
      "version": 1,
      "rules": [
        { "domain_suffix": ["domain.com", "other.com"] }
      ]
    }

Каждый ``domain_suffix`` трактуется как wildcard (домен + все поддомены).
Дополнительно (необязательно) читается ключ ``domain`` — на случай, если
в списке встретятся точные домены; они также добавляются как wildcard.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import aiohttp

from .config import BlockList
from .domains import normalize_domain

log = logging.getLogger(__name__)

# github.com/<user>/<repo>/blob|raw/<branch>/<path>  ->  raw.githubusercontent.com/...
_GH_FILE_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$", re.IGNORECASE)


def normalize_list_url(url: str) -> str:
    """Привести обычную github.com-ссылку на файл к raw-виду.

    ``github.com/<u>/<r>/blob/<branch>/<path>`` (и ``/raw/``) →
    ``raw.githubusercontent.com/<u>/<r>/<branch>/<path>``. Остальные URL — без
    изменений. Так в config.yaml можно вставлять ссылку прямо из адресной строки.
    """
    url = url.strip()
    m = _GH_FILE_RE.match(url)
    if m:
        user, repo, rest = m.group(1), m.group(2), m.group(3)
        return f"https://raw.githubusercontent.com/{user}/{repo}/{rest}"
    return url


@dataclass
class ListStat:
    name: str
    count: int
    error: str | None = None


def extract_domains(data: object) -> set[str]:
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект верхнего уровня")
    version = data.get("version")
    if version is not None and version != 1:
        log.warning("Неизвестная версия списка: %r (продолжаем как v1).", version)

    out: set[str] = set()
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError("поле 'rules' должно быть массивом")
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        for key in ("domain_suffix", "domain"):
            values = rule.get(key) or []
            if isinstance(values, str):
                values = [values]
            for raw in values:
                nd = normalize_domain(raw)
                if nd:
                    out.add(nd)
    return out


async def fetch_one(session: aiohttp.ClientSession, url: str, timeout: int) -> set[str]:
    url = normalize_list_url(url)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        resp.raise_for_status()
        text = await resp.text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"невалидный JSON: {e}") from e
    return extract_domains(data)


async def fetch_all(
    session: aiohttp.ClientSession,
    blocklists: Iterable[BlockList],
    timeout: int = 60,
) -> tuple[set[str], list[ListStat]]:
    """Скачать и объединить все списки. Ошибка одного списка не валит остальные."""
    domains: set[str] = set()
    stats: list[ListStat] = []
    for bl in blocklists:
        try:
            doms = await fetch_one(session, bl.url, timeout)
            domains |= doms
            stats.append(ListStat(bl.name, len(doms)))
            log.info("Список %r: получено %d доменов.", bl.name, len(doms))
        except Exception as e:  # noqa: BLE001 — изолируем сбой одного источника
            stats.append(ListStat(bl.name, 0, str(e)))
            log.error("Не удалось загрузить список %r (%s): %s", bl.name, bl.url, e)
    return domains, stats
