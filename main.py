"""Точка входа арбитражного терминала."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# uvloop даёт ~2x прирост производительности event loop, но недоступен на Windows
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from core.config import ConfigManager
from core.lifecycle import AsyncLifecycle
from core.logging import get_logger, setup_logging
from credentials.manager import SecretsManager


async def main() -> None:
    config = ConfigManager(config_dir=Path("config")).load()

    log_level: str = config.get("app", "log_level", default="INFO")
    app_env: str = config.get("app", "env", default="development")
    setup_logging(level=log_level, json_output=(app_env == "production"))

    log = get_logger("main")
    log.info("Инициализация терминала", env=app_env)

    secrets = SecretsManager()
    creds_status = secrets.validate_all(["bybit", "binance"])
    for exchange, ok in creds_status.items():
        if ok:
            log.info("Credentials загружены", exchange=exchange)
        else:
            log.warning("Credentials не найдены — заполните .env", exchange=exchange)

    lifecycle = AsyncLifecycle()

    @lifecycle.on_startup
    async def _startup() -> None:
        log.info("Phase 1 Foundation — готово к интеграции следующих фаз")

    @lifecycle.on_shutdown
    async def _shutdown() -> None:
        log.info("Graceful shutdown завершён")

    async with lifecycle.lifespan():
        await lifecycle.run_until_stopped()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
