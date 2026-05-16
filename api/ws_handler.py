"""WebSocketBroadcaster: управление подключениями и широковещательная рассылка."""
from __future__ import annotations

import asyncio
import json

import structlog
from aiohttp import WSMsgType, web

logger = structlog.get_logger(__name__)


class WebSocketBroadcaster:
    """
    Хранит множество активных WebSocket подключений и рассылает им сообщения.

    Жизненный цикл соединения:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async with broadcaster.connection(ws):
            await broadcaster.listen(ws)   # блокирует до закрытия
    """

    def __init__(self) -> None:
        self._connections: set[web.WebSocketResponse] = set()
        self._lock = asyncio.Lock()

    @property
    def connections_count(self) -> int:
        return len(self._connections)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        """aiohttp handler для /ws эндпоинта."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        async with self._register(ws):
            logger.info("WebSocket подключён", remote=request.remote, total=self.connections_count)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Клиент может слать ping
                    if msg.data == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        logger.info("WebSocket отключён", remote=request.remote, total=self.connections_count)
        return ws

    async def broadcast(self, msg: dict) -> None:
        """Разослать сообщение всем подключённым клиентам."""
        if not self._connections:
            return
        payload = json.dumps(msg)
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._connections):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    # ---- внутреннее ----

    class _RegisterCtx:
        """Async context manager: добавляет/удаляет WS из множества."""
        def __init__(self, broadcaster: "WebSocketBroadcaster", ws: web.WebSocketResponse) -> None:
            self._b = broadcaster
            self._ws = ws

        async def __aenter__(self):
            async with self._b._lock:
                self._b._connections.add(self._ws)
            return self._ws

        async def __aexit__(self, *_):
            async with self._b._lock:
                self._b._connections.discard(self._ws)

    def _register(self, ws: web.WebSocketResponse) -> "_RegisterCtx":
        return WebSocketBroadcaster._RegisterCtx(self, ws)
