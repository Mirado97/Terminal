"""
run.py — арбитраж фьючерсного спреда Bybit Perp ↔ MEXC Futures.

Стратегия: x1 плечо, только USDT, лонг/шорт одновременно.
Порог прибыли: Bybit 5.5 bps + MEXC 6 bps = 11.5 bps суммарно.
Режим: мониторинг спредов (торговля подключается отдельно).
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
from exchanges.mexc.futures_adapter import MexcFuturesAdapter
from monitoring.latency import LatencyTracker
from orderbook.engine import OrderBookEngine
from risk.engine import RiskEngine
from risk.inventory import InventoryManager
from risk.limits import RiskLimits
from credentials.manager import ExchangeCredentials
from spread.calculator import SpreadCalculator
from spread.detector import SpreadDetector
from spread.fees import FeeSchedule, FeeTable
from watchdog.orchestrator import WatchdogOrchestrator

_spread_map: dict[tuple, dict] = {}

_FALLBACK_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT",
    "ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT","UNIUSDT","LTCUSDT",
    "ATOMUSDT","NEARUSDT","INJUSDT","ARBUSDT","OPUSDT","APTUSDT",
    "SUIUSDT","TONUSDT","PEPEUSDT","SHIBUSDT","BONKUSDT","WIFUSDT",
    "TAOUSDT","JUPUSDT","ENAUSDT","TIAUSDT","STRKUSDT","PYTHUSDT",
    "NOTUSDT","LDOUSDT","RUNEUSDT","FETUSDT","RENDERUSDT","SEIUSDT",
    "IMXUSDT","GALAUSDT","SANDUSDT","AXSUSDT","FLOKIUSDT","POLUSDT",
]

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


async def fetch_futures_symbols(n: int = 500) -> list[str]:
    """Топ N USDT-пар по объёму, доступных на Bybit Linear И MEXC Futures."""
    headers = {"User-Agent": _UA}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers=headers,
        ) as session:
            # Bybit linear (перп)
            async with session.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear"},
            ) as r:
                bybit_data = await r.json(content_type=None)
            bybit_symbols = {
                t["symbol"]
                for t in bybit_data.get("result", {}).get("list", [])
                if t["symbol"].endswith("USDT")
            }

            # MEXC Futures (contract)
            async with session.get("https://contract.mexc.com/api/v1/contract/detail") as r:
                mexc_data = await r.json(content_type=None)

        mexc_symbols: dict[str, float] = {}
        for c in mexc_data.get("data", []):
            sym_raw = c.get("symbol", "")           # BTC_USDT
            sym = sym_raw.replace("_", "")          # BTCUSDT
            if sym.endswith("USDT") and sym in bybit_symbols:
                vol = float(c.get("volumeOf24h", 0) or 0)
                mexc_symbols[sym] = vol

        symbols = sorted(mexc_symbols, key=lambda s: mexc_symbols[s], reverse=True)[:n]
        print(f"  Загружено {len(symbols)} фьюч. пар (Bybit Linear ∩ MEXC Futures)")
        return symbols

    except Exception as exc:
        print(f"  Не удалось загрузить пары ({exc}), используем fallback")
        return _FALLBACK_SYMBOLS[:n]


async def run() -> None:
    setup_logging(level="INFO", json_output=False)

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

    symbols = await fetch_futures_symbols(500)

    # ── Exchange адаптеры ─────────────────────────────────────────
    bybit_cfg = {"testnet": False, "rate_limit": {"requests_per_second": 10, "orders_per_second": 5}}
    bybit = BybitAdapter(config=bybit_cfg, credentials=bybit_creds)
    mexc  = MexcFuturesAdapter(credentials=mexc_creds)

    # ── OrderBook Engine ──────────────────────────────────────────
    ob_engine = OrderBookEngine(validate_checksum=False)
    bybit.on_orderbook(ob_engine.handle)
    mexc.on_orderbook(ob_engine.handle)

    # ── Spread калькулятор: перп комиссии (Bybit 5.5 + MEXC 6 = 11.5 bps) ──
    fee_table = FeeTable(overrides={
        (Exchange.MEXC,  MarketType.PERPETUAL): FeeSchedule(maker_bps=0.8, taker_bps=3.2),
        (Exchange.BYBIT, MarketType.PERPETUAL): FeeSchedule(maker_bps=3.24, taker_bps=9.0),
    })
    calculator = SpreadCalculator(fee_table=fee_table, latency_us=10_000)
    detector = SpreadDetector(
        engine                    = ob_engine,
        calculator                = calculator,
        min_executable_spread_bps = -50.0,
        min_size_usdt             = 10.0,
        max_raw_spread_bps        = 500.0,
        max_position_usdt         = 1_000.0,
    )

    async def on_opportunity(opp) -> None:
        key = (opp.symbol, opp.buy_exchange.value, opp.sell_exchange.value)
        _spread_map[key] = {
            "symbol":                opp.symbol,
            "buy_exchange":          opp.buy_exchange.value,
            "sell_exchange":         opp.sell_exchange.value,
            "raw_spread_bps":        round(opp.raw_spread_bps, 2),
            "executable_spread_bps": round(opp.executable_spread_bps, 2),
            "fee_cost_bps":          12.2,
            "executed":              False,
            "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    detector.on_opportunity(on_opportunity)

    async def _periodic_scan() -> None:
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
                            "fee_cost_bps":          12.2,
                            "executed":              False,
                            "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }

    # ── Risk / Inventory ──────────────────────────────────────────
    inventory   = InventoryManager(initial_capital_usdt=10_000.0)
    risk_engine = RiskEngine(limits=RiskLimits(), inventory=inventory)

    # ── Latency ───────────────────────────────────────────────────
    latency = LatencyTracker()
    bybit.set_latency_tracker(latency, "bybit")
    mexc.set_latency_tracker(latency, "mexc_fut")

    # ── API Server ────────────────────────────────────────────────
    orchestrator = WatchdogOrchestrator()
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
        data = sorted(_spread_map.values(), key=lambda x: x["executable_spread_bps"], reverse=True)
        return web.json_response({"spreads": data, "total": len(data)})

    server._app.router.add_get("/api/spreads/live", _handle_spreads)

    # ── Старт ─────────────────────────────────────────────────────
    await bybit.connect()
    await mexc.connect()
    await server.start()

    for symbol in symbols:
        await bybit.subscribe_orderbook(symbol, MarketType.PERPETUAL)
        await mexc.subscribe_orderbook(symbol, MarketType.PERPETUAL)

    asyncio.create_task(_periodic_scan())

    print("\n" + "=" * 60)
    print("  Arbitrage Terminal — FUTURES x1 LEVERAGE")
    print(f"  Bybit Linear + MEXC Futures | Символов: {len(symbols)}")
    print(f"  Порог прибыли: >12.2 bps (Bybit 9.0 + MEXC 3.2)")
    print("  http://localhost:8080/api/spreads/live")
    print("  Ctrl+C для остановки")
    print("=" * 60 + "\n")

    try:
        while True:
            await asyncio.sleep(10)
            s = detector.stats
            books = ob_engine.synced_books()
            synced_pairs = len({b.symbol for b in books if b.best_bid > 0})
            profitable = sum(1 for v in _spread_map.values() if v["executable_spread_bps"] > 0)
            print(
                f"  Пар: {synced_pairs} | Сканов: {s.scans} | "
                f"Прибыльных спредов: {profitable}"
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await server.stop()
        await bybit.disconnect()
        await mexc.disconnect()
        print("\nОстановлен.")


if __name__ == "__main__":
    asyncio.run(run())
