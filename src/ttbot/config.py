"""Загрузка и валидация конфигурации (YAML)."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# {x}h, {x}m или {x}h{y}m  (часы/минуты)
_INTERVAL_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.IGNORECASE)


class ConfigError(Exception):
    """Ошибка конфигурации."""


def parse_interval(text: object) -> int:
    """Разобрать строку интервала в секунды.

    Поддерживаются форматы ``{x}h``, ``{x}m`` и ``{x}h{y}m``.
    """
    s = str(text)
    m = _INTERVAL_RE.match(s)
    if not m or (m.group(1) is None and m.group(2) is None):
        raise ConfigError(f"Неверный формат интервала {s!r}. Допустимо: '6h', '30m', '1h30m'.")
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    total = hours * 3600 + minutes * 60
    if total <= 0:
        raise ConfigError("Интервал обновления должен быть больше нуля.")
    return total


@dataclass
class TechnitiumCfg:
    url: str
    token: str
    ttl: int = 300
    zone_type: str = "Primary"
    verify_ssl: bool = True
    request_timeout: int = 30
    max_concurrency: int = 8


@dataclass
class TelegramCfg:
    bot_token: str
    whitelist: set[int]


@dataclass
class BlockList:
    name: str
    url: str


@dataclass
class Config:
    spoof_ipv4: list[str]
    spoof_ipv6: list[str]
    update_interval_seconds: int
    interval_raw: str
    technitium: TechnitiumCfg
    telegram: TelegramCfg
    blocklists: list[BlockList]
    state_file: Path
    list_fetch_timeout: int = 60


def _require(data: dict, key: str):
    if key not in data or data[key] in (None, "", []):
        raise ConfigError(f"В конфиге отсутствует обязательный параметр: {key!r}")
    return data[key]


def _split_ips(values: object) -> tuple[list[str], list[str]]:
    if isinstance(values, (str, int)):
        items: list[object] = [values]
    elif isinstance(values, (list, tuple)):
        items = list(values)
    else:
        raise ConfigError(f"Некорректное значение spoof_ips: {values!r}")
    v4: list[str] = []
    v6: list[str] = []
    seen: set[str] = set()
    for raw in items:
        try:
            ip = ipaddress.ip_address(str(raw).strip())
        except ValueError as e:
            raise ConfigError(f"Некорректный IP в spoof_ips: {raw!r} ({e})") from e
        canonical = str(ip)
        if canonical in seen:  # отбрасываем дубли, сохраняя порядок
            continue
        seen.add(canonical)
        (v4 if ip.version == 4 else v6).append(canonical)
    if not v4 and not v6:
        raise ConfigError("Не задан ни один IP в spoof_ips.")
    return v4, v6


def parse_spoof_ips(raw: str) -> tuple[list[str], list[str]]:
    """Разобрать пользовательский ввод IP (через пробел/запятую/точку с запятой).

    Возвращает ``(ipv4, ipv6)`` с дедупликацией. Бросает ``ConfigError`` на
    пустой или некорректный ввод (используется при смене IP из Telegram-бота).
    """
    parts = [p for p in re.split(r"[\s,;]+", str(raw).strip()) if p]
    if not parts:
        raise ConfigError("Не указан ни один IP-адрес.")
    return _split_ips(parts)


def _normalize_url(url: str) -> str:
    url = str(url).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Файл конфигурации не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError("Конфиг должен быть YAML-объектом.")

    v4, v6 = _split_ips(_require(data, "spoof_ips"))
    interval_raw = str(_require(data, "update_interval"))
    interval = parse_interval(interval_raw)

    tech_raw = _require(data, "technitium")
    technitium = TechnitiumCfg(
        url=_normalize_url(_require(tech_raw, "url")),
        token=str(_require(tech_raw, "token")),
        ttl=int(tech_raw.get("ttl", 300)),
        zone_type=str(tech_raw.get("zone_type", "Primary")),
        verify_ssl=bool(tech_raw.get("verify_ssl", True)),
        request_timeout=int(tech_raw.get("request_timeout", 30)),
        max_concurrency=int(tech_raw.get("max_concurrency", 8)),
    )

    tg_raw = _require(data, "telegram")
    whitelist_raw = _require(tg_raw, "whitelist")
    if isinstance(whitelist_raw, (int, str)):
        whitelist_raw = [whitelist_raw]
    try:
        whitelist = {int(x) for x in whitelist_raw}
    except (TypeError, ValueError) as e:
        raise ConfigError("telegram.whitelist должен содержать числовые Telegram ID.") from e
    if not whitelist:
        raise ConfigError("telegram.whitelist не может быть пустым (это защита бота).")
    telegram = TelegramCfg(
        bot_token=str(_require(tg_raw, "bot_token")),
        whitelist=whitelist,
    )

    blocklists: list[BlockList] = []
    for i, item in enumerate(data.get("blocklists", []) or []):
        if not isinstance(item, dict) or "url" not in item:
            raise ConfigError(f"blocklists[{i}] должен содержать поле 'url'.")
        blocklists.append(BlockList(name=str(item.get("name", item["url"])), url=str(item["url"])))

    state_file = Path(data.get("state_file", "state.json"))

    return Config(
        spoof_ipv4=v4,
        spoof_ipv6=v6,
        update_interval_seconds=interval,
        interval_raw=interval_raw,
        technitium=technitium,
        telegram=telegram,
        blocklists=blocklists,
        state_file=state_file,
        list_fetch_timeout=int(data.get("list_fetch_timeout", 60)),
    )
