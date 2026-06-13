"""Генерация человекочитаемого файла правил для внешнего потребителя.

Файл (`rules_file`, по умолчанию `rules.yaml`) описывает ЭФФЕКТИВНЫЕ правила
подмены (интернет-списки + пользовательские правила): для каждого домена — нужно
ли подменять сам домен (``apex``) и его поддомены (``subdomains``). Домена нет в
файле → подмены нет (запрос уходит на вышестоящие DNS-серверы).

Предназначен для чтения сторонним Technitium App. Регенерируется ботом при каждом
изменении; вручную редактировать не нужно (перезапишется).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

import yaml

_HEADER = (
    "# Сгенерировано автоматически technitium-dns-bot — НЕ редактировать вручную.\n"
    "# Эффективные правила подмены DNS. Домена нет в списке -> форвард наверх.\n"
    "#   apex=true       — подменять сам домен\n"
    "#   subdomains=true — подменять *.домен (поддомены)\n"
)


def build_rules(
    domains: Iterable[str],
    desired: Callable[[str], tuple[bool, bool]],
    ipv4: list[str],
    ipv6: list[str],
) -> dict:
    """Построить структуру правил: только домены, где что-то подменяется."""
    rules = []
    for d in sorted(domains):
        apex, subdomains = desired(d)
        if apex or subdomains:
            rules.append({"domain": d, "apex": apex, "subdomains": subdomains})
    return {"spoof_ipv4": list(ipv4), "spoof_ipv6": list(ipv6), "rules": rules}


def write_rules(path: str | Path, data: dict) -> None:
    """Атомарно записать файл правил (YAML с шапкой-комментарием)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_HEADER)
            f.write(body)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — чистим temp и пробрасываем
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
