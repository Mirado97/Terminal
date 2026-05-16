"""
run.py — запуск арбитражного терминала с реальными биржами.

Подключается к:
  - Bybit  mainnet (публичный WS)
  - MEXC   mainnet (WS bookTicker, без прокси — запуск с VPS)

Режим: наблюдение + детектор спредов.
Торговля отключена — включить когда будет депозит на биржах.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiohttp

from api.server import TerminalApiServer
from core.logging import setup_logging
from core.models import Exchange, MarketType
from exchanges.bybit.adapter import BybitAdapter
from exchanges.mexc.adapter import MexcAdapter
from monitoring.latency import LatencyTracker
from orderbook.engine import OrderBookEngine
from risk.engine import RiskEngine
from risk.inventory import InventoryManager
from risk.limits import RiskLimits
from credentials.manager import ExchangeCredentials
from spread.calculator import SpreadCalculator
from spread.detector import SpreadDetector
from spread.fees import FeeTable
from watchdog.orchestrator import WatchdogOrchestrator

# Текущий спред по каждой паре (symbol, buy, sell) → последнее значение
_spread_map: dict[tuple, dict] = {}


_FALLBACK_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT",
    "DOTUSDT","LTCUSDT","LINKUSDT","UNIUSDT","ATOMUSDT","ETCUSDT",
    "XLMUSDT","ALGOUSDT","ICPUSDT","FILUSDT","VETUSDT","TRXUSDT","HBARUSDT",
    "NEARUSDT","SANDUSDT","MANAUSDT","AXSUSDT","GALAUSDT","APEUSDT",
    "FTMUSDT","EGLDUSDT","THETAUSDT","AAVEUSDT","GRTUSDT","MKRUSDT","SNXUSDT",
    "COMPUSDT","CRVUSDT","SUSHIUSDT","YFIUSDT","1INCHUSDT","ENJUSDT","CHZUSDT",
    "ZILUSDT","QNTUSDT","KAVAUSDT","WAVESUSDT","ZECUSDT","DASHUSDT",
    "NEOUSDT","IOTAUSDT","KSMUSDT","RUNEUSDT","KLAYUSDT","ONEUSDT",
    "SKLUSDT","STORJUSDT","ANKRUSDT","CELRUSDT","OCEANUSDT","FETUSDT",
    "AGIXUSDT","RNDRUSDT","INJUSDT","SUIUSDT","ARBUSDT","OPUSDT","APTUSDT",
    "SEIUSDT","TIAUSDT","WLDUSDT","JUPUSDT","STRKUSDT","PYTHUSDT",
    "ENAUSDT","SAGAUSDT","TAOUSDT","NOTUSDT","TONUSDT","BNBUSDT",
    "WIFUSDT","FLOKIUSDT","PEPEUSDT","SHIBUSDT","BONKUSDT",
    "LDOUSDT","STXUSDT","IMXUSDT","POLUSDT",
]

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


async def fetch_top_symbols(n: int = 100) -> list[str]:
    """Топ N USDT-пар по объёму MEXC, доступных также на Bybit."""
    headers = {"User-Agent": _UA}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers=headers,
        ) as session:
            async with session.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "spot"},
            ) as r:
                bybit_data = await r.json(content_type=None)
            bybit_symbols = {
                t["symbol"]
                for t in bybit_data.get("result", {}).get("list", [])
                if t["symbol"].endswith("USDT")
            }

            async with session.get("https://api.mexc.com/api/v3/ticker/24hr") as r:
                mexc_tickers: list[dict] = await r.json(content_type=None)

        mexc_pairs: list[tuple[str, float]] = []
        for t in mexc_tickers:
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym in bybit_symbols:
                vol = float(t.get("quoteVolume", 0) or 0)
                mexc_pairs.append((sym, vol))

        mexc_pairs.sort(key=lambda x: x[1], reverse=True)
        symbols = [sym for sym, _ in mexc_pairs[:n]]
        print(f"  Загружено {len(symbols)} пар (топ {n} по объёму MEXC∩Bybit)")
        return symbols

    except Exception as exc:
        print(f"  Не удалось загрузить пары ({exc}), используем fallback-список")
        return _FALLBACK_SYMBOLS[:n]


async def run() -> None:
    setup_logging(level="INFO", json_output=False)

    # ── Credentials ───────────────────────────────────────────────
    from dotenv import load_dotenv
    import os
    load_dotenv(Path(".env"))

    bybit_creds = ExchangeCredentials(
        api_key    = os.environ.get("BYBIT_API_KEY", ""),
        api_secret = os.environ.get("BYBIT_API_SECRET", ""),
    )
    mexc_creds = ExchangeCredentials(
        api_key    = os.environ.get("MEXC_API_KEY", ""),
        api_secret = os.environ.get("MEXC_API_SECRET", ""),
    )

    symbols = await fetch_top_symbols(200)

    # ── Exchange адаптеры ─────────────────────────────────────────
    bybit_cfg = {"testnet": False, "rate_limit": {"requests_per_second": 10, "orders_per_second": 5}}
    bybit = BybitAdapter(config=bybit_cfg, credentials=bybit_creds)
    mexc  = MexcAdapter(credentials=mexc_creds)

    # ── OrderBook Engine ──────────────────────────────────────────
    ob_engine = OrderBookEngine(validate_checksum=False)

    bybit.on_orderbook(ob_engine.handle)
    mexc.on_orderbook(ob_engine.handle)

    # ── Spread Detection ──────────────────────────────────────────
    fee_table  = FeeTable()
    calculator = SpreadCalculator(fee_table=fee_table, latency_us=10_000)
    detector   = SpreadDetector(
        engine                     = ob_engine,
        calculator                 = calculator,
        min_executable_spread_bps  = -100.0,  # мониторинг: показываем все спреды
        min_size_usdt              = 10.0,
        max_raw_spread_bps         = 500.0,
        max_position_usdt          = 1_000.0,
    )

    async def on_opportunity(opp) -> None:
        key = (opp.symbol, opp.buy_exchange.value, opp.sell_exchange.value)
        _spread_map[key] = {
            "symbol":                opp.symbol,
            "buy_exchange":          opp.buy_exchange.value,
            "sell_exchange":         opp.sell_exchange.value,
            "raw_spread_bps":        round(opp.raw_spread_bps, 2),
            "executable_spread_bps": round(opp.executable_spread_bps, 2),
            "executed":              False,
            "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    detector.on_opportunity(on_opportunity)

    async def _periodic_scan() -> None:
        """Принудительное обновление spread_map каждые 3 секунды для всех синхронизированных пар."""
        while True:
            await asyncio.sleep(3)
            books = ob_engine.synced_books()
            by_symbol: dict[str, list] = {}
            for b in books:
                by_symbol.setdefault(b.symbol, []).append(b)

            for symbol, book_list in by_symbol.items():
                if len(book_list) < 2:
                    continue
                for buy_book in book_list:
                    for sell_book in book_list:
                        if buy_book is sell_book:
                            continue
                        result = calculator.compute(buy_book, sell_book, 1_000.0)
                        if result is None or result.size_usdt < 10 or result.raw_spread_bps > 500:
                            continue
                        key = (symbol, buy_book.exchange.value, sell_book.exchange.value)
                        _spread_map[key] = {
                            "symbol":                symbol,
                            "buy_exchange":          buy_book.exchange.value,
                            "sell_exchange":         sell_book.exchange.value,
                            "raw_spread_bps":        round(result.raw_spread_bps, 2),
                            "executable_spread_bps": round(result.executable_spread_bps, 2),
                            "executed":              False,
                            "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }

    # ── Risk / Inventory ──────────────────────────────────────────
    inventory   = InventoryManager(initial_capital_usdt=10_000.0)
    risk_engine = RiskEngine(limits=RiskLimits(), inventory=inventory)

    # ── Latency Tracker ───────────────────────────────────────────
    latency = LatencyTracker()
    bybit.set_latency_tracker(latency, "bybit")
    mexc.set_latency_tracker(latency, "mexc")

    # ── Orchestrator ──────────────────────────────────────────────
    orchestrator = WatchdogOrchestrator()

    # ── API Server ────────────────────────────────────────────────
    server = TerminalApiServer(
        inventory       = inventory,
        risk_engine     = risk_engine,
        orchestrator    = orchestrator,
        latency_tracker = latency,
        host            = "0.0.0.0",
        port            = 8080,
        push_interval_s = 1.0,
    )

    from aiohttp import web
    async def _handle_spreads(request: web.Request) -> web.Response:
        data = sorted(
            _spread_map.values(),
            key=lambda x: x["executable_spread_bps"],
            reverse=True,
        )
        return web.json_response({"spreads": data, "total": len(data)})

    server._app.router.add_get("/api/spreads/live", _handle_spreads)

    # ── Старт ─────────────────────────────────────────────────────
    await bybit.connect()
    await mexc.connect()
    await server.start()

    # Подписываемся на стаканы
    for symbol in symbols:
        await bybit.subscribe_orderbook(symbol, MarketType.SPOT)
        await mexc.subscribe_orderbook(symbol, MarketType.SPOT)

    asyncio.create_task(_periodic_scan())

    print("\n" + "=" * 55)
    print("  Arbitrage Terminal — LIVE")
    print(f"  Биржи: Bybit + MEXC | Символов: {len(symbols)}")
    print("  http://localhost:8080/api/status")
    print("  http://localhost:8080/api/spreads/live")
    print("  Ctrl+C для остановки")
    print("=" * 55 + "\n")

    try:
        while True:
            await asyncio.sleep(10)
            s = detector.stats
            books = ob_engine.synced_books()
            print(
                f"  Стаканы: {len(books)} синхронизировано | "
                f"Сканов: {s.scans} | "
                f"Возможностей: {s.opportunities_found}"
            )
            for b in sorted(books, key=lambda x: (x.symbol, x.exchange.value)):
                if b.best_bid > 0:
                    print(f"    {b.exchange.value:6} {b.symbol}: bid={b.best_bid:.2f}  ask={b.best_ask:.2f}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await server.stop()
        await bybit.disconnect()
        await mexc.disconnect()
        print("\nОстановлен.")


if __name__ == "__main__":
    asyncio.run(run())
