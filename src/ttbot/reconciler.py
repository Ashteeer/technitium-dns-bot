"""Согласование (reconcile) желаемого состояния с сервером Technitium.

Для каждого базового домена вычисляется пара флагов ``(apex, wildcard)`` —
подменять ли сам домен и его поддомены. Логика приоритетов:

* Базовое значение берётся из интернет-списков: если домен есть в
  ``list_domains`` — ``apex=True, wildcard=True`` (списки всегда wildcard).
* Пользовательское правило (если есть) ПЕРЕОПРЕДЕЛЯЕТ список:
    - add / wildcard  -> apex=True,  wildcard=True
    - add / exact (@) -> apex=True,  wildcard=False   (только сам домен)
    - block / wildcard-> apex=False, wildcard=False   (никогда не подменять)
    - block / exact(@)-> apex=False, wildcard=<из списка>  (исключить только сам домен)

Замечание о конкурентности: операции выполняются в одном событийном цикле
asyncio. Чтение/изменение состояния — синхронные операции (атомарны между
await'ами), поэтому отдельный мьютекс не требуется; одновременный HTTP к
Technitium ограничен семафором внутри клиента.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

import aiohttp

from .config import Config
from .lists import ListStat, fetch_all
from .state import StateStore, UserRule
from .technitium import TechnitiumClient, TechnitiumError

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    new_domains: int
    applied: int
    failed: int
    list_stats: list[ListStat]


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Reconciler:
    def __init__(self, cfg: Config, client: TechnitiumClient, state: StateStore):
        self.cfg = cfg
        self.client = client
        self.state = state

    # -------------------------------------------------------- вычисление цели
    def _desired(self, domain: str) -> tuple[bool, bool]:
        in_list = domain in self.state.list_domains
        apex = in_list
        wildcard = in_list
        rule = self.state.get_rule(domain)
        if rule is not None:
            if rule.action == "add":
                apex = True
                wildcard = rule.scope == "wildcard"
            else:  # block
                if rule.scope == "wildcard":
                    apex = False
                    wildcard = False
                else:  # exact block — исключаем только сам домен
                    apex = False
                    # wildcard остаётся равным значению из списка
        return apex, wildcard

    async def apply_domain(self, domain: str) -> None:
        apex, wildcard = self._desired(domain)
        await self.client.set_spoof(
            domain, apex, wildcard, self.state.spoof_ipv4, self.state.spoof_ipv6
        )

    async def _safe_apply(self, domain: str) -> bool:
        try:
            await self.apply_domain(domain)
            return True
        except TechnitiumError as e:
            log.warning("Не удалось применить домен %s: %s", domain, e)
            return False

    # ---------------------------------------------------- периодический sync
    async def sync_lists(self, session: aiohttp.ClientSession) -> SyncResult:
        """Скачать списки и применить ТОЛЬКО новые домены.

        Удалённые из источника домены не трогаем (они остаются в Technitium).
        Заблокированные пользователем домены при применении дадут пустую/
        частичную зону согласно ``_desired`` — то есть подменяться не будут.
        """
        incoming, stats = await fetch_all(session, self.cfg.blocklists, self.cfg.list_fetch_timeout)
        new = sorted(incoming - self.state.list_domains)
        if new:
            self.state.add_list_domains(set(new))

        applied = failed = 0
        for batch in _chunks(new, 100):
            results = await asyncio.gather(*(self._safe_apply(d) for d in batch))
            applied += sum(1 for r in results if r)
            failed += sum(1 for r in results if not r)

        log.info(
            "Sync завершён: новых доменов=%d, применено=%d, ошибок=%d.",
            len(new),
            applied,
            failed,
        )
        return SyncResult(len(new), applied, failed, stats)

    # ---------------------------------------------------- действия из бота
    async def user_add(self, domain: str, scope: str) -> None:
        """Пользователь добавляет домен (он будет подменяться)."""
        self.state.set_rule(domain, "add", scope)
        await self.apply_domain(domain)

    async def user_block(self, domain: str, scope: str) -> None:
        """Пользователь удаляет домен (он больше не будет подменяться)."""
        self.state.set_rule(domain, "block", scope)
        await self.apply_domain(domain)

    async def remove_rule(self, index_1based: int) -> UserRule | None:
        """Убрать пользовательское правило по номеру.

        После удаления домен возвращается к поведению по умолчанию: если он
        присутствует в списках — снова начинает подменяться (wildcard); если
        нет — зона удаляется.
        """
        rule = self.state.pop_rule(index_1based)
        if rule is None:
            return None
        await self.apply_domain(rule.domain)
        return rule

    # ---------------------------------------------------- смена IP подмены
    def managed_domains(self) -> list[str]:
        """Все домены, которыми управляет бот (имеют или могут иметь зону).

        Это объединение доменов из списков и доменов с пользовательскими
        правилами. Применение каждого через ``apply_domain`` идемпотентно:
        add/списки пересоздают зону, block — удаляют её.
        """
        return sorted(self.state.list_domains | set(self.state.user_rules.keys()))

    async def change_spoof_ips(self, ipv4: list[str], ipv6: list[str]) -> tuple[int, int]:
        """Сменить IP подмены и пересоздать ВСЕ управляемые зоны с новым IP.

        Новый набор IP полностью заменяет старый и сохраняется в состоянии
        (становится источником истины). Возвращает ``(applied, failed)``.
        """
        self.state.set_spoof_ips(ipv4, ipv6)
        domains = self.managed_domains()
        applied = failed = 0
        for batch in _chunks(domains, 100):
            results = await asyncio.gather(*(self._safe_apply(d) for d in batch))
            applied += sum(1 for r in results if r)
            failed += sum(1 for r in results if not r)
        log.info(
            "Смена IP подмены на %s: пересоздано=%d, ошибок=%d (всего доменов=%d).",
            ", ".join(ipv4 + ipv6),
            applied,
            failed,
            len(domains),
        )
        return applied, failed

    # ---------------------------------------------------- проверка домена
    def check_domain(self, query: str) -> tuple[bool, str]:
        """Проверить, будет ли домен (или поддомен) подменяться.

        Возвращает ``(is_proxied, reason)`` — флаг и человекочитаемая причина.

        Алгоритм:
        1. Проверяем сам ``query`` через ``_desired``.
        2. Если не найдено — поднимаемся по цепочке родителей и ищем
           wildcard-покрытие (например, ``*.google.com`` покрывает
           ``test.google.com``). Останавливаемся перед TLD.
        """
        parts = query.split(".")

        # 1. Точный домен (apex-запись)
        apex, _ = self._desired(query)
        if apex:
            rule = self.state.get_rule(query)
            if rule and rule.action == "add":
                if rule.scope == "wildcard":
                    reason = f"пользовательское правило (wildcard) для `{query}`"
                else:
                    reason = f"пользовательское правило (точное) для `{query}`"
            else:
                reason = f"домен `{query}` есть в списках"
            return True, reason

        # Явная блокировка самого домена
        rule = self.state.get_rule(query)
        if rule and rule.action == "block":
            return False, f"заблокирован пользовательским правилом для `{query}`"

        # 2. Поднимаемся по цепочке родителей, ищем wildcard-покрытие.
        # range(1, N-1): для test.google.com → проверим google.com, но не com (TLD).
        for i in range(1, len(parts) - 1):
            parent = ".".join(parts[i:])
            _, wildcard = self._desired(parent)
            if wildcard:
                prule = self.state.get_rule(parent)
                if prule and prule.action == "add":
                    reason = f"пользовательское wildcard-правило `*.{parent}`"
                else:
                    reason = f"домен `{parent}` есть в списках (wildcard `*.{parent}`)"
                return True, reason
            # Явная wildcard-блокировка на родителе
            prule = self.state.get_rule(parent)
            if prule and prule.action == "block" and prule.scope == "wildcard":
                return False, f"заблокирован wildcard-правилом `*.{parent}`"

        return False, "нет активных правил для этого домена"
