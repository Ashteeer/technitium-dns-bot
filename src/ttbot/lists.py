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
from collections.abc import Iterable
from dataclasses import dataclass

import aiohttp

from .config import BlockList
from .domains import normalize_domain

log = logging.getLogger(__name__)


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
