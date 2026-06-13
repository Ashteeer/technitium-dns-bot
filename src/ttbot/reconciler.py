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
from .rules import build_rules, write_rules
from .state import StateStore, UserRule
from .technitium import TechnitiumClient, TechnitiumError, parse_zone_spoof

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    new_domains: int
    applied: int
    failed: int
    list_stats: list[ListStat]


@dataclass
class ProxyHit:
    """Проксируемая зона, найденная в Technitium (ниже по дереву от запроса)."""

    domain: str  # имя зоны, напр. "robinfrontend-pa.googleapis.com"
    apex: bool  # подменяется сам домен
    wildcard: bool  # подменяются его поддомены (*.domain)
    ips: list[str]


@dataclass
class CheckReport:
    """Результат проверки домена по фактическим зонам Technitium."""

    query: str
    proxied: bool  # подменяется ли сам query
    reason: str  # как именно (для proxied)
    ips: list[str]  # IP, на которые подменяется query
    subdomains: list[ProxyHit]  # проксируемые поддомены query (вниз по дереву)


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

    async def _safe_delete(self, domain: str) -> bool:
        try:
            await self.client.delete_zone(domain)
            return True
        except TechnitiumError as e:
            log.warning("Не удалось удалить зону %s: %s", domain, e)
            return False

    def export_rules(self) -> None:
        """Перезаписать файл правил (``rules_file``) для внешнего Technitium App.

        Содержит эффективные правила (списки + пользовательские) в виде
        ``{domain, apex, subdomains}`` + текущие IP подмены. Вызывается после
        каждого изменения состояния.
        """
        data = build_rules(
            self.managed_domains(),
            self._desired,
            self.state.spoof_ipv4,
            self.state.spoof_ipv6,
        )
        write_rules(self.cfg.rules_file, data)

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
        self.export_rules()
        return SyncResult(len(new), applied, failed, stats)

    # ---------------------------------------------------- действия из бота
    async def user_add(self, domain: str, scope: str) -> None:
        """Пользователь добавляет домен (он будет подменяться)."""
        self.state.set_rule(domain, "add", scope)
        await self.apply_domain(domain)
        self.export_rules()

    async def user_block(self, domain: str, scope: str) -> None:
        """Пользователь удаляет домен (он больше не будет подменяться)."""
        self.state.set_rule(domain, "block", scope)
        await self.apply_domain(domain)
        self.export_rules()

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
        self.export_rules()
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
        self.export_rules()
        return applied, failed

    # ------------------------------------------------- сброс и перезагрузка
    async def flush(self) -> int:
        """Сбросить всё в исходное состояние: удалить все правила и зоны.

        Удаляет управляемые зоны из Technitium, очищает пользовательские правила
        и домены из списков, пере-сидирует IP подмены из конфига. Возвращает
        число доменов, для которых удалялись зоны.
        """
        domains = self.managed_domains()
        for batch in _chunks(domains, 100):
            await asyncio.gather(*(self._safe_delete(d) for d in batch))
        self.state.user_rules = {}
        self.state.list_domains = set()
        self.state.set_spoof_ips(self.cfg.spoof_ipv4, self.cfg.spoof_ipv6)  # сохраняет
        self.export_rules()
        log.info("Flush: удалены все правила и %d зон.", len(domains))
        return len(domains)

    async def reload(self, session: aiohttp.ClientSession) -> SyncResult:
        """Перекачать списки и заново применить ВСЕ правила (с учётом пользовательских).

        В отличие от ``sync_lists`` (только новые домены) — применяет повторно
        все управляемые домены, чтобы подхватить изменения списков/правил.
        """
        incoming, stats = await fetch_all(session, self.cfg.blocklists, self.cfg.list_fetch_timeout)
        new = incoming - self.state.list_domains
        if new:
            self.state.add_list_domains(set(new))

        domains = self.managed_domains()
        applied = failed = 0
        for batch in _chunks(domains, 100):
            results = await asyncio.gather(*(self._safe_apply(d) for d in batch))
            applied += sum(1 for r in results if r)
            failed += sum(1 for r in results if not r)

        self.export_rules()
        log.info(
            "Reload: новых доменов=%d, применено=%d, ошибок=%d (всего=%d).",
            len(new),
            applied,
            failed,
            len(domains),
        )
        return SyncResult(len(new), applied, failed, stats)

    # ---------------------------------------------------- проверка домена
    async def check_domain(self, query: str) -> CheckReport:
        """Проверить домен по **фактическим зонам Technitium** (через API).

        Возвращает :class:`CheckReport`:
        1. Подменяется ли сам ``query`` — если есть apex-запись в его зоне ИЛИ
           один из родителей покрывает его wildcard'ом (``*.parent``).
        2. Какие **поддомены** ``query`` проксируются — перечисляются зоны ниже
           по дереву (например, при проверке ``google.com`` найдётся
           ``*.test.google.com``), с IP из их записей.
        """
        zones = await self.client.list_zones()
        parts = query.split(".")
        # Предки query без TLD: для test.google.com → google.com (не com).
        ancestors = [".".join(parts[i:]) for i in range(1, len(parts) - 1)]
        # Зоны строго ниже query (его поддомены).
        sub_zone_names = sorted(z for z in zones if z.endswith("." + query))

        to_inspect: list[str] = []
        if query in zones:
            to_inspect.append(query)
        to_inspect.extend(a for a in ancestors if a in zones)
        to_inspect.extend(sub_zone_names)

        records = await asyncio.gather(*(self.client.get_records(z) for z in to_inspect))
        spoof = {z: parse_zone_spoof(rec, z) for z, rec in zip(to_inspect, records, strict=True)}

        # 1. Сам query.
        proxied = False
        reason = ""
        ips: list[str] = []
        q_apex, q_wild, q_ips = spoof.get(query, (False, False, []))
        if q_apex:
            proxied = True
            ips = q_ips
            reason = "домен и поддомены" if q_wild else "только домен"
        else:
            for parent in ancestors:
                _, p_wild, p_ips = spoof.get(parent, (False, False, []))
                if p_wild:
                    proxied = True
                    ips = p_ips
                    reason = f"покрыт wildcard `*.{parent}`"
                    break

        # 2. Поддомены query.
        subdomains: list[ProxyHit] = []
        # Собственный wildcard query, когда сам домен (apex) не подменяется:
        # сам query не проксируется, но его поддомены (*.query) — да.
        if q_wild and not q_apex:
            subdomains.append(ProxyHit(query, apex=False, wildcard=True, ips=q_ips))
        for z in sub_zone_names:
            z_apex, z_wild, z_ips = spoof[z]
            if z_apex or z_wild:
                subdomains.append(ProxyHit(z, apex=z_apex, wildcard=z_wild, ips=z_ips))

        return CheckReport(query, proxied, reason, ips, subdomains)
