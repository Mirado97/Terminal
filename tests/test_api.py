"""Тесты Terminal API: WebSocketBroadcaster, TerminalApiServer."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import AioHTTPTestCase, TestClient

from api.server import TerminalApiServer
from api.ws_handler import WebSocketBroadcaster


# ---- вспомогательные моки ----

def _mock_inventory(pnl: float = 50.0) -> MagicMock:
    inv = MagicMock()
    inv.snapshot.return_value = {
        "realized_pnl": pnl,
        "peak_pnl": 80.0,
        "drawdown_pct": 2.5,
        "total_exposure_usdt": 1000.0,
        "trade_count": 10,
        "positions": {},
    }
    return inv


def _mock_risk() -> MagicMock:
    risk = MagicMock()
    risk.status.return_value = {
        "halted": False,
        "paused": False,
        "pause_reason": "",
        "drawdown_pct": 2.5,
        "realized_pnl": 50.0,
        "total_exposure_usdt": 1000.0,
        "trade_count": 10,
        "violations": 0,
    }
    return risk


def _mock_orchestrator() -> MagicMock:
    orch = MagicMock()
    orch.status.return_value = {
        "total": 5,
        "running": 4,
        "failed": 1,
        "disabled": 0,
        "stopped": 0,
        "starting": 0,
        "routed_opportunities": 100,
        "missed_opportunities": 2,
        "workers": [],
    }
    return orch


def _make_server(**kwargs) -> TerminalApiServer:
    return TerminalApiServer(
        inventory=_mock_inventory(),
        risk_engine=_mock_risk(),
        orchestrator=_mock_orchestrator(),
        port=18080,
        push_interval_s=60.0,  # не пушим в тестах
        **kwargs,
    )


# =========================================================
# WebSocketBroadcaster
# =========================================================

def test_broadcaster_initial_count():
    b = WebSocketBroadcaster()
    assert b.connections_count == 0


def test_broadcaster_broadcast_empty():
    """Broadcast без подключений не должен бросать исключение."""
    async def _run():
        b = WebSocketBroadcaster()
        await b.broadcast({"type": "test"})

    asyncio.run(_run())


def test_broadcaster_broadcast_sends_json():
    """Broadcast рассылает JSON всем подключённым клиентам."""
    async def _run():
        b = WebSocketBroadcaster()
        ws_mock = AsyncMock()
        ws_mock.send_str = AsyncMock()

        async with b._lock:
            b._connections.add(ws_mock)

        await b.broadcast({"type": "snapshot", "data": {"pnl": 100.0}})
        return ws_mock.send_str.call_args[0][0]

    payload = asyncio.run(_run())
    data = json.loads(payload)
    assert data["type"] == "snapshot"
    assert data["data"]["pnl"] == 100.0


def test_broadcaster_removes_dead_connection():
    """Упавшее соединение удаляется из множества при broadcast."""
    async def _run():
        b = WebSocketBroadcaster()
        dead_ws = AsyncMock()
        dead_ws.send_str = AsyncMock(side_effect=Exception("соединение закрыто"))

        async with b._lock:
            b._connections.add(dead_ws)

        await b.broadcast({"type": "ping"})
        return b.connections_count

    count = asyncio.run(_run())
    assert count == 0


# =========================================================
# TerminalApiServer — REST endpoints (через aiohttp TestClient)
# =========================================================

class TestApiServer(AioHTTPTestCase):
    async def get_application(self):
        server = TerminalApiServer(
            inventory=_mock_inventory(pnl=123.0),
            risk_engine=_mock_risk(),
            orchestrator=_mock_orchestrator(),
            push_interval_s=9999,  # push loop не мешает тестам
        )
        return server._app

    async def test_health_endpoint(self):
        resp = await self.client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "ts" in data

    async def test_status_endpoint(self):
        resp = await self.client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert "inventory" in data
        assert "watchdog" in data
        assert data["inventory"]["realized_pnl"] == 123.0

    async def test_risk_endpoint(self):
        resp = await self.client.get("/api/risk")
        assert resp.status == 200
        data = await resp.json()
        assert "halted" in data
        assert "drawdown_pct" in data

    async def test_trades_endpoint(self):
        resp = await self.client.get("/api/trades")
        assert resp.status == 200
        data = await resp.json()
        assert "trades" in data

    async def test_spreads_endpoint(self):
        resp = await self.client.get("/api/spreads")
        assert resp.status == 200
        data = await resp.json()
        assert "spreads" in data

    async def test_latency_endpoint_no_tracker(self):
        resp = await self.client.get("/api/latency")
        assert resp.status == 200

    async def test_risk_endpoint_no_engine(self):
        """Без risk engine — 503."""
        from aiohttp.test_utils import TestServer
        server = TerminalApiServer(push_interval_s=9999)
        client = TestClient(TestServer(server._app))
        await client.start_server()
        resp = await client.get("/api/risk")
        assert resp.status == 503
        await client.close()


# =========================================================
# TerminalApiServer — snapshot builder
# =========================================================

def test_build_snapshot_structure():
    server = _make_server()
    snap = server._build_snapshot()

    assert snap["type"] == "snapshot"
    assert "ts" in snap
    assert "data" in snap
    assert "inventory" in snap["data"]
    assert "risk" in snap["data"]
    assert "watchdog" in snap["data"]


def test_build_snapshot_pnl_value():
    server = _make_server()
    snap = server._build_snapshot()
    assert snap["data"]["inventory"]["realized_pnl"] == 50.0


def test_build_snapshot_without_optional_components():
    server = TerminalApiServer()  # всё None
    snap = server._build_snapshot()
    assert snap["type"] == "snapshot"
    assert snap["data"] == {}


# =========================================================
# TerminalApiServer — lifecycle
# =========================================================

def test_server_start_stop():
    async def _run():
        server = TerminalApiServer(port=18081, push_interval_s=9999)
        await server.start()
        await asyncio.sleep(0.05)
        await server.stop()

    asyncio.run(_run())


def test_push_loop_broadcasts_when_clients():
    """Push loop должен вызвать broadcaster.broadcast при наличии клиентов."""
    async def _run():
        server = TerminalApiServer(
            inventory=_mock_inventory(),
            push_interval_s=0.05,
        )
        calls = []
        original = server._broadcaster.broadcast
        server._broadcaster.broadcast = AsyncMock(side_effect=lambda m: calls.append(m))

        # Симулируем одного подключённого клиента
        fake_ws = AsyncMock()
        async with server._broadcaster._lock:
            server._broadcaster._connections.add(fake_ws)

        push_task = asyncio.create_task(server._push_loop())
        await asyncio.sleep(0.2)
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass

        return calls

    calls = asyncio.run(_run())
    assert len(calls) >= 1
    assert calls[0]["type"] == "snapshot"


def test_push_loop_skips_when_no_clients():
    """Push loop НЕ вызывает broadcast если нет клиентов."""
    async def _run():
        server = TerminalApiServer(push_interval_s=0.05)
        calls = []
        server._broadcaster.broadcast = AsyncMock(side_effect=lambda m: calls.append(m))

        push_task = asyncio.create_task(server._push_loop())
        await asyncio.sleep(0.2)
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass

        return calls

    calls = asyncio.run(_run())
    assert len(calls) == 0
