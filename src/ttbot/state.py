"""Хранилище состояния бота.

Состояние состоит из двух частей:

``user_rules`` — словарь ``домен -> UserRule``. Пользовательские правила имеют
    наивысший приоритет. Действие ``add`` означает «подменять», ``block`` —
    «никогда не подменять». Для одного домена возможно только одно правило.

``list_domains`` — «липкое» множество доменов, попавших из интернет-списков.
    Домены отсюда никогда не удаляются автоматически (даже если исчезли из
    источника) — соответствует требованию «удалённые из списка не трогаем».

``spoof_ipv4`` / ``spoof_ipv6`` — текущие IP-адреса подмены. Сидируются из
    конфига при первом запуске и затем являются источником истины (их можно
    сменить из Telegram-бота). См. ``set_spoof_ips``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class UserRule:
    domain: str
    action: str  # "add" | "block"
    scope: str  # "wildcard" | "exact"


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.user_rules: dict[str, UserRule] = {}
        self.list_domains: set[str] = set()
        self.spoof_ipv4: list[str] = []
        self.spoof_ipv6: list[str] = []

    # ------------------------------------------------------------------ I/O
    def load(self) -> None:
        if not self.path.exists():
            log.info("Файл состояния %s не найден — начинаем с пустого.", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("Не удалось прочитать состояние (%s) — старт с пустого.", e)
            return
        self.user_rules = {}
        for r in data.get("user_rules", []):
            try:
                rule = UserRule(domain=r["domain"], action=r["action"], scope=r["scope"])
                self.user_rules[rule.domain] = rule
            except (KeyError, TypeError):
                continue
        self.list_domains = set(data.get("list_domains", []))
        self.spoof_ipv4 = [str(x) for x in data.get("spoof_ipv4", [])]
        self.spoof_ipv6 = [str(x) for x in data.get("spoof_ipv6", [])]
        log.info(
            "Состояние загружено: %d пользовательских правил, %d доменов из списков, "
            "IP подмены: %s.",
            len(self.user_rules),
            len(self.list_domains),
            ", ".join(self.spoof_ipv4 + self.spoof_ipv6) or "—",
        )

    def save(self) -> None:
        payload = {
            "user_rules": [asdict(r) for r in self.user_rules.values()],
            "list_domains": sorted(self.list_domains),
            "spoof_ipv4": self.spoof_ipv4,
            "spoof_ipv6": self.spoof_ipv6,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Атомарная запись: пишем во временный файл и переименовываем.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — любой сбой: чистим temp и пробрасываем
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -------------------------------------------------------------- мутации
    def set_rule(self, domain: str, action: str, scope: str) -> UserRule:
        """Установить (заменить) пользовательское правило для домена."""
        # Сохраняем порядок: при перезаписи правило встаёт в конец списка.
        self.user_rules.pop(domain, None)
        rule = UserRule(domain=domain, action=action, scope=scope)
        self.user_rules[domain] = rule
        self.save()
        return rule

    def pop_rule(self, index_1based: int) -> UserRule | None:
        """Удалить правило по его порядковому номеру (с 1). None — если нет."""
        rules = list(self.user_rules.values())
        if not (1 <= index_1based <= len(rules)):
            return None
        rule = rules[index_1based - 1]
        del self.user_rules[rule.domain]
        self.save()
        return rule

    def add_list_domains(self, domains: set[str]) -> None:
        if not domains:
            return
        self.list_domains |= set(domains)
        self.save()

    def set_spoof_ips(self, ipv4: list[str], ipv6: list[str]) -> None:
        """Заменить текущий набор IP-адресов подмены и сохранить."""
        self.spoof_ipv4 = list(ipv4)
        self.spoof_ipv6 = list(ipv6)
        self.save()

    def has_spoof_ips(self) -> bool:
        return bool(self.spoof_ipv4 or self.spoof_ipv6)

    # ------------------------------------------------------------- чтение
    def list_rules(self) -> list[UserRule]:
        return list(self.user_rules.values())

    def get_rule(self, domain: str) -> UserRule | None:
        return self.user_rules.get(domain)
