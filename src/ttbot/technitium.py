"""Асинхронный клиент HTTP API сервера Technitium DNS.

Подмена ответов реализуется через авторитативные зоны (Zones):
для домена ``example.com`` создаётся Primary-зона, в которую добавляются
A/AAAA-записи на нужные IP — для самого домена (apex) и/или для ``*.example.com``
(wildcard, т.е. все поддомены).
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)


class TechnitiumError(Exception):
    pass


class TechnitiumClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        ttl: int = 300,
        zone_type: str = "Primary",
        verify_ssl: bool = True,
        request_timeout: int = 30,
        max_concurrency: int = 8,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ttl = ttl
        self.zone_type = zone_type
        self.verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> TechnitiumClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    # ----------------------------------------------------------- transport
    async def _call(self, path: str, params: dict) -> dict:
        if self._session is None:
            raise RuntimeError("ClientSession не инициализирована")
        url = f"{self.base_url}/api/{path}"
        q = {"token": self.token, **{k: str(v) for k, v in params.items()}}
        async with self._sem:
            try:
                async with self._session.get(
                    url, params=q, timeout=self._timeout, ssl=self.verify_ssl
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Таймаут aiohttp бросает asyncio.TimeoutError (НЕ ClientError) —
                # ловим оба, чтобы любой сетевой сбой стал TechnitiumError.
                raise TechnitiumError(f"Сетевая ошибка при запросе {path}: {e}") from e
        status = data.get("status")
        if status != "ok":
            msg = data.get("errorMessage") or data.get("status") or "неизвестная ошибка"
            raise TechnitiumError(f"{path}: {msg}")
        return data

    # --------------------------------------------------------------- zones
    async def ping(self) -> int:
        """Проверить доступность API. Возвращает число зон."""
        data = await self._call("zones/list", {})
        return len(data.get("response", {}).get("zones", []))

    async def list_zones(self) -> set[str]:
        data = await self._call("zones/list", {})
        return {
            z.get("name", "").lower()
            for z in data.get("response", {}).get("zones", [])
            if z.get("name")
        }

    async def create_zone(self, zone: str) -> None:
        try:
            await self._call("zones/create", {"zone": zone, "type": self.zone_type})
        except TechnitiumError as e:
            if "already exists" in str(e).lower():
                return
            raise

    async def delete_zone(self, zone: str) -> None:
        """Удалить зону. Best-effort: отсутствие зоны не считается ошибкой."""
        try:
            await self._call("zones/delete", {"zone": zone})
        except TechnitiumError as e:
            low = str(e).lower()
            if "no such zone" in low or "does not exist" in low or "not found" in low:
                return
            raise

    async def add_record(self, name: str, zone: str, rtype: str, ip: str) -> None:
        await self._call(
            "zones/records/add",
            {"domain": name, "zone": zone, "type": rtype, "ttl": self.ttl, "ipAddress": ip},
        )

    # ----------------------------------------------------------- high level
    async def set_spoof(
        self,
        base_domain: str,
        apex: bool,
        wildcard: bool,
        ipv4: list[str],
        ipv6: list[str],
    ) -> None:
        """Привести зону домена к нужному состоянию подмены.

        ``apex`` — подменять ли сам домен; ``wildcard`` — подменять ли поддомены.
        Если оба ``False`` — зона удаляется.

        Реализовано через пересоздание зоны «с чистого листа», что гарантирует
        отсутствие устаревших записей и делает операцию идемпотентной.
        """
        if not apex and not wildcard:
            await self.delete_zone(base_domain)
            return

        await self.delete_zone(base_domain)
        await self.create_zone(base_domain)

        names: list[str] = []
        if apex:
            names.append(base_domain)
        if wildcard:
            names.append(f"*.{base_domain}")

        for name in names:
            for ip in ipv4:
                await self.add_record(name, base_domain, "A", ip)
            for ip in ipv6:
                await self.add_record(name, base_domain, "AAAA", ip)
