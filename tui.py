"""
tui.py — Terminal UI для арбитражного бота.
Запуск: python3 tui.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
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

from run import _BLACKLIST  # noqa: E402

# ── Shared state ──────────────────────────────────────────────────────────
_spread_map: dict[tuple, dict] = {}
_stats = {
    "pairs":           0,
    "profitable":      0,
    "best_spread":     0.0,
    "best_symbol":     "—",
    "bybit_connected": False,
    "mexc_connected":  False,
    "start_time":      time.time(),
}

FEE_BPS = 12.2

# ── Virtual trading ───────────────────────────────────────────────────────
_trades: list[dict] = []
_positions: dict[tuple, dict] = {}
_portfolio = {"balance": 100.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0}
_trades_page = 0

VIRTUAL_SIZE_USDT = 50.0   # размер позиции на сторону (лонг $50 + шорт $50 = $100)
ENTRY_THRESHOLD   = 5.0    # bps executable — порог входа
EXIT_THRESHOLD    = -2.0   # bps executable — порог выхода
MAX_POSITIONS     = 3
TRADES_PER_PAGE   = 10


# ── UI builder ────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _stats["start_time"])
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _conn(ok: bool) -> str:
    return "[green]●[/]" if ok else "[red]●[/]"


def _pnl_str(val: float, decimals: int = 4) -> str:
    fmt = f".{decimals}f"
    if val >= 0:
        return f"[green]+{val:{fmt}}[/]"
    return f"[red]{val:{fmt}}[/]"


def build_ui() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header",    size=3),
        Layout(name="positions", size=7),
        Layout(name="history",   size=14),
        Layout(name="spreads",   size=9),
        Layout(name="footer",    size=1),
    )

    # ── Header ──────────────────────────────────────────────────────────
    bybit_s = _conn(_stats["bybit_connected"])
    mexc_s  = _conn(_stats["mexc_connected"])
    best    = f"[green]+{_stats['best_spread']:.2f}bps[/] {_stats['best_symbol']}" \
              if _stats["best_spread"] > 0 else "[dim]нет[/]"
    rpnl = _portfolio["realized_pnl"]
    upnl = _portfolio["unrealized_pnl"]
    bal  = _portfolio["balance"]

    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold cyan]◈ ARBITRAGE TERMINAL[/]  Bybit: {bybit_s}  MEXC Futures: {mexc_s}  │  "
            f"Пар: [yellow]{_stats['pairs']}[/]  Прибыльных: [green]{_stats['profitable']}[/]  "
            f"Лучший: {best}  Uptime: [dim]{_uptime()}[/]  │  "
            f"Виртуал: [yellow]${bal:.2f}[/]  R:{_pnl_str(rpnl, 4)}  U:{_pnl_str(upnl, 4)}"
        ),
        style="on grey7",
    ))

    # ── Открытые позиции (полная ширина) ─────────────────────────────────
    pos_tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
    pos_tbl.add_column("Символ",       style="cyan",    width=12)
    pos_tbl.add_column("Лонг",         width=5)
    pos_tbl.add_column("Цена лонг",    justify="right", width=12)
    pos_tbl.add_column("Шорт",         width=5)
    pos_tbl.add_column("Цена шорт",    justify="right", width=12)
    pos_tbl.add_column("Вход bps",     justify="right", width=9)
    pos_tbl.add_column("Сейчас bps",   justify="right", width=11)
    pos_tbl.add_column("Держим",       width=8)
    pos_tbl.add_column("Unreal $",     justify="right", width=10)

    for key, pos in _positions.items():
        current        = _spread_map.get(key)
        cur_ep         = current["executable_spread_bps"] if current else pos["entry_executable_bps"]
        cur_buy_price  = current.get("buy_price",  pos.get("entry_buy_price",  0.0)) if current else pos.get("entry_buy_price", 0.0)
        cur_sell_price = current.get("sell_price", pos.get("entry_sell_price", 0.0)) if current else pos.get("entry_sell_price", 0.0)
        unreal  = VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - cur_ep) / 10_000
        hold_s  = int(time.time() - pos["opened_at"])
        hold_str = f"{hold_s//60}м{hold_s%60:02d}с"

        if cur_ep > ENTRY_THRESHOLD:
            ep_now = f"[green]{cur_ep:+.2f}[/]"
        elif cur_ep > 0:
            ep_now = f"[yellow]{cur_ep:+.2f}[/]"
        else:
            ep_now = f"[red]{cur_ep:+.2f}[/]"

        pos_tbl.add_row(
            pos["symbol"],
            pos["buy_exchange"].upper()[:5],
            f"{cur_buy_price:.4f}" if cur_buy_price else "—",
            pos["sell_exchange"].upper()[:5],
            f"{cur_sell_price:.4f}" if cur_sell_price else "—",
            f"+{pos['entry_executable_bps']:.2f}",
            ep_now,
            hold_str,
            _pnl_str(unreal, 4),
        )

    layout["positions"].update(Panel(
        pos_tbl,
        title=f"[bold]Открытые позиции[/]  {len(_positions)}/{MAX_POSITIONS}  │  порог входа >{ENTRY_THRESHOLD:.0f}bps  выход <{EXIT_THRESHOLD:.0f}bps  ${VIRTUAL_SIZE_USDT:.0f}/сторону",
    ))

    # ── История сделок (полная ширина) ───────────────────────────────────
    hist_tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
    hist_tbl.add_column("Символ",    style="cyan",    width=12)
    hist_tbl.add_column("Вход bps",  justify="right", width=9)
    hist_tbl.add_column("Выход bps", justify="right", width=10)
    hist_tbl.add_column("P&L bps",   justify="right", width=9)
    hist_tbl.add_column("P&L $",     justify="right", width=9)
    hist_tbl.add_column("Держал",    width=8)
    hist_tbl.add_column("Закрыт",    width=8)

    trades_rev = list(reversed(_trades))
    n_pages    = max(1, (len(trades_rev) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
    page       = _trades_page % n_pages
    page_slice = trades_rev[page * TRADES_PER_PAGE : (page + 1) * TRADES_PER_PAGE]

    for t in page_slice:
        hist_tbl.add_row(
            t["symbol"],
            f"+{t['entry_bps']:.2f}",
            f"{t['exit_bps']:+.2f}",
            _pnl_str(t["pnl_bps"], 2),
            _pnl_str(t["pnl_usdt"], 4),
            t["hold"],
            t["time"],
        )

    layout["history"].update(Panel(
        hist_tbl,
        title="[bold]История виртуальных сделок[/]",
        subtitle=f"[dim]стр. {page + 1}/{n_pages}  ← → для переключения[/]",
    ))

    # ── Bybit Linear ↔ MEXC Futures (топ 5, полная ширина) ──────────────
    tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
    tbl.add_column("Символ",    style="cyan",    width=14)
    tbl.add_column("Лонг",      width=7)
    tbl.add_column("Шорт",      width=7)
    tbl.add_column("Спред",     justify="right", width=9)
    tbl.add_column("Прибыль",   justify="right", width=11)
    tbl.add_column("Обновлено", width=10)

    rows = sorted(
        (v for v in _spread_map.values() if v["symbol"] not in _BLACKLIST),
        key=lambda x: x["executable_spread_bps"],
        reverse=True,
    )[:5]

    for row in rows:
        ep  = row["executable_spread_bps"]
        raw = row["raw_spread_bps"]
        if ep > 0:
            ep_str  = f"[bold green]+{ep:.2f} bps[/]"
            raw_str = f"[green]{raw:+.2f}[/]"
        elif ep > -5:
            ep_str  = f"[yellow]{ep:.2f} bps[/]"
            raw_str = f"[yellow]{raw:+.2f}[/]"
        else:
            ep_str  = f"[dim]{ep:.2f} bps[/]"
            raw_str = f"[dim]{raw:+.2f}[/]"
        tbl.add_row(
            row["symbol"],
            row["buy_exchange"].upper()[:5],
            row["sell_exchange"].upper()[:5],
            raw_str, ep_str,
            row["created_at"][11:19],
        )

    layout["spreads"].update(Panel(
        tbl,
        title="[bold]Bybit Linear  ↔  MEXC Futures  │  x1 leverage[/]",
        subtitle=f"[dim]порог прибыли >{FEE_BPS} bps  │  топ 5 из {len(_spread_map)}[/]",
    ))

    # ── Footer ───────────────────────────────────────────────────────────
    layout["footer"].update(
        Text.from_markup(
            "  [dim]q / Ctrl+C — выход   │  "
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

    # Мгновенный обработчик каждого тикера: пересчёт спреда + вход/выход виртуала
    async def _on_book_update(updated) -> None:
        if not updated.is_synced or updated.is_stale:
            return
        sym   = updated.symbol
        mtype = updated.market_type
        other_ex = Exchange.BYBIT if updated.exchange == Exchange.MEXC else Exchange.MEXC
        other    = ob_engine.get(other_ex, sym, mtype)
        if other is None or not other.is_synced or other.is_stale:
            return

        for buy_b, sell_b in [(updated, other), (other, updated)]:
            if buy_b.best_bid <= 0 or sell_b.best_ask <= 0:
                continue
            res = calculator.compute(buy_b, sell_b, 1_000.0)
            if res is None or res.size_usdt < 10 or res.raw_spread_bps > 500:
                continue
            key = (sym, buy_b.exchange.value, sell_b.exchange.value)
            ep  = round(res.executable_spread_bps, 2)
            _spread_map[key] = {
                "symbol":                sym,
                "buy_exchange":          buy_b.exchange.value,
                "sell_exchange":         sell_b.exchange.value,
                "raw_spread_bps":        round(res.raw_spread_bps, 2),
                "executable_spread_bps": ep,
                "fee_cost_bps":          FEE_BPS,
                "buy_price":             buy_b.best_ask,
                "sell_price":            sell_b.best_bid,
                "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
                "_ts":                   time.monotonic(),
            }

            # ── Виртуальная торговля: решение мгновенно на каждом тикере ──
            pos = _positions.get(key)

            if pos is not None:
                # Проверяем выход
                if ep < EXIT_THRESHOLD:
                    entry_ep = pos["entry_executable_bps"]
                    pnl_bps  = entry_ep - ep
                    pnl_usdt = round(VIRTUAL_SIZE_USDT * pnl_bps / 10_000, 4)
                    _portfolio["realized_pnl"] += pnl_usdt
                    _portfolio["balance"]      += pnl_usdt
                    hold_s = int(time.time() - pos["opened_at"])
                    _trades.append({
                        "symbol":    sym,
                        "entry_bps": round(entry_ep, 2),
                        "exit_bps":  ep,
                        "pnl_bps":   round(pnl_bps, 2),
                        "pnl_usdt":  pnl_usdt,
                        "hold":      f"{hold_s//60}м{hold_s%60:02d}с",
                        "time":      time.strftime("%H:%M:%S"),
                    })
                    if len(_trades) > 500:
                        _trades.pop(0)
                    del _positions[key]
            else:
                # Проверяем вход
                if ep > ENTRY_THRESHOLD and len(_positions) < MAX_POSITIONS and sym not in _BLACKLIST:
                    _positions[key] = {
                        "symbol":               sym,
                        "buy_exchange":         buy_b.exchange.value,
                        "sell_exchange":        sell_b.exchange.value,
                        "entry_executable_bps": ep,
                        "entry_buy_price":      buy_b.best_ask,
                        "entry_sell_price":     sell_b.best_bid,
                        "opened_at":            time.time(),
                    }

    ob_engine.on_update(_on_book_update)

    # Лёгкий цикл: обновляет статистику и чистит устаревшие записи из карты
    async def _stats_loop() -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            stale_keys = [k for k, v in _spread_map.items() if now - v.get("_ts", 0) > 10]
            for k in stale_keys:
                del _spread_map[k]

            profitable = [v for v in _spread_map.values() if v["executable_spread_bps"] > 0]
            _stats["profitable"]      = len(profitable)
            _stats["bybit_connected"] = bybit.health.ws_connected
            _stats["mexc_connected"]  = mexc.health.ws_connected
            if profitable:
                best = max(profitable, key=lambda x: x["executable_spread_bps"])
                _stats["best_spread"] = best["executable_spread_bps"]
                _stats["best_symbol"] = best["symbol"]

            # Нереализованный PnL
            _portfolio["unrealized_pnl"] = round(sum(
                VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - _spread_map[k]["executable_spread_bps"]) / 10_000
                for k, pos in _positions.items() if k in _spread_map
            ), 4)

    await bybit.connect()
    await mexc.connect()

    for sym in symbols:
        await bybit.subscribe_orderbook(sym, MarketType.PERPETUAL)
        await mexc.subscribe_orderbook(sym, MarketType.PERPETUAL)

    await _stats_loop()


# ── Keyboard listener (Linux) ─────────────────────────────────────────────

def _start_key_listener() -> None:
    """Фоновый поток: стрелки ← → переключают страницы истории."""
    try:
        import select
        import signal
        import termios
        import tty
    except ImportError:
        return  # Windows — пропускаем

    global _trades_page
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.buffer.read(1)
            if ch == b"\x1b":
                # escape-sequence: стрелка = ESC [ C/D
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    seq = sys.stdin.buffer.read(2)
                    n_pages = max(1, (len(_trades) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
                    if seq in (b"[C", b"OC"):   # → вправо
                        _trades_page = (_trades_page + 1) % n_pages
                    elif seq in (b"[D", b"OD"): # ← влево
                        _trades_page = (_trades_page - 1) % n_pages
            elif ch in (b"q", b"Q", b"\x03"):   # q / Ctrl+C
                os.kill(os.getpid(), signal.SIGINT)
                break
    except Exception:
        pass
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────

async def main() -> None:
    logging.disable(logging.WARNING)

    from core.logging import setup_logging
    setup_logging(level="ERROR", json_output=False)

    from run import fetch_futures_symbols
    symbols = await fetch_futures_symbols(500)
    _stats["pairs"] = len(symbols)

    bot_task = asyncio.create_task(bot_main(symbols))

    threading.Thread(target=_start_key_listener, daemon=True).start()

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
