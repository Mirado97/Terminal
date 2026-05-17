"""
tui.py — Terminal UI для арбитражного бота.
Запуск: python3 tui.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv(Path(".env"))

# ── Shared state ──────────────────────────────────────────────────────────
_spread_map: dict[tuple, dict] = {}
_stats = {
    "pairs":            0,
    "profitable":       0,
    "best_spread":      0.0,
    "best_symbol":      "—",
    "bybit_connected":  False,
    "mexc_connected":   False,
    "start_time":       time.time(),
}

FEE_BPS = 12.2


# ── UI builder ────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _stats["start_time"])
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _conn(ok: bool) -> str:
    return "[green]●[/]" if ok else "[red]●[/]"


def build_ui() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=1),
    )

    # ── Header ──────────────────────────────────────────────────────────
    bybit_s = _conn(_stats["bybit_connected"])
    mexc_s  = _conn(_stats["mexc_connected"])
    best    = f"[green]+{_stats['best_spread']:.2f}bps[/] {_stats['best_symbol']}" \
              if _stats["best_spread"] > 0 else "[dim]нет[/]"

    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold cyan]◈ ARBITRAGE TERMINAL[/]  Bybit: {bybit_s}  MEXC Futures: {mexc_s}  │  "
            f"Пар: [yellow]{_stats['pairs']}[/]  │  "
            f"Прибыльных: [green]{_stats['profitable']}[/]  │  "
            f"Лучший: {best}  │  "
            f"Uptime: [dim]{_uptime()}[/]"
        ),
        style="on grey7",
    ))

    # ── Spreads table ────────────────────────────────────────────────────
    tbl = Table(
        box=box.SIMPLE_HEAVY,
        header_style="bold white on grey15",
        expand=True,
        show_edge=False,
    )
    tbl.add_column("Символ",   style="cyan",   width=14)
    tbl.add_column("Лонг",     width=7)
    tbl.add_column("Шорт",     width=7)
    tbl.add_column("Спред",    justify="right", width=9)
    tbl.add_column("Прибыль",  justify="right", width=11)
    tbl.add_column("Обновлено",width=10)

    rows = sorted(
        _spread_map.values(),
        key=lambda x: x["executable_spread_bps"],
        reverse=True,
    )[:50]

    for row in rows:
        ep  = row["executable_spread_bps"]
        raw = row["raw_spread_bps"]

        if ep > 0:
            ep_str   = f"[bold green]+{ep:.2f} bps[/]"
            raw_str  = f"[green]{raw:+.2f}[/]"
        elif ep > -5:
            ep_str   = f"[yellow]{ep:.2f} bps[/]"
            raw_str  = f"[yellow]{raw:+.2f}[/]"
        else:
            ep_str   = f"[dim]{ep:.2f} bps[/]"
            raw_str  = f"[dim]{raw:+.2f}[/]"

        buy  = row["buy_exchange"].upper()[:5]
        sell = row["sell_exchange"].upper()[:5]
        ts   = row["created_at"][11:19]

        tbl.add_row(row["symbol"], buy, sell, raw_str, ep_str, ts)

    layout["main"].update(Panel(
        tbl,
        title="[bold]Bybit Linear  ↔  MEXC Futures  │  x1 leverage[/]",
        subtitle=f"[dim]порог прибыли >{FEE_BPS} bps  │  топ 50 из {len(_spread_map)}[/]",
    ))

    # ── Footer ───────────────────────────────────────────────────────────
    layout["footer"].update(
        Text.from_markup(
            "  [dim]q / Ctrl+C — выход   │   "
            "Зелёный = прибыльный спред после комиссий[/]"
        )
    )

    return layout


# ── Bot logic ─────────────────────────────────────────────────────────────

async def bot_main(symbols: list[str]) -> None:
    from core.models import Exchange, MarketType
    from exchanges.bybit.adapter import BybitAdapter
    from exchanges.mexc.futures_adapter import MexcFuturesAdapter
    from orderbook.engine import OrderBookEngine
    from spread.calculator import SpreadCalculator
    from spread.fees import FeeSchedule, FeeTable
    from credentials.manager import ExchangeCredentials

    bybit_creds = ExchangeCredentials(
        api_key    = os.environ.get("BYBIT_API_KEY", ""),
        api_secret = os.environ.get("BYBIT_API_SECRET", ""),
    )
    mexc_creds = ExchangeCredentials(
        api_key    = os.environ.get("MEXC_API_KEY", ""),
        api_secret = os.environ.get("MEXC_API_SECRET", ""),
    )

    bybit_cfg = {"testnet": False, "rate_limit": {"requests_per_second": 10, "orders_per_second": 5}}
    bybit = BybitAdapter(config=bybit_cfg, credentials=bybit_creds)
    mexc  = MexcFuturesAdapter(credentials=mexc_creds)

    ob_engine = OrderBookEngine(validate_checksum=False)
    bybit.on_orderbook(ob_engine.handle)
    mexc.on_orderbook(ob_engine.handle)

    fee_table = FeeTable(overrides={
        (Exchange.MEXC,  MarketType.PERPETUAL): FeeSchedule(maker_bps=0.8,  taker_bps=3.2),
        (Exchange.BYBIT, MarketType.PERPETUAL): FeeSchedule(maker_bps=3.24, taker_bps=9.0),
    })
    calculator = SpreadCalculator(fee_table=fee_table, latency_us=10_000)

    async def _scan() -> None:
        while True:
            await asyncio.sleep(2)
            books = ob_engine.synced_books()
            by_sym: dict[str, list] = {}
            for b in books:
                by_sym.setdefault(b.symbol, []).append(b)

            for sym, blist in by_sym.items():
                if len(blist) < 2:
                    continue
                for bb in blist:
                    for sb in blist:
                        if bb is sb:
                            continue
                        res = calculator.compute(bb, sb, 1_000.0)
                        if res is None or res.size_usdt < 10 or res.raw_spread_bps > 500:
                            continue
                        key = (sym, bb.exchange.value, sb.exchange.value)
                        _spread_map[key] = {
                            "symbol":                sym,
                            "buy_exchange":          bb.exchange.value,
                            "sell_exchange":         sb.exchange.value,
                            "raw_spread_bps":        round(res.raw_spread_bps, 2),
                            "executable_spread_bps": round(res.executable_spread_bps, 2),
                            "fee_cost_bps":          FEE_BPS,
                            "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }

            profitable = [v for v in _spread_map.values() if v["executable_spread_bps"] > 0]
            _stats["profitable"]      = len(profitable)
            _stats["bybit_connected"] = bybit.health.ws_connected
            _stats["mexc_connected"]  = mexc.health.ws_connected
            if profitable:
                best = max(profitable, key=lambda x: x["executable_spread_bps"])
                _stats["best_spread"] = best["executable_spread_bps"]
                _stats["best_symbol"] = best["symbol"]

    await bybit.connect()
    await mexc.connect()

    for sym in symbols:
        await bybit.subscribe_orderbook(sym, MarketType.PERPETUAL)
        await mexc.subscribe_orderbook(sym, MarketType.PERPETUAL)

    await _scan()


# ── Entry point ───────────────────────────────────────────────────────────

async def main() -> None:
    # Подавляем логи — в TUI они мешают
    logging.disable(logging.WARNING)

    from core.logging import setup_logging
    setup_logging(level="ERROR", json_output=False)

    from run import fetch_futures_symbols
    symbols = await fetch_futures_symbols(500)
    _stats["pairs"] = len(symbols)

    bot_task = asyncio.create_task(bot_main(symbols))

    console = Console()
    with Live(
        build_ui(),
        console=console,
        refresh_per_second=2,
        screen=True,
    ) as live:
        try:
            while True:
                live.update(build_ui())
                await asyncio.sleep(0.5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            bot_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
