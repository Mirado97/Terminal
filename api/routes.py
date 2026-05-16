"""REST обработчики: /api/status, /api/trades, /api/spreads, /api/risk."""
from __future__ import annotations

import json

from aiohttp import web


def make_routes(server: "TerminalApiServer") -> list[web.RouteDef]:  # type: ignore[name-defined]
    return [
        web.get("/api/status",  server.handle_status),
        web.get("/api/risk",    server.handle_risk),
        web.get("/api/trades",  server.handle_trades),
        web.get("/api/spreads", server.handle_spreads),
        web.get("/api/latency", server.handle_latency),
        web.get("/ws",          server.handle_ws),
        web.get("/health",      server.handle_health),
    ]
