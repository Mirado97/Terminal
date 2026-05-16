"""TerminalApiServer: aiohttp HTTP/WebSocket сервер для фронтенда."""
from __future__ import annotations

import asyncio
import time

import structlog
from aiohttp import web
from aiohttp.web_middlewares import middleware

from api.ws_handler import WebSocketBroadcaster

logger = structlog.get_logger(__name__)


@middleware
async def cors_middleware(request: web.Request, handler):
    """Разрешить CORS для фронтенда на localhost."""
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


class TerminalApiServer:
    """
    HTTP + WebSocket сервер для фронтенда.

    REST endpoints:
        GET /api/status   — общий статус системы
        GET /api/risk     — риск-движок: drawdown, exposure, violations
        GET /api/trades   — последние сделки (?limit=50)
        GET /api/spreads  — последние возможности (?symbol=BTCUSDT&limit=100)
        GET /api/latency  — латентность по биржам
        GET /health       — liveness probe

    WebSocket:
        GET /ws           — real-time push каждые push_interval_s секунд

    WebSocket message format:
        { "type": "snapshot", "ts": 1234567890, "data": { ... } }
    """

    def __init__(
        self,
        inventory=None,       # InventoryManager (duck-typing)
        risk_engine=None,     # RiskEngine
        orchestrator=None,    # WatchdogOrchestrator
        latency_tracker=None, # LatencyTracker
        host: str = "0.0.0.0",
        port: int = 8080,
        push_interval_s: float = 1.0,
    ) -> None:
        self._inventory = inventory
        self._risk = risk_engine
        self._orchestrator = orchestrator
        self._latency = latency_tracker
        self._host = host
        self._port = port
        self._push_interval = push_interval_s

        self._broadcaster = WebSocketBroadcaster()
        self._app = web.Application(middlewares=[cors_middleware])
        self._setup_routes()

        self._runner: web.AppRunner | None = None
        self._push_task: asyncio.Task | None = None

    # ---- REST handlers ----

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "ts": int(time.time() * 1000)})

    async def handle_status(self, request: web.Request) -> web.Response:
        data: dict = {"ts": int(time.time() * 1000), "ws_clients": self._broadcaster.connections_count}
        if self._inventory:
            data["inventory"] = self._inventory.snapshot()
        if self._orchestrator:
            data["watchdog"] = self._orchestrator.status()
        return web.json_response(data)

    async def handle_risk(self, request: web.Request) -> web.Response:
        if not self._risk:
            return web.json_response({"error": "risk engine not configured"}, status=503)
        return web.json_response(self._risk.status())

    async def handle_trades(self, request: web.Request) -> web.Response:
        # Возвращаем заглушку; в production — из TradeRepository
        return web.json_response({"trades": [], "total": 0})

    async def handle_spreads(self, request: web.Request) -> web.Response:
        return web.json_response({"spreads": [], "total": 0})

    async def handle_latency(self, request: web.Request) -> web.Response:
        if not self._latency:
            return web.json_response({})
        return web.json_response({
            "bybit": {
                "ws": self._latency.ws_stats("bybit"),
                "rest": self._latency.rest_stats("bybit"),
            },
            "gate": {
                "ws": self._latency.ws_stats("gate"),
                "rest": self._latency.rest_stats("gate"),
            },
            "mexc": {
                "ws": self._latency.ws_stats("mexc"),
                "rest": self._latency.rest_stats("mexc"),
            },
            "execution": self._latency.execution_stats(),
        })

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        return await self._broadcaster.handle(request)

    # ---- lifecycle ----

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        self._push_task = asyncio.create_task(
            self._push_loop(), name="api:push"
        )
        logger.info("Terminal API запущен", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._push_task:
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass
        if self._runner:
            await self._runner.cleanup()
        logger.info("Terminal API остановлен")

    # ---- WebSocket push loop ----

    async def _push_loop(self) -> None:
        while True:
            await asyncio.sleep(self._push_interval)
            if self._broadcaster.connections_count == 0:
                continue
            snapshot = self._build_snapshot()
            await self._broadcaster.broadcast(snapshot)

    def _build_snapshot(self) -> dict:
        data: dict = {}
        if self._inventory:
            data["inventory"] = self._inventory.snapshot()
        if self._risk:
            data["risk"] = self._risk.status()
        if self._orchestrator:
            data["watchdog"] = self._orchestrator.status()
        if self._latency:
            data["latency"] = {
                "execution": self._latency.execution_stats(),
            }
        return {
            "type": "snapshot",
            "ts": int(time.time() * 1000),
            "data": data,
        }

    # ---- internal ----

    def _setup_routes(self) -> None:
        self._app.router.add_get("/health",      self.handle_health)
        self._app.router.add_get("/api/status",  self.handle_status)
        self._app.router.add_get("/api/risk",    self.handle_risk)
        self._app.router.add_get("/api/trades",  self.handle_trades)
        self._app.router.add_get("/api/spreads", self.handle_spreads)
        self._app.router.add_get("/api/latency", self.handle_latency)
        self._app.router.add_get("/ws",          self.handle_ws)

    @property
    def broadcaster(self) -> WebSocketBroadcaster:
        return self._broadcaster
