"""Точка входа: ``python -m ttbot --config config.yaml``."""

from __future__ import annotations

import argparse
import logging
import sys

import aiohttp
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from . import __version__
from .bot import register_handlers
from .config import ConfigError, load_config
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

    try:
        zones = await client.ping()
        log.info("Technitium доступен (%s), зон на сервере: %d", cfg.technitium.url, zones)
    except TechnitiumError as e:
        log.error("Не удалось связаться с Technitium API: %s", e)

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


def build_application(config_path: str) -> Application:
    cfg = load_config(config_path)
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
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Не засоряем лог отладкой HTTP-библиотек.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    try:
        application = build_application(args.config)
    except ConfigError as e:
        log.error("Ошибка конфигурации: %s", e)
        return 2

    log.info("Запуск бота…")
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    return 0


if __name__ == "__main__":
    sys.exit(main())
