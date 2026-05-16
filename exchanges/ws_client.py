"""Базовый WebSocket клиент: reconnect, heartbeat, stale detection."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import structlog
import websockets
import websockets.exceptions

logger = structlog.get_logger(__name__)

_MAX_BACKOFF = 60.0
_STALE_TIMEOUT = 35.0  # нет сообщений N секунд → форсируем переподключение

# websockets 14+ использует новый тип соединения
WsConnection = Any


class BaseWsClient(ABC):
    """
    Управляет жизненным циклом WebSocket соединения.
    Subclass реализует: _on_connect, _on_message, _send_ping.

    Совместим с websockets >= 10 и >= 14 (новый asyncio API).
    """

    def __init__(
        self,
        url: str,
        ping_interval: float = 20.0,
        additional_headers: dict[str, str] | None = None,
        proxy_url: str = "",
    ) -> None:
        self._url = url
        self._ping_interval = ping_interval
        self._additional_headers = additional_headers or {}
        self._proxy_url = proxy_url
        self._ws: WsConnection = None
        self._stopped = False
        self._last_msg_at = 0.0
        self._reconnect_count = 0
        self._connect_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._ping_sent_at: float = 0.0
        self._latency_cb: Callable[[int], None] | None = None  # принимает мкс

    def set_latency_callback(self, cb: Callable[[int], None]) -> None:
        self._latency_cb = cb

    def _report_pong(self) -> None:
        """Вызывать при получении pong — записывает RTT/2 как one-way latency."""
        if self._ping_sent_at > 0 and self._latency_cb:
            rtt_us = int((time.perf_counter() - self._ping_sent_at) * 1_000_000)
            self._latency_cb(rtt_us // 2)
            self._ping_sent_at = 0.0

    async def start(self) -> None:
        self._stopped = False
        self._connect_task = asyncio.create_task(self._connect_loop(), name=f"ws:{self._url}")

    async def stop(self) -> None:
        self._stopped = True
        if self._connect_task:
            self._connect_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def send_raw(self, msg: str) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(msg)
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        # websockets 16: атрибут state, старые — closed
        state = getattr(self._ws, "state", None)
        if state is not None:
            import websockets.connection
            return state == websockets.connection.State.OPEN
        return not getattr(self._ws, "closed", True)

    @property
    def last_message_ms(self) -> int:
        return int(self._last_msg_at * 1000)

    # ---- внутренний цикл ----

    async def _connect_loop(self) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                connect_kwargs: dict[str, Any] = {
                    "ping_interval": None,   # ping управляется вручную
                    "open_timeout":  10,
                    "max_size":      10 * 1024 * 1024,  # 10 MB
                }
                if self._additional_headers:
                    connect_kwargs["additional_headers"] = self._additional_headers

                # close_timeout был убран в websockets 14+
                ws_version = tuple(int(x) for x in websockets.__version__.split(".")[:2])
                if ws_version < (14, 0):
                    connect_kwargs["close_timeout"] = 5

                # SOCKS5 прокси (ротирующий IP — каждое соединение новый IP)
                if self._proxy_url:
                    from python_socks.async_.asyncio import Proxy as _SocksProxy  # noqa: PLC0415
                    from urllib.parse import urlparse as _urlparse  # noqa: PLC0415
                    _p = _urlparse(self._url)
                    _port = _p.port or (443 if _p.scheme == "wss" else 80)
                    _proxy = _SocksProxy.from_url(self._proxy_url, rdns=True)
                    connect_kwargs["sock"] = await _proxy.connect(
                        dest_host=_p.hostname, dest_port=_port
                    )

                async with websockets.connect(self._url, **connect_kwargs) as ws:
                    self._ws = ws
                    self._ping_sent_at = 0.0  # сброс при каждом новом соединении
                    self._last_msg_at = time.monotonic()
                    self._reconnect_count += 1 if backoff > 1.0 else 0
                    backoff = 1.0
                    logger.info("WS подключён", url=self._url)

                    await self._on_connect(ws)

                    ping_task  = asyncio.create_task(self._ping_loop(ws))
                    stale_task = asyncio.create_task(self._stale_watchdog(ws))
                    try:
                        await self._recv_loop(ws)
                    finally:
                        ping_task.cancel()
                        stale_task.cancel()

            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError,
                asyncio.TimeoutError,
            ) as exc:
                if self._stopped:
                    break
                logger.warning("WS разрыв, переподключение", error=str(exc), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
            except Exception:
                if self._stopped:
                    break
                logger.exception("Неожиданная ошибка WS", url=self._url)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _recv_loop(self, ws: WsConnection) -> None:
        async for raw in ws:
            self._last_msg_at = time.monotonic()
            await self._on_message(raw)

    async def _ping_loop(self, ws: WsConnection) -> None:
        while not self._stopped:
            await asyncio.sleep(self._ping_interval)
            try:
                # Нативный WS ping — работает на всех биржах, точнее application-level
                if self._latency_cb:
                    t0 = time.perf_counter()
                    pong_waiter = await ws.ping()
                    await pong_waiter
                    rtt_us = int((time.perf_counter() - t0) * 1_000_000)
                    self._latency_cb(rtt_us // 2)
                # Application-level keepalive (Bybit требует, Binance — нет)
                await self._send_ping(ws)
            except Exception:
                break

    async def _stale_watchdog(self, ws: WsConnection) -> None:
        while not self._stopped:
            await asyncio.sleep(_STALE_TIMEOUT)
            elapsed = time.monotonic() - self._last_msg_at
            if elapsed > _STALE_TIMEOUT:
                logger.warning("WS stale — форсируем переподключение", url=self._url, elapsed=elapsed)
                try:
                    await ws.close()
                except Exception:
                    pass
                break

    # ---- subclass hooks ----

    @abstractmethod
    async def _on_connect(self, ws: WsConnection) -> None: ...

    @abstractmethod
    async def _on_message(self, raw: str | bytes) -> None: ...

    @abstractmethod
    async def _send_ping(self, ws: WsConnection) -> None: ...
