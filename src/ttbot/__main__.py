"""Точка входа: ``python -m ttbot --config config.yaml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import aiohttp
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from . import __version__
from .bot import register_handlers
from .config import Config, ConfigError, load_config
from .reconciler import Reconciler
from .state import StateStore
from .technitium import TechnitiumClient, TechnitiumError

log = logging.getLogger("ttbot")

SYNC_JOB = "sync_lists"


async def _sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    reconciler: Reconciler = context.application.bot_data["reconciler"]
    session: aiohttp.ClientSession = context.application.bot_data["session"]
    try:
        result = await reconciler.sync_lists(session)
        for s in result.list_stats:
            if s.error:
                log.warning("Список %r не загружен: %s", s.name, s.error)
    except Exception:  # noqa: BLE001
        log.exception("Сбой при обновлении списков")


async def _on_startup(application: Application) -> None:
    cfg = application.bot_data["config"]

    session = aiohttp.ClientSession()
    client = TechnitiumClient(
        cfg.technitium.url,
        cfg.technitium.token,
        ttl=cfg.technitium.ttl,
        zone_type=cfg.technitium.zone_type,
        verify_ssl=cfg.technitium.verify_ssl,
        request_timeout=cfg.technitium.request_timeout,
        max_concurrency=cfg.technitium.max_concurrency,
        session=session,
    )
    state = StateStore(cfg.state_file)
    state.load()
    # IP подмены: при первом запуске берём из конфига, далее источник истины —
    # состояние (его можно сменить из Telegram-бота, см. Reconciler.change_spoof_ips).
    if not state.has_spoof_ips():
        state.set_spoof_ips(cfg.spoof_ipv4, cfg.spoof_ipv6)
        log.info(
            "IP подмены засеяны из конфига: %s",
            ", ".join(cfg.spoof_ipv4 + cfg.spoof_ipv6),
        )
    reconciler = Reconciler(cfg, client, state)

    application.bot_data["session"] = session
    application.bot_data["client"] = client
    application.bot_data["state"] = state
    application.bot_data["reconciler"] = reconciler

    if cfg.manage_zones:
        try:
            zones = await client.ping()
            log.info("Technitium доступен (%s), зон: %d", cfg.technitium.url, zones)
        except TechnitiumError as e:
            log.error("Не удалось связаться с Technitium API: %s", e)
    else:
        log.info("Режим App: зоны не управляются, правила пишутся в %s", cfg.rules_file)

    if application.job_queue is None:
        raise RuntimeError("JobQueue недоступна — установите python-telegram-bot[job-queue].")
    application.job_queue.run_repeating(
        _sync_job,
        interval=cfg.update_interval_seconds,
        first=10,
        name=SYNC_JOB,
    )
    log.info(
        "Планировщик: обновление списков каждые %s (%d сек).",
        cfg.interval_raw,
        cfg.update_interval_seconds,
    )


async def _on_shutdown(application: Application) -> None:
    session = application.bot_data.get("session")
    if session is not None:
        await session.close()
    log.info("Остановлен.")


async def _run_oneshot(cfg: Config, action: str) -> None:
    """Одноразовая операция (--flush / --reload) без запуска Telegram-бота."""
    session = aiohttp.ClientSession()
    try:
        client = TechnitiumClient(
            cfg.technitium.url,
            cfg.technitium.token,
            ttl=cfg.technitium.ttl,
            zone_type=cfg.technitium.zone_type,
            verify_ssl=cfg.technitium.verify_ssl,
            request_timeout=cfg.technitium.request_timeout,
            max_concurrency=cfg.technitium.max_concurrency,
            session=session,
        )
        state = StateStore(cfg.state_file)
        state.load()
        if not state.has_spoof_ips():
            state.set_spoof_ips(cfg.spoof_ipv4, cfg.spoof_ipv6)
        reconciler = Reconciler(cfg, client, state)
        if action == "flush":
            n = await reconciler.flush()
            log.info("Flush выполнен: удалено зон=%d, все правила сброшены.", n)
        elif action == "cleanup-zones":
            n = await reconciler.cleanup_zones()
            log.info("Cleanup выполнен: удалено зон=%d (правила сохранены).", n)
        else:  # reload
            res = await reconciler.reload(session)
            log.info(
                "Reload выполнен: новых доменов=%d, применено=%d, ошибок=%d.",
                res.new_domains,
                res.applied,
                res.failed,
            )
    finally:
        await session.close()


def build_application(cfg: Config) -> Application:
    application = (
        ApplicationBuilder()
        .token(cfg.telegram.bot_token)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    application.bot_data["config"] = cfg
    register_handlers(application)
    return application


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="technitium-bot", description="Technitium DNS spoofing Telegram bot"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", default="config.yaml", help="путь к config.yaml")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--flush",
        action="store_true",
        help="удалить все правила и зоны, сбросить состояние, и выйти",
    )
    group.add_argument(
        "--reload",
        action="store_true",
        help="перекачать списки и заново применить все правила, и выйти",
    )
    group.add_argument(
        "--cleanup-zones",
        action="store_true",
        help="удалить все управляемые зоны Technitium (правила сохранить), и выйти",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Не засоряем лог отладкой HTTP-библиотек.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("Ошибка конфигурации: %s", e)
        return 2

    action = None
    if args.flush:
        action = "flush"
    elif args.reload:
        action = "reload"
    elif args.cleanup_zones:
        action = "cleanup-zones"
    if action:
        try:
            asyncio.run(_run_oneshot(cfg, action))
        except TechnitiumError as e:
            log.error("Ошибка Technitium: %s", e)
            return 1
        return 0

    log.info("Запуск бота…")
    application = build_application(cfg)
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    return 0


if __name__ == "__main__":
    sys.exit(main())
