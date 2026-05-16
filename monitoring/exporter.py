"""PrometheusExporter: HTTP-сервер /metrics + /health."""
from __future__ import annotations

import asyncio

import structlog
from aiohttp import web
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from monitoring import metrics
from monitoring.collectors import (
    ExchangeCollector,
    PnLCollector,
    SpreadCollector,
    WatchdogCollector,
)

logger = structlog.get_logger(__name__)


class PrometheusExporter:
    """
    HTTP-сервер с двумя эндпоинтами:

        GET /metrics  — Prometheus text format (для scrape)
        GET /health   — JSON {"status": "ok"} (для liveness probe)

    Все collectors обновляются в фоне каждые collect_interval_s секунд.
    Это предотвращает нагрузку на event loop при каждом Prometheus scrape.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9090,
        collect_interval_s: float = 5.0,
        pnl_collector: PnLCollector | None = None,
        spread_collector: SpreadCollector | None = None,
        watchdog_collector: WatchdogCollector | None = None,
        exchange_collector: ExchangeCollector | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._interval = collect_interval_s
        self._collectors = [c for c in [
            pnl_collector, spread_collector,
            watchdog_collector, exchange_collector,
        ] if c is not None]

        self._app = web.Application()
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._app.router.add_get("/health", self._handle_health)

        self._runner: web.AppRunner | None = None
        self._collect_task: asyncio.Task | None = None

    # ---- handlers ----

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        output = generate_latest(metrics.REGISTRY)
        return web.Response(
            body=output,
            content_type=CONTENT_TYPE_LATEST,
            charset="utf-8",
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    # ---- lifecycle ----

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        self._collect_task = asyncio.create_task(
            self._collect_loop(), name="prometheus:collect"
        )
        logger.info("Prometheus exporter запущен", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._collect_task:
            self._collect_task.cancel()
            try:
                await self._collect_task
            except asyncio.CancelledError:
                pass
        if self._runner:
            await self._runner.cleanup()
        logger.info("Prometheus exporter остановлен")

    # ---- collection loop ----

    async def _collect_loop(self) -> None:
        """Периодически вызывает update() на всех collectors."""
        while True:
            await asyncio.sleep(self._interval)
            self._run_collectors()

    def _run_collectors(self) -> None:
        for collector in self._collectors:
            try:
                if hasattr(collector, "update"):
                    collector.update()
            except Exception as exc:
                logger.warning("Ошибка collector", collector=type(collector).__name__, error=repr(exc))

    def generate_output(self) -> bytes:
        """Сгенерировать Prometheus text format (для тестов)."""
        return generate_latest(metrics.REGISTRY)
